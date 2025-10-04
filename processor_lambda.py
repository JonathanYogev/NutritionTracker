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
PROCESSED_MESSAGES_TABLE_NAME = "nutrition-tracker-messages"
processed_messages_table = dynamodb.Table(PROCESSED_MESSAGES_TABLE_NAME)


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


def process_meal_from_message(message_body):
    """
    Processes a single meal from an SQS message body.
    Downloads image, analyzes nutrition, logs to sheets, and notifies user.
    """
    # For security and freshness, fetch all secrets and config here, not globally
    telegram_bot_token = get_secret('TELEGRAM_BOT_TOKEN_SSM_PATH')
    gemini_api_key = get_secret('GEMINI_API_KEY_SSM_PATH')
    fdc_api_key = get_secret('FDC_API_KEY_SSM_PATH')
    google_sheets_credentials = get_secret(
        'GOOGLE_SHEETS_CREDENTIALS_SSM_PATH')
    spreadsheet_id = get_secret('SPREADSHEET_ID_SSM_PATH')

    # Configure the Gemini library once per invocation
    genai.configure(api_key=gemini_api_key)

    idempotency_key = message_body['idempotency_key']

    # Stateful Idempotency Check
    try:
        # Check if the message has already been processed or is in progress
        response = processed_messages_table.get_item(
            Key={'idempotency_key': idempotency_key})
        item = response.get('Item')

        if item and item.get('status') == 'COMPLETED':
            logger.warning(
                f"Request {idempotency_key} already completed. Skipping.")
            return

        if item and item.get('status') == 'PROCESSING':
            logger.warning(
                f"Request {idempotency_key} is already processing. Assuming previous attempt failed. Retrying.")
            # Proceed with execution

        else:
            # New request, mark as PROCESSING
            ttl_timestamp = int(time.time()) + 86400  # 24-hour TTL
            processed_messages_table.put_item(
                Item={
                    'idempotency_key': idempotency_key,
                    'status': 'PROCESSING',
                    'ttl': ttl_timestamp
                },
                ConditionExpression='attribute_not_exists(idempotency_key)'
            )
            logger.info(f"Request {idempotency_key} marked as PROCESSING.")

    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            logger.warning(
                f"Request {idempotency_key} was created by a concurrent process. Skipping.")
            return  # Another process is handling this, so we can exit.
        logger.error(
            f"DynamoDB error for request {idempotency_key}: {e}", exc_info=True)
        raise

    chat_id = message_body['chat_id']
    file_id = message_body['file_id']

    logger.info(
        f"Processing message for chat_id: {chat_id}, file_id: {file_id}")

    image_bytes = get_telegram_image(file_id, telegram_bot_token)

    try:
        food_items_text = analyze_image_with_gemini(image_bytes)
    except ValueError as e:
        logger.error(f"Gemini analysis failed: {e}", exc_info=True)
        send_telegram_message(
            chat_id, "Sorry, I couldn't analyze the image. It might be an unsupported format or corrupted.", telegram_bot_token)
        return
    logger.info(f"Gemini response: {food_items_text}")

    if food_items_text.strip() == 'NO_FOOD':
        logger.info(
            f"Gemini identified no food for chat_id: {chat_id}. Notifying user.")
        send_telegram_message(
            chat_id, "Sorry, I couldn't identify any food in the image. Please try another one.", telegram_bot_token)
        return  # Stop processing for this message

    food_items = [item.strip() for item in food_items_text.split(';')]

    total_calories = 0
    total_protein = 0
    total_carbs = 0
    total_fat = 0

    for item in food_items:
        if not item:
            continue

        weight = 0
        food_name = item
        weight_match = re.search(r'\((\d+)g\)', item)
        if weight_match:
            weight = int(weight_match.group(1))
            food_name = item[:weight_match.start()].strip()

        # Remove quantity from food name before searching
        food_name_parts = food_name.split()
        if food_name_parts and food_name_parts[0].isdigit():
            food_name = ' '.join(food_name_parts[1:])

        logger.info(
            f"Processing item: '{item}'. Cleaned food name for search: '{food_name}', weight: {weight}g")

        if weight == 0:
            logger.warning(
                f"Could not determine weight for item: {item}. Skipping.")
            continue

        nutrition_data = get_nutrition_data(food_name, fdc_api_key)
        if nutrition_data and nutrition_data.get('foods'):
            found_food = nutrition_data['foods'][0]
            logger.info(
                f"FDC found food: {found_food.get('description')}")

            nutrients = found_food.get('foodNutrients', [])
            for nutrient in nutrients:
                value_per_100g = nutrient.get('value', 0)
                nutrient_name = nutrient.get('nutrientName')
                nutrient_unit = nutrient.get('unitName', '').upper()

                if nutrient_name in ['Energy', 'Protein', 'Carbohydrate, by difference', 'Total lipid (fat)']:
                    logger.info(
                        f"Nutrient: {nutrient_name}, Value per 100g: {value_per_100g} {nutrient_unit}")

                value_per_gram = value_per_100g / 100
                if nutrient_name == 'Energy' and nutrient_unit == 'KCAL':
                    total_calories += value_per_gram * weight
                elif nutrient_name == 'Protein':
                    total_protein += value_per_gram * weight
                elif nutrient_name == 'Carbohydrate, by difference':
                    total_carbs += value_per_gram * weight
                elif nutrient_name == 'Total lipid (fat)':
                    total_fat += value_per_gram * weight

    # Get current time in Asia/Jerusalem timezone
    now = datetime.now(ZoneInfo('Asia/Jerusalem')
                       ).strftime("%Y-%m-%d %H:%M:%S")

    sheet_data = [
        now,
        ', '.join(food_items),
        round(total_calories, 2),
        round(total_protein, 2),
        round(total_carbs, 2),
        round(total_fat, 2)
    ]

    write_to_google_sheets(
        sheet_data, google_sheets_credentials, spreadsheet_id)

    # Send results to the user
    if food_items:
        # Use the original food items identified by Gemini for the message
        items_text = "\n".join(
            [f"- {item}" for item in food_items if item])
        result_message = f"Nutrition for your meal:\n{items_text}\n\n"
    else:
        result_message = "Nutrition for your meal:\n"

    result_message += f"- Calories: {round(total_calories, 2)}\n"
    result_message += f"- Protein: {round(total_protein, 2)}g\n"
    result_message += f"- Carbs: {round(total_carbs, 2)}g\n"
    result_message += f"- Fat: {round(total_fat, 2)}g"
    send_telegram_message(chat_id, result_message, telegram_bot_token)

    # Mark as COMPLETED
    processed_messages_table.update_item(
        Key={'idempotency_key': idempotency_key},
        UpdateExpression="set #status = :s",
        ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues={':s': 'COMPLETED'}
    )
    logger.info(f"Request {idempotency_key} marked as COMPLETED.")


def lambda_handler(event, context):
    """Lambda function entry point for processing SQS messages."""
    for record in event['Records']:
        try:
            message_body = json.loads(record['body'])
            process_meal_from_message(message_body)

        except Exception as e:
            logger.error(f"Error processing SQS message: {e}", exc_info=True)
            # Try to inform the user about the error
            try:
                # Fetch token only when needed for error reporting
                telegram_bot_token = get_secret('TELEGRAM_BOT_TOKEN_SSM_PATH')
                message_body = json.loads(record['body'])
                if 'chat_id' in message_body:
                    chat_id = message_body['chat_id']
                    send_telegram_message(
                        chat_id, "Sorry, there was an error processing your meal details.", telegram_bot_token)
            except Exception as notify_e:
                logger.error(
                    f"Failed to notify user about the processing error. Error: {notify_e}")
            # Re-raise the exception to ensure the message is redriven to the DLQ
            raise
