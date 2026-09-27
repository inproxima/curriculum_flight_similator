# Application secrets. Terraform creates the container only; set the value out-of-band so secrets never
# enter Terraform state:
#   aws secretsmanager put-secret-value --secret-id <arn> --secret-string \
#     '{"secret_key":"<64 random chars>","openai_api_key":"...","anthropic_api_key":"..."}'
resource "aws_secretsmanager_secret" "app" {
  name                    = "${local.prefix}/app"
  description             = "CFS application secrets (session signing key, model provider API keys)"
  recovery_window_in_days = 7
}
