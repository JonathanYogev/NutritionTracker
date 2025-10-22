variable "aws_region" {
  description = "The AWS region to deploy resources in."
  type        = string
  default     = "us-east-1"
}

variable "dependencies_layer_zip_path" {
  description = "Path to the dependencies_layer.zip file."
  type        = string
  default     = "../lambda_layer/dependencies_layer.zip"
}

variable "env" {
  description = "The environment name to use as a prefix for all resources (e.g., 'dev', 'staging', 'prod')."
  type        = string
  default     = "dev"
}

variable "reporter_schedule_cron" {
  description = "Cron expression for the daily nutrition report (UTC). E.g., 'cron(30 19 * * ? *)' for 19:30 UTC."
  type        = string
  default     = "cron(30 19 * * ? *)"
}

variable "python_runtime" {
  description = "The Python runtime version for Lambda functions and layers."
  type        = string
  default     = "python3.12"
}

variable "timezone" {
  description = "The timezone to be used by the processor and reporter lambdas"
  type        = string
  default     = "Asia/Jerusalem"
}

variable "log_retention_in_days" {
  description = "The number of days to retain CloudWatch logs for Lambda functions."
  type        = number
  default     = 14
}