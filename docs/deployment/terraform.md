# Deployment Guide: Nutrition Tracker with Terraform

This guide provides step-by-step instructions to deploy the nutrition tracker application on AWS using Terraform.

## 1. Prerequisites

- [Terraform](https://learn.hashicorp.com/tutorials/terraform/install-cli) v1.0 or later.
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-install.html) installed and configured with your AWS credentials.
- A Telegram Bot token, Google Cloud project, and FoodData Central API key as described in the main project README.
- A tool to create zip files (e.g., `zip` command-line utility).

### Configure IAM Role for Terraform (Security Best Practice)

For enhanced security, it is highly recommended to run Terraform with an IAM role that has permissions tailored specifically for this project, rather than using your personal IAM user credentials. This adheres to the principle of least privilege.

To set this up:

1.  **Create a dedicated IAM role** in your AWS account (e.g., `Terraform-NutritionTracker-Role`).
2.  **Create an IAM policy** that grants the necessary permissions for Terraform to create the resources defined in this project (API Gateway, Lambda, SQS, DynamoDB, IAM roles for Lambdas, etc.). Attach this policy to the role.
3.  **Configure your local environment** to assume this IAM role when you run `terraform apply`. You can do this by setting up a dedicated profile in your `~/.aws/config` file.

By using a dedicated role, you ensure that your Terraform deployments are secure, consistent, and auditable.

## 2. Step-by-Step Instructions

### Part 1: Prerequisites

#### 2.1. Create Google Sheet

Follow the instructions in the [Manual Deployment Guide - Create Google Sheet section](./manual.md#2-create-google-sheet) to create your Google Sheet and share it with your service account. Make a note of the **Spreadsheet ID**.

#### 2.2. Store Secrets in SSM Parameter Store

This deployment uses a Terraform variable, `env` (which defaults to `dev`), to prefix all resources. You **must** create your secrets in AWS SSM Parameter Store using this prefix.

**Option A: Using AWS CLI (Recommended)**

This is the fastest method. Copy the commands below, replace the placeholder values (e.g., `YOUR_TELEGRAM_BOT_TOKEN`) with your actual secrets, and run them in your terminal.

```bash
# For your Telegram Bot Token
aws ssm put-parameter --name "/dev/nutrition-tracker/telegram-bot-token" --value "YOUR_TELEGRAM_BOT_TOKEN" --type "SecureString" --region <YOUR_REGION>

# For your Gemini API Key
aws ssm put-parameter --name "/dev/nutrition-tracker/gemini-api-key" --value "YOUR_GEMINI_API_KEY" --type "SecureString" --region <YOUR_REGION>

# For your FoodData Central API Key
aws ssm put-parameter --name "/dev/nutrition-tracker/fdc-api-key" --value "YOUR_FDC_API_KEY" --type "SecureString" --region <YOUR_REGION>

# For your Google Sheets Credentials
aws ssm put-parameter --name "/dev/nutrition-tracker/google-sheets-credentials" --value file://"<PATH_TO_JSON>" --type "SecureString" --region <YOUR_REGION>

# For your Google Spreadsheet ID
aws ssm put-parameter --name "/dev/nutrition-tracker/spreadsheet-id" --value "YOUR_SPREADSHEET_ID" --type "SecureString" --region <YOUR_REGION>

# For your personal Telegram Chat ID
aws ssm put-parameter --name "/dev/nutrition-tracker/telegram-chat-id" --value "YOUR_TELEGRAM_CHAT_ID" --type "SecureString" --region <YOUR_REGION>
```

**Option B: Using AWS Management Console**

1.  Navigate to **AWS Systems Manager > Parameter Store**.
2.  Click **Create parameter** for each secret, ensuring the **Name** matches exactly. Use the **Type** `SecureString` for sensitive values.

    -   `/dev/nutrition-tracker/telegram-bot-token`
    -   `/dev/nutrition-tracker/gemini-api-key`
    -   `/dev/nutrition-tracker/fdc-api-key`
    -   `/dev/nutrition-tracker/google-sheets-credentials`
    -   `/dev/nutrition-tracker/spreadsheet-id`
    -   `/dev/nutrition-tracker/telegram-chat-id`

#### 2.3. Create the Lambda Layer ZIP File  (Run from Project Root)

This step packages your Python dependencies for the Lambda functions.

1.  **Install dependencies into the layer folder**:
    ```bash
    pip install -r requirements.txt -t lambda_layer/python
    ```

2.  **Create the zip file**:
    ```bash
    cd lambda_layer
    zip -r dependencies_layer.zip python
    cd ..
    ```
    This creates the `lambda_layer/dependencies_layer.zip` file, which Terraform will use.

### Part 2: Deploy Infrastructure (Run from Terraform Directory)

Now, you will work exclusively in the `terraform` subdirectory.

#### 2.4. Navigate to the Terraform Directory

```bash
cd terraform
```

#### 2.5. Create `terraform.tfvars` File (Optional)

Your Terraform configuration uses default values for the region (`us-east-1`) and environment (`dev`). If you want to override these, create a `terraform.tfvars` file.

You can copy the example file as a starting point:
```bash
cp terraform.tfvars.example terraform.tfvars
```

Open `terraform.tfvars` and customize it. For example, to deploy a `staging` environment, you would change it to:
```hcl
aws_region = "eu-west-1"
env        = "staging"
```
**Note:** If you use a different `env` or `aws_region` than default , remember to create your SSM parameters with the matching prefix (e.g., `/staging/nutrition-tracker/...`) and in a matching region.

#### 2.6. Initialize Terraform

Run `terraform init` to download the necessary providers.

```bash
terraform init
```

#### 2.7. Plan and Apply

First, run a plan to see what will be created.
```bash
terraform plan -out=tfplan
```

If the plan looks correct, apply it to create the infrastructure.
```bash
terraform apply tfplan
```

#### 2.8. Set the Webhook

After the apply finishes, Terraform will output the `api_gateway_url`. Use this to set your Telegram webhook. Remember to use the correct bot token.

```bash
curl -F "url=<api_gateway_url>" https://api.telegram.org/bot<YOUR_TELEGRAM_BOT_TOKEN>/setWebhook
```

#### 2.9. Destroy Resources

When you are finished, you can destroy all the created resources from within the `terraform` directory:

```bash
terraform destroy
```