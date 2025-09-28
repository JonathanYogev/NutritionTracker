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

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Boto3 client and a cache for secrets
ssm = boto3.client('ssm')
secrets_cache = {}


def get_secret(parameter_name_env_var):
    """Fetches a secret from AWS SSM Parameter Store with caching."""
    if parameter_name_env_var in secrets_cache:
        return secrets_cache[parameter_name_env_var]

    try:
        parameter_name = os.environ[parameter_name_env_var]
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        secret_value = response['Parameter']['Value']
        secrets_cache[parameter_name_env_var] = secret_value
        return secret_value
    except Exception as e:
        logger.error(
            f"Failed to fetch SSM parameter: {os.environ.get(parameter_name_env_var)}. Error: {e}")
        raise e


# API Keys and Tokens from environment variables pointing to SSM
TELEGRAM_BOT_TOKEN = get_secret('TELEGRAM_BOT_TOKEN_SSM_PATH')
GEMINI_API_KEY = get_secret('GEMINI_API_KEY_SSM_PATH')
FDC_API_KEY = get_secret('FDC_API_KEY_SSM_PATH')
GOOGLE_SHEETS_CREDENTIALS = get_secret('GOOGLE_SHEETS_CREDENTIALS_SSM_PATH')
SPREADSHEET_ID = get_secret('SPREADSHEET_ID_SSM_PATH')


def send_telegram_message(chat_id, text):
    """Sends a message to a Telegram user."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info(f"Message sent to chat_id {chat_id}: '{text}'")
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Failed to send message to chat_id {chat_id}. Error: {e}")


def get_telegram_image(file_id):
    """Downloads an image from Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
    response = requests.get(url)
    response.raise_for_status()
    file_path = response.json()['result']['file_path']
    image_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    image_response = requests.get(image_url)
    image_response.raise_for_status()
    return image_response.content


def analyze_image_with_gemini(image_bytes):
    """Analyzes an image with Google Gemini API using the Python SDK."""
    genai.configure(api_key=GEMINI_API_KEY)
    img = Image.open(io.BytesIO(image_bytes))
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content([
        "Identify the food items in the image. For each food item, provide an estimated weight in grams in parentheses. It is very important that the weight in parentheses comes directly after the food item it refers to. For example: '1 cooked chicken breast (170g)'; 'Broccoli florets (160g)'. Separate items with a semicolon (;). Do not include any introductory text in your response, only the list of items. If no food is identifiable in the image, respond with the single word: NO_FOOD.",
        img
    ])
    if not response.parts:
        raise ValueError("Gemini response is empty.")
    return response.text


def get_nutrition_data(food_item):
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
            search_url = f"https://api.nal.usda.gov/fdc/v1/foods/search?query={food_item}&dataType={encoded_data_type}&api_key={FDC_API_KEY}&pageSize=10"
            
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

    picker_model = genai.GenerativeModel('gemini-2.5-flash')
    picker_response = picker_model.generate_content(prompt)

    best_option_number = 0
    try:
        best_option_number = int(picker_response.text.strip())
        selected_food = search_data['foods'][best_option_number - 1]
    except (ValueError, IndexError):
        # If Gemini's response is not a valid number, or out of range,
        # fallback to the first result.
        best_option_number = 1
        selected_food = search_data['foods'][0]

    logger.info(
        f"Gemini picked option {best_option_number}, which is '{selected_food.get('description')}' (FDC ID: {selected_food.get('fdcId')}).")

    # Step 4: Return the selected food data from the search result
    return {'foods': [selected_food]}


def write_to_google_sheets(data):
    """Writes data to Google Sheets."""
    creds_json = json.loads(GOOGLE_SHEETS_CREDENTIALS)
    creds = service_account.Credentials.from_service_account_info(creds_json)
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    body = {'values': [data]}
    result = sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range='Sheet1!A:F',
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
    chat_id = message_body['chat_id']
    file_id = message_body['file_id']

    logger.info(
        f"Processing message for chat_id: {chat_id}, file_id: {file_id}")

    image_bytes = get_telegram_image(file_id)

    food_items_text = analyze_image_with_gemini(image_bytes)
    logger.info(f"Gemini response: {food_items_text}")

    if food_items_text.strip() == 'NO_FOOD':
        logger.info(
            f"Gemini identified no food for chat_id: {chat_id}. Notifying user.")
        send_telegram_message(
            chat_id, "Sorry, I couldn't identify any food in the image. Please try another one.")
        return  # Stop processing for this message

    food_items = [item.strip() for item in food_items_text.split(';')]

    total_calories = 0
    total_protein = 0
    total_carbs = 0
    total_fat = 0
    used_food_descriptions = []

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

        nutrition_data = get_nutrition_data(food_name)
        if nutrition_data and nutrition_data.get('foods'):
            found_food = nutrition_data['foods'][0]
            logger.info(
                f"FDC found food: {found_food.get('description')}")
            used_food_descriptions.append(
                found_food.get('description'))
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

    write_to_google_sheets(sheet_data)

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
    send_telegram_message(chat_id, result_message)


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
                message_body = json.loads(record['body'])
                if 'chat_id' in message_body:
                    chat_id = message_body['chat_id']
                    send_telegram_message(
                        chat_id, "Sorry, there was an error processing your meal details.")
            except Exception as notify_e:
                logger.error(
                    f"Failed to notify user about the processing error. Error: {notify_e}")