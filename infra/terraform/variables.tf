variable "region" {
  description = "AWS region for all regional resources. A Canadian region does not determine where external model providers process data."
  type        = string
  default     = "ca-central-1"
}
variable "environment" {
  type    = string
  default = "staging"
}
variable "name" {
  type    = string
  default = "cfs"
}
variable "image_tag" {
  description = "Container image tag in ECR (set by the deploy pipeline)."
  type        = string
  default     = "latest"
}
variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}
variable "single_nat_gateway" {
  description = "One NAT gateway (cheaper) vs one per AZ (resilient). Tasks need egress to model provider APIs."
  type        = bool
  default     = true
}
variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}
variable "db_allocated_storage" {
  type    = number
  default = 20
}
variable "db_multi_az" {
  type    = bool
  default = false
}
variable "db_backup_retention_days" {
  type    = number
  default = 14
}
variable "api_desired_count" {
  type    = number
  default = 2
}
variable "worker_desired_count" {
  type    = number
  default = 1
}
variable "api_cpu" {
  type    = number
  default = 512
}
variable "api_memory" {
  type    = number
  default = 1024
}
variable "worker_cpu" {
  type    = number
  default = 1024
}
variable "worker_memory" {
  type    = number
  default = 2048
}
variable "create_cognito" {
  description = "Create a Cognito user pool for sign-in. Set false to use an institutional OIDC provider."
  type        = bool
  default     = true
}
variable "oidc_issuer" {
  description = "Issuer URL when create_cognito = false."
  type        = string
  default     = ""
}
variable "oidc_client_id" {
  description = "OIDC client id when create_cognito = false."
  type        = string
  default     = ""
}
variable "domain_name" {
  description = "Optional custom domain (e.g. cfs.example.ca). Empty = use the CloudFront domain."
  type        = string
  default     = ""
}
variable "cloudfront_certificate_arn" {
  description = "ACM certificate in us-east-1 for domain_name (required when domain_name is set)."
  type        = string
  default     = ""
}
variable "alb_certificate_arn" {
  description = "Optional regional ACM certificate for the ALB. When set, CloudFront→ALB uses HTTPS."
  type        = string
  default     = ""
}
variable "alarm_email" {
  description = "Optional email for CloudWatch alarm notifications."
  type        = string
  default     = ""
}
variable "ai_provider_allowlist" {
  type    = string
  default = "openai,anthropic"
}
variable "ai_monthly_budget_usd" {
  type    = number
  default = 50
}
variable "fetch_allowed_domains" {
  type    = string
  default = "ucalgary.ca"
}
variable "log_retention_days" {
  type    = number
  default = 30
}
