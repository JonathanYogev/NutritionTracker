import json
import os
import logging
import requests
import boto3
from google.oauth2 import service_account
from googleapiclient.discovery import build
import google.generativeai as genai
from PIL import Image
import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import time
from botocore.exceptions import ClientError
from common.utils import get_secret, send_telegram_message

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB client for idempotency
dynamodb = boto3.resource('dynamodb')



# Externalize model names for a flexible, two-model strategy
GEMINI_VISION_MODEL_NAME = os.environ.get(
    'GEMINI_VISION_MODEL_NAME', 'gemini-2.5-pro')
GEMINI_PICKER_MODEL_NAME = os.environ.get(
    'GEMINI_PICKER_MODEL_NAME', 'gemini-2.5-flash')


def get_telegram_image(file_id, bot_token):
    """Downloads an image from Telegram."""
    url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
    response = requests.get(url)
    response.raise_for_status()
    file_path = response.json()['result']['file_path']
    image_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    image_response = requests.get(image_url)
    image_response.raise_for_status()
    return image_response.content


def analyze_image_with_gemini(image_bytes):
    """Analyzes an image with Google Gemini Vision API using the Python SDK."""
    img = Image.open(io.BytesIO(image_bytes))
    model = genai.GenerativeModel(GEMINI_VISION_MODEL_NAME)
    response = model.generate_content([
        "Identify the food items in the image. For each food item, provide an estimated weight in grams in parentheses. It is very important that the weight in parentheses comes directly after the food item it refers to. For example: '1 cooked chicken breast (170g)'; 'Broccoli florets (160g)'. Separate items with a semicolon (;). Do not include any introductory text in your response, only the list of items. If no food is identifiable in the image, respond with the single word: NO_FOOD.",
        img
    ])
    if not response.parts:
        raise ValueError("Gemini response is empty.")
    return response.text


def get_nutrition_data(food_item, fdc_api_key):
    """
    Gets nutrition data from FoodData Central by making separate calls for each data type
    and letting Gemini pick the best match from the combined results.
    """
    data_types = ["SR Legacy", "Foundation", "Survey (FNDDS)"]
    all_foods = []
    seen_fdc_ids = set()

    for data_type in data_types:
        try:
            # URL encode the data_type string to handle spaces, e.g., "Survey (FNDDS)"
            encoded_data_type = requests.utils.quote(data_type)
            search_url = f"https://api.nal.usda.gov/fdc/v1/foods/search?query={food_item}&dataType={encoded_data_type}&api_key={fdc_api_key}&pageSize=10"

            response = requests.get(search_url)
            response.raise_for_status()
            search_data_subset = response.json()

            if search_data_subset.get('foods'):
                for food in search_data_subset['foods']:
                    if food['fdcId'] not in seen_fdc_ids:
                        all_foods.append(food)
                        seen_fdc_ids.add(food['fdcId'])
        except requests.exceptions.RequestException as e:
            logger.warning(
                f"API call to FDC failed for dataType {data_type}. Error: {e}")
            # Continue to the next data type even if one fails
            continue

    if not all_foods:
        return None

    # The rest of the function now operates on the combined list of foods
    search_data = {'foods': all_foods}

    # Step 2: Format the options for Gemini
    options = []
    for i, food in enumerate(search_data['foods']):
        options.append(f"{i+1}. {food.get('description')}")

    options_string = "\n".join(options)

    # Step 3: Ask Gemini to pick the best match
    prompt = f"""You are a nutrition expert. The user ate '{food_item}'. I found the following items in the USDA database. Which one is the best and most accurate match? Please respond with only the number of the best option.\n\n{options_string}"""

    picker_model = genai.GenerativeModel(GEMINI_PICKER_MODEL_NAME)
    picker_response = picker_model.generate_content(prompt)

    best_option_number = 0
    try:
        best_option_number = int(picker_response.text.strip())
        selected_food = search_data['foods'][best_option_number - 1]
    except (ValueError, IndexError):
        # If Gemini's response is not a valid number, or out of range,
        # fallback to the first result.
        logger.warning(
            f"Gemini picker returned invalid response: '{picker_response.text.strip()}'. Defaulting to option 1.")
        best_option_number = 1
        selected_food = search_data['foods'][0]

    logger.info(
        f"Gemini picked option {best_option_number}, which is '{selected_food.get('description')}' (FDC ID: {selected_food.get('fdcId')}).")

    # Step 4: Return the selected food data from the search result
    return {'foods': [selected_food]}


def write_to_google_sheets(data, google_sheets_credentials, spreadsheet_id):
    """Writes data to Google Sheets."""
    creds_json = json.loads(google_sheets_credentials)
    creds = service_account.Credentials.from_service_account_info(creds_json)
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    body = {'values': [data]}
    result = sheet.values().append(
        spreadsheetId=spreadsheet_id,
        range='Meals!A:F',
        valueInputOption='USER_ENTERED',
        insertDataOption='INSERT_ROWS',
        body=body
    ).execute()
    return result


def _check_and_update_idempotency(idempotency_key, table):
    """
    Checks for and sets the idempotency key in DynamoDB.

    Returns:
        bool: True if processing should continue, False if it should be skipped.
    """
    try:
        response = table.get_item(Key={'idempotency_key': idempotency_key})
        item = response.get('Item')

        if item and item.get('status') == 'COMPLETED':
            logger.warning(f"Request {idempotency_key} already completed. Skipping.")
            return False

        if item and item.get('status') == 'PROCESSING':
            logger.warning(f"Request {idempotency_key} is already processing. Retrying.")
            return True

        # New request, mark as PROCESSING
        ttl_timestamp = int(time.time()) + 86400  # 24-hour TTL
        table.put_item(
            Item={'idempotency_key': idempotency_key, 'status': 'PROCESSING', 'ttl': ttl_timestamp},
            ConditionExpression='attribute_not_exists(idempotency_key)'
        )
        logger.info(f"Request {idempotency_key} marked as PROCESSING.")
        return True

    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            logger.warning(f"Request {idempotency_key} was created by a concurrent process. Skipping.")
            return False
        logger.error(f"DynamoDB error for request {idempotency_key}: {e}", exc_info=True)
        raise

def _get_food_items_from_image(image_bytes, chat_id, telegram_bot_token):
    """Analyzes image with Gemini and returns a list of food items."""
    try:
        food_items_text = analyze_image_with_gemini(image_bytes)
        logger.info(f"Gemini response: {food_items_text}")

        if food_items_text.strip() == 'NO_FOOD':
            logger.info(f"Gemini identified no food for chat_id: {chat_id}. Notifying user.")
            send_telegram_message(chat_id, "Sorry, I couldn't identify any food in the image. Please try another one.", telegram_bot_token)
            return None
        
        return [item.strip() for item in food_items_text.split(';')]

    except ValueError as e:
        logger.error(f"Gemini analysis failed: {e}", exc_info=True)
        send_telegram_message(chat_id, "Sorry, I couldn't analyze the image. It might be an unsupported format or corrupted.", telegram_bot_token)
        return None

def _calculate_meal_nutrition(food_items, fdc_api_key):
    """Calculates total nutrition for a list of food items."""
    totals = {'calories': 0, 'protein': 0, 'carbs': 0, 'fat': 0}

    for item in food_items:
        if not item:
            continue

        weight = 0
        food_name = item
        weight_match = re.search(r'\((\d+)g\)', item)
        if weight_match:
            weight = int(weight_match.group(1))
            food_name = item[:weight_match.start()].strip()

        food_name_parts = food_name.split()
        if food_name_parts and food_name_parts[0].isdigit():
            food_name = ' '.join(food_name_parts[1:])

        logger.info(f"Processing item: '{item}'. Cleaned food name: '{food_name}', weight: {weight}g")

        if weight == 0:
            logger.warning(f"Could not determine weight for item: {item}. Skipping.")
            continue

        nutrition_data = get_nutrition_data(food_name, fdc_api_key)
        if nutrition_data and nutrition_data.get('foods'):
            found_food = nutrition_data['foods'][0]
            logger.info(f"FDC found food: {found_food.get('description')}")

            for nutrient in found_food.get('foodNutrients', []):
                value_per_100g = nutrient.get('value', 0)
                value_per_gram = value_per_100g / 100
                nutrient_name = nutrient.get('nutrientName')
                
                if nutrient_name == 'Energy' and nutrient.get('unitName', '').upper() == 'KCAL':
                    totals['calories'] += value_per_gram * weight
                elif nutrient_name == 'Protein':
                    totals['protein'] += value_per_gram * weight
                elif nutrient_name == 'Carbohydrate, by difference':
                    totals['carbs'] += value_per_gram * weight
                elif nutrient_name == 'Total lipid (fat)':
                    totals['fat'] += value_per_gram * weight
    
    return totals

def _format_result_message(food_items, nutrition_totals):
    """Formats the final nutrition summary message for Telegram."""
    items_text = "\n".join([f"- {item}" for item in food_items if item])
    result_message = f"Nutrition for your meal:\n{items_text}\n\n"
    result_message += f"- Calories: {round(nutrition_totals['calories'], 2)}\n"
    result_message += f"- Protein: {round(nutrition_totals['protein'], 2)}g\n"
    result_message += f"- Carbs: {round(nutrition_totals['carbs'], 2)}g\n"
    result_message += f"- Fat: {round(nutrition_totals['fat'], 2)}g"
    return result_message

def process_meal_from_message(message_body, configs):
    """
    Processes a single meal from an SQS message body.
    Downloads image, analyzes nutrition, logs to sheets, and notifies user.
    """
    idempotency_key = message_body['idempotency_key']
    if not _check_and_update_idempotency(idempotency_key, configs['table']):
        return

    chat_id = message_body['chat_id']
    file_id = message_body['file_id']
    logger.info(f"Processing message for chat_id: {chat_id}, file_id: {file_id}")

    image_bytes = get_telegram_image(file_id, configs['telegram_bot_token'])
    
    food_items = _get_food_items_from_image(image_bytes, chat_id, configs['telegram_bot_token'])
    if food_items is None:
        # User has been notified, and we should stop processing.
        # Mark as complete to prevent retries for non-food images.
        configs['table'].update_item(
            Key={'idempotency_key': idempotency_key},
            UpdateExpression="set #status = :s",
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':s': 'COMPLETED'}
        )
        logger.info(f"NO FOOD detected - Request {idempotency_key} marked as COMPLETED.")
        return

    nutrition_totals = _calculate_meal_nutrition(food_items, configs['fdc_api_key'])

    now = datetime.now(ZoneInfo('Asia/Jerusalem')).strftime("%Y-%m-%d %H:%M:%S")
    sheet_data = [
        now,
        ', '.join(food_items),
        round(nutrition_totals['calories'], 2),
        round(nutrition_totals['protein'], 2),
        round(nutrition_totals['carbs'], 2),
        round(nutrition_totals['fat'], 2)
    ]
    write_to_google_sheets(sheet_data, configs['google_sheets_credentials'], configs['spreadsheet_id'])

    result_message = _format_result_message(food_items, nutrition_totals)
    send_telegram_message(chat_id, result_message, configs['telegram_bot_token'])

    # Mark as COMPLETED
    configs['table'].update_item(
        Key={'idempotency_key': idempotency_key},
        UpdateExpression="set #status = :s",
        ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues={':s': 'COMPLETED'}
    )
    logger.info(f"Request {idempotency_key} marked as COMPLETED.")


def lambda_handler(event, context):
    """Lambda function entry point for processing SQS messages."""
    # --- Performance Refactoring: Setup once per invocation ---
    try:
        table_name = os.environ['DYNAMODB_TABLE_NAME']
        configs = {
            'telegram_bot_token': get_secret('TELEGRAM_BOT_TOKEN_SSM_PATH'),
            'gemini_api_key': get_secret('GEMINI_API_KEY_SSM_PATH'),
            'fdc_api_key': get_secret('FDC_API_KEY_SSM_PATH'),
            'google_sheets_credentials': get_secret('GOOGLE_SHEETS_CREDENTIALS_SSM_PATH'),
            'spreadsheet_id': get_secret('SPREADSHEET_ID_SSM_PATH'),
            'table': dynamodb.Table(table_name)
        }
        genai.configure(api_key=configs['gemini_api_key'])
    except Exception as e:
        logger.critical(f"Failed to load initial configuration. Aborting invocation. Error: {e}", exc_info=True)
        # Re-raise to signal a catastrophic failure for this invocation
        raise

    for record in event['Records']:
        try:
            message_body = json.loads(record['body'])
            process_meal_from_message(message_body, configs)

        except Exception as e:
            logger.error(f"Error processing SQS message: {e}", exc_info=True)
            try:
                message_body = json.loads(record['body'])
                if 'chat_id' in message_body:
                    send_telegram_message(
                        message_body['chat_id'], 
                        "Sorry, there was an error processing your meal details.", 
                        configs.get('telegram_bot_token') # Use loaded config if available
                    )
            except Exception as notify_e:
                logger.error(f"Failed to notify user about the processing error. Error: {notify_e}")
            # Re-raise the exception to ensure the message is redriven to the DLQ
            raise
