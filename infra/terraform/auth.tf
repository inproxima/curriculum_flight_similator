# Optional Cognito user pool. Group membership maps to application roles (see CFS_OIDC_ROLE_MAP_JSON):
# cfs-admin, cfs-editor, cfs-reviewer, cfs-viewer. Users without a group are denied (fail closed).
resource "aws_cognito_user_pool" "main" {
  count                    = var.create_cognito ? 1 : 0
  name                     = local.prefix
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"
  deletion_protection      = "ACTIVE"
  admin_create_user_config { allow_admin_create_user_only = true }
  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }
  software_token_mfa_configuration { enabled = true }
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  count        = var.create_cognito ? 1 : 0
  domain       = "${local.prefix}-${random_id.suffix.hex}"
  user_pool_id = aws_cognito_user_pool.main[0].id
}

resource "aws_cognito_user_pool_client" "spa" {
  count                                = var.create_cognito ? 1 : 0
  name                                 = "${local.prefix}-spa"
  user_pool_id                         = aws_cognito_user_pool.main[0].id
  generate_secret                      = false # public SPA client (Authorization Code + PKCE)
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["${local.app_origin}/auth/callback"]
  logout_urls                          = [local.app_origin]
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 12
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true
}

resource "aws_cognito_user_group" "roles" {
  for_each     = var.create_cognito ? toset(["cfs-admin", "cfs-editor", "cfs-reviewer", "cfs-viewer"]) : toset([])
  name         = each.key
  user_pool_id = aws_cognito_user_pool.main[0].id
}

locals {
  oidc_issuer    = var.create_cognito ? "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main[0].id}" : var.oidc_issuer
  oidc_client_id = var.create_cognito ? aws_cognito_user_pool_client.spa[0].id : var.oidc_client_id
  cognito_domain = var.create_cognito ? "https://${aws_cognito_user_pool_domain.main[0].domain}.auth.${var.region}.amazoncognito.com" : ""
}
