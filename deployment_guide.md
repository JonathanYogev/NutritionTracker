# Deployment Guide: Decoupled Nutrition Tracker

This guide provides step-by-step instructions to deploy the decoupled nutrition tracker application on AWS. The architecture consists of two Lambda functions, an SQS queue, and an API Gateway.

## 1. Prerequisites

- An AWS account with Free Tier eligibility.
- A Telegram Bot token from BotFather.
- A Google Cloud project with the Google Sheets API and Gemini API enabled.
- A service account for Google Cloud with credentials to access the Sheets API.
- A FoodData Central API key.

## 2. Securely Store API Keys and Secrets

Before deploying the Lambdas, store all your secrets in AWS Systems Manager (SSM) Parameter Store. This is a best practice for security and manageability.

1.  **Navigate to SSM Parameter Store** in the AWS Console.
2.  Create the following parameters with the type `SecureString`:
    - `TELEGRAM_BOT_TOKEN_SSM_PATH`: Your Telegram bot token.
    - `GEMINI_API_KEY_SSM_PATH`: Your Google Gemini API key.
    - `FDC_API_KEY_SSM_PATH`: Your FoodData Central API key.
    - `GOOGLE_SHEETS_CREDENTIALS_SSM_PATH`: The JSON content of your Google service account credentials file.
    - `SPREADSHEET_ID_SSM_PATH`: The ID of your Google Sheet.

## 3. Create the SQS Queue

This queue will decouple the `client_lambda` from the `processor_lambda`.

1.  **Navigate to SQS** in the AWS Console.
2.  Click **Create queue**.
3.  Select **Standard** as the queue type.
4.  Name the queue (e.g., `nutrition-tracker-queue`).
5.  Leave the default settings and click **Create queue**.
6.  Note the **Queue URL**; you will need it later.

## 4. Create IAM Roles and Policies

You will need two IAM roles, one for each Lambda function.

### 4.1. `client_lambda_role`

1.  **Navigate to IAM** > **Roles** and click **Create role**.
2.  Select **AWS service** as the trusted entity, and choose **Lambda**.
3.  Add the following AWS managed policies:
    - `AWSLambdaBasicExecutionRole`
4.  Create a new inline policy with the following JSON to allow sending messages to the SQS queue and reading from SSM:

    ```json
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "sqs:SendMessage",
                "Resource": "arn:aws:sqs:REGION:ACCOUNT_ID:YOUR_QUEUE_NAME"
            },
            {
                "Effect": "Allow",
                "Action": "ssm:GetParameter",
                "Resource": "arn:aws:ssm:REGION:ACCOUNT_ID:parameter/PATH_TO_YOUR_SECRETS/*"
            }
        ]
    }
    ```

    **Replace:**
    - `REGION`: Your AWS region (e.g., `us-east-1`).
    - `ACCOUNT_ID`: Your AWS account ID.
    - `YOUR_QUEUE_NAME`: The name of your SQS queue.
    - `PATH_TO_YOUR_SECRETS`: The path prefix for your SSM parameters.

5.  Name the role `client_lambda_role` and create it.

### 4.2. `processor_lambda_role`

1.  Create another role for the `processor_lambda` following the same steps as above.
2.  Add the `AWSLambdaBasicExecutionRole` managed policy.
3.  Create a new inline policy with the following JSON to allow reading messages from the SQS queue and reading from SSM:

    ```json
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "sqs:ReceiveMessage",
                    "sqs:DeleteMessage",
                    "sqs:GetQueueAttributes"
                ],
                "Resource": "arn:aws:sqs:REGION:ACCOUNT_ID:YOUR_QUEUE_NAME"
            },
            {
                "Effect": "Allow",
                "Action": "ssm:GetParameter",
                "Resource": "arn:aws:ssm:REGION:ACCOUNT_ID:parameter/PATH_TO_YOUR_SECRETS/*"
            }
        ]
    }
    ```

4.  Name the role `processor_lambda_role` and create it.

## 5. Create and Deploy the Lambda Functions

### 5.1. `client_lambda`

1.  **Navigate to Lambda** and click **Create function**.
2.  Select **Author from scratch**.
3.  Name the function `client_lambda`.
4.  Choose **Python 3.12** as the runtime.
5.  Select **Use an existing role** and choose the `client_lambda_role` you created.
6.  Click **Create function**.
7.  In the **Code source** section, upload a ZIP file containing `client_lambda.py` and the libraries from `client_requirements.txt`.
8.  In the **Configuration** > **Environment variables** section, add the following:
    - `TELEGRAM_BOT_TOKEN_SSM_PATH`: The name of your SSM parameter for the Telegram token.
    - `SQS_QUEUE_URL`: The URL of your SQS queue.

### 5.2. `processor_lambda`

1.  Create another Lambda function named `processor_lambda`.
2.  Choose **Python 3.12** as the runtime and use the `processor_lambda_role`.
3.  Upload a ZIP file containing `processor_lambda.py` and the libraries from `processor_requirements.txt`.
4.  In the **Configuration** > **Environment variables** section, add the following:
    - `TELEGRAM_BOT_TOKEN_SSM_PATH`: The name of your SSM parameter for the Telegram token.
    - `GEMINI_API_KEY_SSM_PATH`: The name of your SSM parameter for the Gemini API key.
    - `FDC_API_KEY_SSM_PATH`: The name of your SSM parameter for the FDC API key.
    - `GOOGLE_SHEETS_CREDENTIALS_SSM_PATH`: The name of your SSM parameter for the Google Sheets credentials.
    - `SPREADSHEET_ID_SSM_PATH`: The name of your SSM parameter for the spreadsheet ID.
5.  In the **Configuration** > **Triggers** section, add a trigger:
    - Select **SQS** as the source.
    - Choose the SQS queue you created.
    - Leave the batch size at 10 (or adjust as needed).
    - Click **Add**.

## 6. Configure API Gateway

1.  **Navigate to API Gateway** and click **Create API**.
2.  Choose **HTTP API** and click **Build**.
3.  Click **Add integration** and select **Lambda**.
4.  Choose the `client_lambda` function.
5.  Give your API a name (e.g., `nutrition-tracker-api`).
6.  Configure a `POST` route for `/webhook` that points to the `client_lambda` integration.
7.  Deploy your API.

## 7. Set the Telegram Webhook

After deploying your API, you need to tell Telegram where to send updates. You can do this by sending a `POST` request to the Telegram API with your API Gateway URL. You can use `curl` or a tool like Postman:

```bash
curl -F "url=https://YOUR_API_GATEWAY_URL/webhook" https://api.telegram.org/botYOUR_TELEGRAM_BOT_TOKEN/setWebhook
```

Replace `YOUR_API_GATEWAY_URL` and `YOUR_TELEGRAM_BOT_TOKEN` with your actual values.

## 8. Example Google Sheets Row Output

| Date & Time (Asia/Jerusalem) | Food item(s)                                  | Calories | Protein | Carbs | Fat   |
| ---------------------------- | --------------------------------------------- | -------- | ------- | ----- | ----- |
| 2025-09-28 14:30:00          | 1 cooked chicken breast (170g); Broccoli florets (160g) | 534.50   | 68.50   | 10.20 | 24.80 |

## 9. Notes on Keeping the Solution Completely Free

- **AWS Lambda**: The free tier includes 1 million free requests per month and 400,000 GB-seconds of compute time per month.
- **Amazon SQS**: The free tier includes 1 million requests per month.
- **Amazon API Gateway**: The free tier includes 1 million HTTP API calls per month.
- **AWS SSM Parameter Store**: Standard parameters are free. High-throughput parameters have a cost.
- **Google Gemini API**: The free tier of Gemini has limitations on requests per minute. Check the latest documentation for details.
- **FoodData Central API**: This is a free service from the USDA.
- **Google Sheets API**: The free tier has limitations on the number of requests per day.

By staying within these limits, you can run this solution for free.

## 10. Deploying the Daily Reporter Lambda (`reporter_lambda`)

This optional function runs on a daily schedule to read all the meal entries for the day, calculate a total nutritional summary, append that summary to a new sheet, and send the report to you via Telegram.

### 10.1. Prerequisites

1.  **Create the `Daily_Reports` Sheet**: In your Google Sheets document, create a new sheet and name it `Daily_Reports`.
2.  **Store Your Chat ID**: You must have your personal Telegram Chat ID stored in SSM Parameter Store.
    - To find your ID, message the bot `@userinfobot` on Telegram.
    - Create a new `String` parameter in SSM named `/nutrition-tracker/telegram-chat-id` and paste your ID as the value.

### 10.2. Packaging the Lambda

You need to create a `.zip` deployment package that includes the function code and its dependencies.

1.  **Create a directory and virtual environment**:
    ```bash
    mkdir reporter_package
    cd reporter_package
    python -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r ../reporter_requirements.txt -t .
    ```

3.  **Add Lambda code and create ZIP file**:
    ```bash
    cp ../reporter_lambda.py .
    zip -r reporter_lambda_package.zip .
    ```

### 10.3. Create and Configure the Lambda Function

1.  **Navigate to Lambda** in the AWS Console and click **Create function**.
2.  Select **Author from scratch**.
3.  **Function name**: `nutrition-tracker-reporter`.
4.  **Runtime**: Choose **Python 3.12** (or a compatible version).
5.  **Permissions**: Select **Use an existing role** and choose the same IAM role you are using for `processor_lambda_role`. This role should already have permissions for SSM and CloudWatch Logs.
6.  Click **Create function**.

### 10.4. Upload Code and Configure Settings

1.  In the **Code source** section, click **Upload from** -> **.zip file** and upload the `reporter_lambda_package.zip` file you created.
2.  Go to the **Configuration** > **Environment variables** section and add the following:
    - `TELEGRAM_BOT_TOKEN_SSM_PATH`: The name of your SSM parameter for the Telegram token.
    - `GOOGLE_SHEETS_CREDENTIALS_SSM_PATH`: The name of your SSM parameter for the Google Sheets credentials.
    - `SPREADSHEET_ID_SSM_PATH`: The name of your SSM parameter for the spreadsheet ID.
    - `TELEGRAM_CHAT_ID_SSM_PATH`: The name of your SSM parameter for your personal Telegram chat ID (e.g., `/nutrition-tracker/telegram-chat-id`).
3.  Go to **Configuration** > **General configuration** and click **Edit**. Increase the **Timeout** to at least **30 seconds**.
4.  Under **Runtime settings**, ensure the handler is set to `reporter_lambda.lambda_handler`.

### 10.5. Create the Scheduled Trigger

This will run your function automatically every day.

1.  In the function's main page, click **Add trigger**.
2.  Select **EventBridge (CloudWatch Events)** as the source.
3.  Choose **Create a new rule**.
4.  **Rule name**: `daily-nutrition-report-trigger`.
5.  **Rule type**: Select **Schedule expression**.
6.  **Schedule expression**: Enter a cron expression for your desired reporting time. For example, to run at 11:00 PM UTC every day, use: `cron(0 23 * * ? *)`.
7.  Click **Add**.

Your daily reporter is now fully deployed. It will trigger at the scheduled time, calculate your nutrition for the day, and send you a report.
