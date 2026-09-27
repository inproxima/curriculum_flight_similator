terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = ">= 5.70, < 7.0" }
    random = { source = "hashicorp/random", version = ">= 3.6" }
  }
  # Configure a remote backend before real use, e.g.:
  # backend "s3" { bucket = "<state-bucket>" key = "cfs/<env>.tfstate" region = "ca-central-1" use_lockfile = true }
}

provider "aws" {
  region = var.region
  default_tags { tags = local.tags }
}

# CloudFront requires ACM certificates in us-east-1 (only used when a custom domain is configured).
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
  default_tags { tags = local.tags }
}
