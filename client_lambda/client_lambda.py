import json
import os
import logging
import boto3
import requests

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Boto3 clients and a cache for secrets
ssm = boto3.client('ssm')
sqs = boto3.client('sqs')
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
        logger.error(f"Failed to fetch SSM parameter: {os.environ.get(parameter_name_env_var)}. Error: {e}")
        raise e

# API Keys and Tokens from environment variables pointing to SSM
TELEGRAM_BOT_TOKEN = get_secret('TELEGRAM_BOT_TOKEN_SSM_PATH')
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL')

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
        logger.error(f"Failed to send message to chat_id {chat_id}. Error: {e}")

def lambda_handler(event, context):
    """
    Receives a webhook from Telegram, sends a message to SQS,
    and sends a confirmation to the user.
    """
    try:
        logger.info(f"Received event: {json.dumps(event)}")
        body = json.loads(event.get('body', '{}'))

        if 'message' not in body or 'photo' not in body['message']:
            logger.info("No photo found in the message.")
            return {'statusCode': 200, 'body': json.dumps('No photo found.')}

        chat_id = body['message']['chat']['id']
        file_id = body['message']['photo'][-1]['file_id']
        
        # Send "processing" message to the user
        send_telegram_message(chat_id, "Processing your meal...")

        # Prepare message for SQS
        sqs_message = {
            'chat_id': chat_id,
            'file_id': file_id
        }

        # Send message to SQS
        sqs.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody=json.dumps(sqs_message)
        )
        logger.info(f"Message sent to SQS queue for chat_id {chat_id}.")

        return {'statusCode': 200, 'body': json.dumps('Request is being processed.')}

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        # Try to inform the user about the error
        try:
            body = json.loads(event.get('body', '{}'))
            if 'message' in body and 'chat' in body['message']:
                chat_id = body['message']['chat']['id']
                send_telegram_message(chat_id, "Sorry, there was an error processing your request.")
        except Exception as notify_e:
            logger.error(f"Failed to notify user about the error. Error: {notify_e}")
            
        return {'statusCode': 500, 'body': json.dumps(f"An error occurred: {str(e)}")}
