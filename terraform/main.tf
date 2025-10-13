# Terraform Configuration for Nutrition Tracker Application
data "aws_caller_identity" "current" {}

locals {
  client_lambda_ssm_params = [
    "/${var.env}/nutrition-tracker/telegram-bot-token",
    "/${var.env}/nutrition-tracker/telegram-secret-token"
  ]

  processor_lambda_ssm_params = [
    "/${var.env}/nutrition-tracker/telegram-bot-token",
    "/${var.env}/nutrition-tracker/gemini-api-key",
    "/${var.env}/nutrition-tracker/fdc-api-key",
    "/${var.env}/nutrition-tracker/google-sheets-credentials",
    "/${var.env}/nutrition-tracker/spreadsheet-id"
  ]

  reporter_lambda_ssm_params = [
    "/${var.env}/nutrition-tracker/telegram-bot-token",
    "/${var.env}/nutrition-tracker/google-sheets-credentials",
    "/${var.env}/nutrition-tracker/spreadsheet-id",
    "/${var.env}/nutrition-tracker/telegram-chat-id"
  ]
}

# SQS Dead-Letter Queue
resource "aws_sqs_queue" "nutrition_tracker_dlq" {
  name = "${var.env}-nutrition-tracker-dlq"
}

# SQS Main Queue
resource "aws_sqs_queue" "nutrition_tracker_queue" {
  name                       = "${var.env}-nutrition-tracker-queue"
  visibility_timeout_seconds = 330
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.nutrition_tracker_dlq.arn
    maxReceiveCount     = 5
  })
}

# DynamoDB Table for Idempotency
resource "aws_dynamodb_table" "nutrition_tracker_messages" {
  name         = "${var.env}-nutrition-tracker-messages"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "idempotency_key"

  attribute {
    name = "idempotency_key"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

# IAM Roles and Policies


resource "aws_iam_role" "client_lambda_role" {
  name = "${var.env}-nutrition-tracker-client-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "client_lambda_policy" {
  name = "${var.env}-nutrition-tracker-client-lambda-policy"
  role = aws_iam_role.client_lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowSSMParameterAccess"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = [for p in local.client_lambda_ssm_params : "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${p}"]
      },
      {
        Sid    = "AllowSQSQueueAccess"
        Effect = "Allow"
        Action = ["sqs:SendMessage"]
        Resource = [
          aws_sqs_queue.nutrition_tracker_queue.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "client_lambda_basic_execution" {
  role       = aws_iam_role.client_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}


resource "aws_iam_role" "processor_lambda_role" {
  name = "${var.env}-nutrition-tracker-processor-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "processor_lambda_policy" {
  name = "${var.env}-nutrition-tracker-processor-lambda-policy"
  role = aws_iam_role.processor_lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowSSMParameterAccess"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = [for p in local.processor_lambda_ssm_params : "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${p}"]
      },
      {
        Sid    = "AllowSQSQueueAccess"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = [
          aws_sqs_queue.nutrition_tracker_queue.arn
        ]
      },
      {
        Sid    = "AllowDynamoDBIdempotencyTableAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem"
        ]
        Resource = [
          aws_dynamodb_table.nutrition_tracker_messages.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "processor_lambda_basic_execution" {
  role       = aws_iam_role.processor_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}


resource "aws_iam_role" "reporter_lambda_role" {
  name = "${var.env}-nutrition-tracker-reporter-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "reporter_lambda_policy" {
  name = "${var.env}-nutrition-tracker-reporter-lambda-policy"
  role = aws_iam_role.reporter_lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowSSMParameterAccess"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = [for p in local.reporter_lambda_ssm_params : "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${p}"]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "reporter_lambda_basic_execution" {
  role       = aws_iam_role.reporter_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Lambda Layer
resource "aws_lambda_layer_version" "dependencies_layer" {
  layer_name          = "${var.env}-nutrition-tracker-dependencies"
  filename            = var.dependencies_layer_zip_path
  compatible_runtimes = [var.python_runtime]
}

# Lambda Functions

data "archive_file" "client_lambda" {
  type        = "zip"
  source_file = "../client_lambda.py"
  output_path = "client_lambda.zip"
}

resource "aws_lambda_function" "client_lambda" {
  function_name    = "${var.env}-nutrition-tracker-client"
  role             = aws_iam_role.client_lambda_role.arn
  handler          = "client_lambda.lambda_handler"
  runtime          = var.python_runtime
  filename         = data.archive_file.client_lambda.output_path
  source_code_hash = data.archive_file.client_lambda.output_base64sha256
  timeout          = 10

  layers = [aws_lambda_layer_version.dependencies_layer.arn]

  environment {
    variables = {
      TELEGRAM_BOT_TOKEN_SSM_PATH    = "/${var.env}/nutrition-tracker/telegram-bot-token"
      TELEGRAM_SECRET_TOKEN_SSM_PATH = "/${var.env}/nutrition-tracker/telegram-secret-token"
      SQS_QUEUE_URL                  = aws_sqs_queue.nutrition_tracker_queue.id
    }
  }
}

data "archive_file" "processor_lambda" {
  type        = "zip"
  source_file = "../processor_lambda.py"
  output_path = "processor_lambda.zip"
}

resource "aws_lambda_function" "processor_lambda" {
  function_name    = "${var.env}-nutrition-tracker-processor"
  role             = aws_iam_role.processor_lambda_role.arn
  handler          = "processor_lambda.lambda_handler"
  runtime          = var.python_runtime
  filename         = data.archive_file.processor_lambda.output_path
  source_code_hash = data.archive_file.processor_lambda.output_base64sha256
  timeout          = 300
  memory_size      = 512

  layers = [aws_lambda_layer_version.dependencies_layer.arn]

  environment {
    variables = {
      TELEGRAM_BOT_TOKEN_SSM_PATH        = "/${var.env}/nutrition-tracker/telegram-bot-token"
      GEMINI_API_KEY_SSM_PATH            = "/${var.env}/nutrition-tracker/gemini-api-key"
      FDC_API_KEY_SSM_PATH               = "/${var.env}/nutrition-tracker/fdc-api-key"
      GOOGLE_SHEETS_CREDENTIALS_SSM_PATH = "/${var.env}/nutrition-tracker/google-sheets-credentials"
      SPREADSHEET_ID_SSM_PATH            = "/${var.env}/nutrition-tracker/spreadsheet-id"
      DYNAMODB_TABLE_NAME                = aws_dynamodb_table.nutrition_tracker_messages.name
    }
  }
}

resource "aws_lambda_event_source_mapping" "processor_trigger" {
  event_source_arn = aws_sqs_queue.nutrition_tracker_queue.arn
  function_name    = aws_lambda_function.processor_lambda.arn
}

data "archive_file" "reporter_lambda" {
  type        = "zip"
  source_file = "../reporter_lambda.py"
  output_path = "reporter_lambda.zip"
}

resource "aws_lambda_function" "reporter_lambda" {
  function_name    = "${var.env}-nutrition-tracker-reporter"
  role             = aws_iam_role.reporter_lambda_role.arn
  handler          = "reporter_lambda.lambda_handler"
  runtime          = var.python_runtime
  filename         = data.archive_file.reporter_lambda.output_path
  source_code_hash = data.archive_file.reporter_lambda.output_base64sha256
  timeout          = 30

  layers = [aws_lambda_layer_version.dependencies_layer.arn]

  environment {
    variables = {
      TELEGRAM_BOT_TOKEN_SSM_PATH        = "/${var.env}/nutrition-tracker/telegram-bot-token"
      GOOGLE_SHEETS_CREDENTIALS_SSM_PATH = "/${var.env}/nutrition-tracker/google-sheets-credentials"
      SPREADSHEET_ID_SSM_PATH            = "/${var.env}/nutrition-tracker/spreadsheet-id"
      TELEGRAM_CHAT_ID_SSM_PATH          = "/${var.env}/nutrition-tracker/telegram-chat-id"
    }
  }
}

# EventBridge Rule for Reporter Lambda
resource "aws_cloudwatch_event_rule" "reporter_rule" {
  name                = "${var.env}-daily-nutrition-report-trigger"
  schedule_expression = var.reporter_schedule_cron
}

resource "aws_cloudwatch_event_target" "reporter_target" {
  rule      = aws_cloudwatch_event_rule.reporter_rule.name
  target_id = "${var.env}-nutrition-tracker-reporter"
  arn       = aws_lambda_function.reporter_lambda.arn
}

resource "aws_lambda_permission" "allow_cloudwatch_to_call_reporter" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.reporter_lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reporter_rule.arn
}

# API Gateway
resource "aws_apigatewayv2_api" "http_api" {
  name          = "${var.env}-nutrition-tracker-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id           = aws_apigatewayv2_api.http_api.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.client_lambda.invoke_arn
}

resource "aws_apigatewayv2_route" "webhook_route" {
  api_id    = aws_apigatewayv2_api.http_api.id
  route_key = "POST /webhook"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http_api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "api_gateway_permission" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.client_lambda.function_name
  principal     = "apigateway.amazonaws.com"

  source_arn = "${aws_apigatewayv2_api.http_api.execution_arn}/*/*"
}