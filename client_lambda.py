import json
import os
import logging
import boto3
from common.utils import get_secret, send_telegram_message

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Boto3 clients and a cache for secrets
sqs = boto3.client('sqs')

# API Keys and Tokens from environment variables pointing to SSM
TELEGRAM_BOT_TOKEN = get_secret('TELEGRAM_BOT_TOKEN_SSM_PATH')
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL')

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
        send_telegram_message(chat_id, "Processing your meal...", TELEGRAM_BOT_TOKEN)

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
                send_telegram_message(
                    chat_id, "Sorry, there was an error processing your request.", TELEGRAM_BOT_TOKEN)
        except Exception as notify_e:
            logger.error(
                f"Failed to notify user about the error. Error: {notify_e}")

        return {'statusCode': 500, 'body': json.dumps(f"An error occurred: {str(e)}")}
