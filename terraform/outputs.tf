output "api_gateway_url" {
  description = "The URL of the API Gateway to set as the Telegram webhook."
  value       = "${aws_apigatewayv2_stage.default.invoke_url}webhook"
}
