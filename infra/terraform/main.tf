locals {
  prefix = "${var.name}-${var.environment}"
  tags = {
    Application = "curriculum-flight-simulator"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)
  app_domain = var.domain_name != "" ? var.domain_name : aws_cloudfront_distribution.app.domain_name
  app_origin = "https://${local.app_domain}"
}

data "aws_availability_zones" "available" {
  state = "available"
}
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
