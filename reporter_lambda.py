import json
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import boto3
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

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
GOOGLE_SHEETS_CREDENTIALS = get_secret('GOOGLE_SHEETS_CREDENTIALS_SSM_PATH')
SPREADSHEET_ID = get_secret('SPREADSHEET_ID_SSM_PATH')
TELEGRAM_CHAT_ID = get_secret('TELEGRAM_CHAT_ID_SSM_PATH')

# Google Sheets configuration
MEALS_SHEET_NAME = 'Meals'
REPORTS_SHEET_NAME = 'Daily_Reports'


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


def get_sheets_service():
    """Creates and returns a Google Sheets API service object."""
    creds_json = json.loads(GOOGLE_SHEETS_CREDENTIALS)
    creds = service_account.Credentials.from_service_account_info(creds_json)
    return build('sheets', 'v4', credentials=creds)


def calculate_daily_totals(sheet_values, today_str):
    """
    Calculates total nutrition values for a given day from sheet data.
    """
    total_calories, total_protein, total_carbs, total_fat = 0, 0, 0, 0

    # Skip header row if it exists
    for row in sheet_values[1:]:
        if not row:
            continue

        try:
            row_date_str = row[0].split(' ')[0]
            if row_date_str == today_str:
                total_calories += float(row[2])
                total_protein += float(row[3])
                total_carbs += float(row[4])
                total_fat += float(row[5])
        except (ValueError, IndexError) as e:
            logger.warning(f"Could not parse row: {row}. Error: {e}. Skipping.")
            continue

    return total_calories, total_protein, total_carbs, total_fat


def lambda_handler(event, context):
    """
    Lambda function to generate a daily nutrition report.
    - Reads all meal entries from a Google Sheet.
    - Calculates the total nutrition for the current day.
    - Appends the daily summary to another sheet.
    - Sends the summary to a specified Telegram chat.
    """
    try:
        logger.info("Starting daily nutrition report generation.")
        service = get_sheets_service()
        sheet = service.spreadsheets()

        # 1. Read data from the meals sheet
        read_range = f"{MEALS_SHEET_NAME}!A:F"
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID,
                                    range=read_range).execute()
        values = result.get('values', [])

        if not values or len(values) <= 1:
            logger.info("Meals sheet is empty or contains only a header. No report to generate.")
            # Optionally send a message that no meals were logged
            send_telegram_message(TELEGRAM_CHAT_ID, "No meals were logged today. No report generated.")
            return {'statusCode': 200, 'body': 'Sheet was empty or had only a header.'}

        # 2. Calculate daily totals
        today_str = datetime.now(
            ZoneInfo('Asia/Jerusalem')).strftime("%Y-%m-%d")
        logger.info(f"Calculating totals for date: {today_str}")

        total_calories, total_protein, total_carbs, total_fat = calculate_daily_totals(
            values, today_str)

        logger.info(
            f"Calculated totals: Cals={total_calories}, Prot={total_protein}, Carbs={total_carbs}, Fat={total_fat}")

        # 3. Write summary to the daily reports sheet
        summary_data = [
            today_str,
            round(total_calories, 2),
            round(total_protein, 2),
            round(total_carbs, 2),
            round(total_fat, 2)
        ]
        body = {'values': [summary_data]}
        sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{REPORTS_SHEET_NAME}!A:E",
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        logger.info(f"Appended daily summary to '{REPORTS_SHEET_NAME}' sheet.")

        # 4. Send summary to Telegram
        report_message = f"Daily Nutrition Summary for {today_str}:\n"
        report_message += f"- Total Calories: {round(total_calories, 2)}\n"
        report_message += f"- Total Protein: {round(total_protein, 2)}g\n"
        report_message += f"- Total Carbs: {round(total_carbs, 2)}g\n"
        report_message += f"- Total Fat: {round(total_fat, 2)}g"

        send_telegram_message(TELEGRAM_CHAT_ID, report_message)

        logger.info("Successfully generated and sent daily report.")
        return {'statusCode': 200, 'body': 'Report generated successfully.'}

    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        # Optionally, send an error message to Telegram
        try:
            error_message = f"Failed to generate daily nutrition report. Error: {e}"
            send_telegram_message(TELEGRAM_CHAT_ID, error_message)
        except Exception as notify_e:
            logger.error(f"Failed to send error notification. Error: {notify_e}")

        return {'statusCode': 500, 'body': f"An error occurred: {str(e)}"}