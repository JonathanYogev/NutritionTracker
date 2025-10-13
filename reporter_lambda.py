import json
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import boto3
from google.oauth2 import service_account
from googleapiclient.discovery import build
from common.utils import get_secret, send_telegram_message

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def get_sheets_service(google_sheets_credentials):
    """Creates and returns a Google Sheets API service object."""
    creds_json = json.loads(google_sheets_credentials)
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
            logger.warning(
                f"Could not parse row: {row}. Error: {e}. Skipping.")
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
    # Fetch secrets and config
    telegram_bot_token = get_secret('TELEGRAM_BOT_TOKEN_SSM_PATH')
    google_sheets_credentials = get_secret(
        'GOOGLE_SHEETS_CREDENTIALS_SSM_PATH')
    spreadsheet_id = get_secret('SPREADSHEET_ID_SSM_PATH')
    telegram_chat_id = get_secret('TELEGRAM_CHAT_ID_SSM_PATH')

    # Externalize sheet names for better maintainability
    meals_sheet_name = os.environ.get('MEALS_SHEET_NAME', 'Meals')
    reports_sheet_name = os.environ.get('REPORTS_SHEET_NAME', 'Daily_Reports')

    try:
        logger.info("Starting daily nutrition report generation.")
        service = get_sheets_service(google_sheets_credentials)
        sheet = service.spreadsheets()

        read_range = f"{meals_sheet_name}!A:F"
        result = sheet.values().get(spreadsheetId=spreadsheet_id,
                                    range=read_range).execute()
        values = result.get('values', [])

        if not values or len(values) <= 1:
            logger.info(
                "Meals sheet is empty or contains only a header. No report to generate.")
            # Optionally send a message that no meals were logged
            send_telegram_message(
                telegram_chat_id, "No meals were logged today. No report generated.", telegram_bot_token)
            return {'statusCode': 200, 'body': 'Sheet was empty or had only a header.'}

        today_str = datetime.now(
            ZoneInfo('Asia/Jerusalem')).strftime("%Y-%m-%d")
        logger.info(f"Calculating totals for date: {today_str}")

        total_calories, total_protein, total_carbs, total_fat = calculate_daily_totals(
            values, today_str)

        logger.info(
            f"Calculated totals: Cals={total_calories}, Prot={total_protein}, Carbs={total_carbs}, Fat={total_fat}")

        summary_data = [
            today_str,
            round(total_calories, 2),
            round(total_protein, 2),
            round(total_carbs, 2),
            round(total_fat, 2)
        ]
        body = {'values': [summary_data]}
        sheet.values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{reports_sheet_name}!A:E",
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        logger.info(f"Appended daily summary to '{reports_sheet_name}' sheet.")

        report_message = f"📊 Daily Nutrition Summary for {today_str}:\n\n"
        report_message += f"🔥 Total Calories: {round(total_calories, 2)}\n"
        report_message += f"💪 Total Protein: {round(total_protein, 2)}g\n"
        report_message += f"🍞 Total Carbs: {round(total_carbs, 2)}g\n"
        report_message += f"🥑 Total Fat: {round(total_fat, 2)}g"

        send_telegram_message(
            telegram_chat_id, report_message, telegram_bot_token)

        logger.info("Successfully generated and sent daily report.")
        return {'statusCode': 200, 'body': 'Report generated successfully.'}

    except Exception as e:
        logger.error(f"An error occurred: {e}", exc_info=True)
        # Optionally, send an error message to Telegram
        try:
            error_message = f"Failed to generate daily nutrition report. Error: {e}"
            send_telegram_message(
                telegram_chat_id, error_message, telegram_bot_token)
        except Exception as notify_e:
            logger.error(
                f"Failed to send error notification. Error: {notify_e}")

        return {'statusCode': 500, 'body': f"An error occurred: {str(e)}"}