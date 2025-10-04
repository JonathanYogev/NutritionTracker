import os
import logging
import boto3
import requests

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize Boto3 clients and a cache for secrets
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


def send_telegram_message(chat_id, text, bot_token):
    """Sends a message to a Telegram user."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
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
        raise e
