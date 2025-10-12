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