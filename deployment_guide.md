# Deployment Guide: Nutrition Tracker

This guide provides step-by-step instructions to deploy the nutrition tracker application on AWS using a robust Lambda Layer approach.

## 1. Prerequisites

- An AWS account.
- A Telegram Bot token from BotFather.
- A Google Cloud project with the Google Sheets API and Gemini API enabled.
- A service account for Google Cloud with credentials to access the Sheets API.
- A FoodData Central API key.

## 2. Securely Store Secrets in SSM

Before deploying, store all secrets in AWS Systems Manager (SSM) Parameter Store.

1.  **Navigate to SSM Parameter Store** in the AWS Console.
2.  Click **Create parameter** for each of the following secrets. Use the type `SecureString`.
    -   **/nutrition-tracker/telegram-bot-token**: Your Telegram bot token.
    -   **/nutrition-tracker/gemini-api-key**: Your Google Gemini API key.
    -   **/nutrition-tracker/fdc-api-key**: Your FoodData Central API key.
    -   **/nutrition-tracker/google-sheets-credentials**: The full JSON content of your Google service account credentials file.
    -   **/nutrition-tracker/spreadsheet-id**: The ID of your Google Sheet.
    -   **/nutrition-tracker/telegram-chat-id**: Your personal Telegram Chat ID (get this by messaging `@userinfobot` on Telegram).

## 3. Create the SQS Queue

This queue decouples the client and processor functions.

1.  **Navigate to SQS** in the AWS Console.
2.  Click **Create queue**.
3.  **Name**: `nutrition-tracker-queue`.
4.  Set the **Default visibility timeout** to **5 minutes 30 seconds**.
5.  Leave the other default settings and click **Create queue**.
6.  Note the **Queue URL** and **ARN** for later.

## 4. Create a Lambda Layer for All Dependencies

A single layer will hold all Python libraries for our functions, simplifying deployment.

1.  **Create a local directory structure**:
    ```bash
    mkdir -p lambda_layer/python
    ```
2.  **Create a requirements file**: Create a file named `requirements.txt` with the following content:
    ```
    requests
    boto3
    google-api-python-client
    google-auth-oauthlib
    google-generativeai
    Pillow
    ```
3.  **Install dependencies into the layer folder**:
    ```bash
    pip install -r requirements.txt -t lambda_layer/python
    ```
4.  **Create the layer ZIP file**:
    ```bash
    cd lambda_layer
    zip -r dependencies_layer.zip python
    cd ..
    ```
5.  **Create the Layer in AWS**:
    -   Navigate to **Lambda** > **Layers** in the AWS Console.
    -   Click **Create layer**.
    -   **Name**: `nutrition-tracker-dependencies`.
    -   **Description**: "Shared dependencies for the nutrition tracker project."
    -   Upload the `dependencies_layer.zip` file.
    -   **Compatible runtimes**: Select **Python 3.12**.
    -   Click **Create**.

## 5. Create IAM Role for Lambdas

A single IAM role can be used for all three functions.

1.  **Navigate to IAM** > **Roles** and click **Create role**.
2.  **Trusted entity**: Select **AWS service** > **Lambda**.
3.  **Permissions**: Add the `AWSLambdaBasicExecutionRole` managed policy.
4.  **Create a new inline policy** with the following JSON, replacing `REGION`, `ACCOUNT_ID`, and `YOUR_QUEUE_NAME` with your specific values.
    ```json
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "ssm:GetParameter",
                "Resource": "arn:aws:ssm:REGION:ACCOUNT_ID:parameter/nutrition-tracker/*"
            },
            {
                "Effect": "Allow",
                "Action": "sqs:*",
                "Resource": "arn:aws:sqs:REGION:ACCOUNT_ID:YOUR_QUEUE_NAME"
            }
        ]
    }
    ```
5.  **Name the role** `nutrition-tracker-lambda-role` and create it.

## 6. Deploy the Lambda Functions

For each function below, follow these general steps.

### 6.1. `client_lambda`

1.  **Create Function**:
    -   Navigate to **Lambda** > **Create function**.
    -   **Name**: `nutrition-tracker-client`.
    -   **Runtime**: Python 3.12.
    -   **Role**: Choose the `nutrition-tracker-lambda-role` you created.
2.  **Add Code**: Copy the code from `client_lambda.py` and paste it into the inline code editor.
3.  **Add Layer**:
    -   In the "Layers" section, click **Add a layer**.
    -   Choose **Custom layers**, select `nutrition-tracker-dependencies`, and choose the latest version.
4.  **Add Environment Variables**:
    -   Go to **Configuration** > **Environment variables**.
    -   `TELEGRAM_BOT_TOKEN_SSM_PATH`: `/nutrition-tracker/telegram-bot-token`
    -   `SQS_QUEUE_URL`: The URL of your SQS queue.
5.  **Increase Timeout**: Under **General configuration**, set the timeout to **10 seconds**.

### 6.2. `processor_lambda`

1.  **Create Function**:
    -   **Name**: `nutrition-tracker-processor`.
    -   **Runtime**: Python 3.12.
    -   **Role**: `nutrition-tracker-lambda-role`.
2.  **Add Code**: Copy the code from `processor_lambda.py` into the editor.
3.  **Add Layer**: Add the `nutrition-tracker-dependencies` layer.
4.  **Add Trigger**:
    -   Click **Add trigger** and select **SQS**.
    -   Choose your `nutrition-tracker-queue`.
    -   Leave defaults and click **Add**.
5.  **Add Environment Variables**:
    -   `TELEGRAM_BOT_TOKEN_SSM_PATH`: `/nutrition-tracker/telegram-bot-token`
    -   `GEMINI_API_KEY_SSM_PATH`: `/nutrition-tracker/gemini-api-key`
    -   `FDC_API_KEY_SSM_PATH`: `/nutrition-tracker/fdc-api-key`
    -   `GOOGLE_SHEETS_CREDENTIALS_SSM_PATH`: `/nutrition-tracker/google-sheets-credentials`
    -   `SPREADSHEET_ID_SSM_PATH`: `/nutrition-tracker/spreadsheet-id`
6.  **Increase Timeout**: Set the timeout to **5 minutes** to allow for image processing.

### 6.3. `reporter_lambda`

1.  **Create Function**:
    -   **Name**: `nutrition-tracker-reporter`.
    -   **Runtime**: Python 3.12.
    -   **Role**: `nutrition-tracker-lambda-role`.
2.  **Add Code**: Copy the code from `reporter_lambda.py` into the editor.
3.  **Add Layer**: Add the `nutrition-tracker-dependencies` layer.
4.  **Add Trigger (Scheduled)**:
    -   Click **Add trigger** and select **EventBridge (CloudWatch Events)**.
    -   Choose **Create a new rule**.
    -   **Rule name**: `daily-nutrition-report-trigger`.
    -   **Schedule expression**: `cron(0 23 * * ? *)` (for 11 PM UTC daily, adjust as needed).
5.  **Add Environment Variables**:
    -   `TELEGRAM_BOT_TOKEN_SSM_PATH`: `/nutrition-tracker/telegram-bot-token`
    -   `GOOGLE_SHEETS_CREDENTIALS_SSM_PATH`: `/nutrition-tracker/google-sheets-credentials`
    -   `SPREADSHEET_ID_SSM_PATH`: `/nutrition-tracker/spreadsheet-id`
    -   `TELEGRAM_CHAT_ID_SSM_PATH`: `/nutrition-tracker/telegram-chat-id`
6.  **Increase Timeout**: Set the timeout to **30 seconds**.

## 7. Configure API Gateway

1.  **Navigate to API Gateway** > **Create API**.
2.  Choose **HTTP API** > **Build**.
3.  **Integration**: Select **Lambda** and choose the `nutrition-tracker-client` function.
4.  **API name**: `nutrition-tracker-api`.
5.  **Route**: Configure a `POST` method for the path `/webhook`.
6.  Review and create, then deploy your API.

## 8. Set the Telegram Webhook

Send a `POST` request to Telegram, replacing the placeholders with your values:
```bash
curl -F "url=https://YOUR_API_GATEWAY_URL/webhook" https://api.telegram.org/botYOUR_TELEGRAM_BOT_TOKEN/setWebhook
```

Your application is now fully deployed.