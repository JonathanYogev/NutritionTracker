# AI-Powered Nutrition Tracker via Telegram

This project is a serverless, AI-powered nutrition tracker that allows a user to log their meals by simply sending a picture to a Telegram bot. The system analyzes the image, calculates nutritional information, and logs the data to a personal Google Sheet.

## Features

- **Image-Based Meal Logging**: Send a picture of your meal to the Telegram bot to start the logging process.
- **AI Food Identification**: Uses Google Gemini to identify food items and estimate their weight directly from the image.
- **Enhanced Nutrition Data Search**: Queries the USDA's FoodData Central API across multiple data types (SR Legacy, Foundation, Survey (FNDDS)) to find the most accurate nutritional match, further refined by Gemini.
- **Guaranteed Idempotency**: Ensures each meal is processed exactly once, preventing duplicate entries in your data, even if messages are re-delivered.
- **Automated Google Sheets Logging**: Automatically appends each meal's nutritional information to a specified Google Sheet.
- **Daily Summary Reports**: A scheduled function runs daily to calculate total nutrition for the day, logs it to a separate summary sheet, and sends a report to the user via Telegram.
- **Robust & Scalable Architecture**: Built on a serverless AWS stack (Lambda, SQS, API Gateway, DynamoDB) for high reliability and scalability.

## Architecture

The application is composed of three core, decoupled services orchestrated by AWS services:

1.  **Client Lambda (`client_lambda.py`)**:
    -   Triggered by an **API Gateway** webhook which receives messages from Telegram.
    -   When a user sends an image, this function sends a job message to an **SQS queue**.
    -   Provides an immediate "Processing..." acknowledgement to the user.

2.  **Processor Lambda (`processor_lambda.py`)**:
    -   Triggered by new messages in the **SQS queue**.
    -   **Idempotency Check**: Uses a DynamoDB table to ensure each message is processed only once.
    -   Downloads the image from Telegram.
    -   Uses the Gemini API to identify food items and estimate weights.
    -   For each food item, it queries the FoodData Central API across multiple data types (SR Legacy, Foundation, Survey (FNDDS)) to find the best nutritional match (using Gemini again to refine the choice).
    -   Calculates total nutrition for the meal.
    -   Writes the meal data to the primary Google Sheet.
    -   Sends a final summary message to the user on Telegram.

3.  **Reporter Lambda (`reporter_lambda.py`)**:
    -   Triggered by a daily **Amazon EventBridge** schedule (cron job).
    -   Reads all meal entries for the current day from the Google Sheet.
    -   Calculates the total nutritional values for the day.
    -   Appends the daily summary to a separate "Daily Reports" sheet.
    -   Sends the daily summary report to the user on Telegram.

## Setup and Deployment

A complete, step-by-step guide for deploying the entire application is available in [**deployment_guide.md**](./deployment_guide.md).

The high-level steps are:
1.  **Prerequisites**: Ensure you have an AWS account, a Telegram bot, and a Google Cloud project with the necessary APIs enabled.
2.  **Store Secrets**: All API keys and tokens are securely stored in AWS Systems Manager (SSM) Parameter Store.
3.  **Create AWS Resources**: Set up an SQS queue, a DynamoDB table for idempotency, and an IAM role with appropriate permissions.
4.  **Create a Lambda Layer**: A single Lambda Layer is created to manage all Python dependencies for the functions.
5.  **Deploy Functions**: Deploy the three Lambda functions (`client`, `processor`, `reporter`) and configure their triggers and environment variables.
6.  **Configure API Gateway**: Set up an HTTP API endpoint to receive the Telegram webhook.
7.  **Set Webhook**: Point your Telegram bot to your new API Gateway endpoint.

## Usage

1.  Open a chat with your configured Telegram bot.
2.  Send a picture of your meal.
3.  Receive a confirmation that it's being processed.
4.  A few moments later, receive a message with the nutritional breakdown for the meal.
5.  At the end of the day, receive a daily summary report.
