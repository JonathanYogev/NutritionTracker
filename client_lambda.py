import json
import os
import logging
import boto3
from common.utils import get_secret, send_telegram_message

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Boto3 clients
sqs = boto3.client('sqs')


def lambda_handler(event, context):
    """
    Receives a webhook from Telegram, sends a message to SQS,
    and sends a confirmation to the user.
    """
    try:
        logger.info(f"Received event: {json.dumps(event)}")

        # Fetch secrets and config within the handler for security and freshness
        telegram_bot_token = get_secret('TELEGRAM_BOT_TOKEN_SSM_PATH')
        sqs_queue_url = os.environ.get('SQS_QUEUE_URL')

        if not sqs_queue_url:
            logger.error(
                "CRITICAL: SQS_QUEUE_URL environment variable is not set.")
            # We can't notify the user because we might not have chat_id yet.
            return {'statusCode': 500, 'body': json.dumps("Internal server configuration error.")}

        body = json.loads(event.get('body', '{}'))

        if 'message' not in body or 'photo' not in body['message']:
            logger.info("No photo found in the message.")
            return {'statusCode': 200, 'body': json.dumps('No photo found.')}

        chat_id = body['message']['chat']['id']
        message_id = body['message']['message_id']
        file_id = body['message']['photo'][-1]['file_id']

        # Create a unique key to ensure this message is processed only once
        idempotency_key = f"{chat_id}-{message_id}"

        # Send "processing" message to the user
        send_telegram_message(
            chat_id, "Processing your meal...", telegram_bot_token)

        # Prepare message for SQS
        sqs_message = {
            'chat_id': chat_id,
            'file_id': file_id,
            'idempotency_key': idempotency_key
        }

        # Send message to SQS
        sqs.send_message(
            QueueUrl=sqs_queue_url,
            MessageBody=json.dumps(sqs_message)
        )
        logger.info(f"Message sent to SQS queue for chat_id {chat_id}.")

        return {'statusCode': 200, 'body': json.dumps('Request is being processed.')}

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        # Try to inform the user about the error
        try:
            telegram_bot_token = get_secret('TELEGRAM_BOT_TOKEN_SSM_PATH')
            body = json.loads(event.get('body', '{}'))
            if 'message' in body and 'chat' in body['message']:
                chat_id = body['message']['chat']['id']
                send_telegram_message(
                    chat_id, "Sorry, there was an error processing your request.", telegram_bot_token)
        except Exception as notify_e:
            logger.error(
                f"Failed to notify user about the error. Error: {notify_e}")

        return {'statusCode': 500, 'body': json.dumps(f"An error occurred: {str(e)}")}
