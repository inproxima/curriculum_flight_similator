# Deploying to AWS

Infrastructure is defined in [`infra/terraform`](../infra/terraform). It has passed `terraform validate` but has
**not been applied**: no AWS resources were created while building it. Applying it creates billable resources in
your account. Review the plan and the cost notes first.

## What gets created
| Component | AWS service | Notes |
|---|---|---|
| Web app | Private S3 bucket behind CloudFront (Origin Access Control) | SPA routes via a CloudFront Function; strict CSP and security headers |
| API | ECS Fargate (ARM64) behind an ALB | ALB accepts only CloudFront origin-facing IPs **and** a secret origin header; `/api/*` routed by CloudFront, not cached |
| Worker | ECS Fargate | Celery on SQS; scales on queue depth |
| Migrations | One-off ECS task (`alembic upgrade head`) | Run before each rollout; `scripts/deploy.sh` does this |
| Database | RDS PostgreSQL 16 | pgvector supported on every 16.x minor version (0.8.2 on 16.13+, per the RDS extension release notes retrieved 2026-09-26). Encrypted, TLS forced, managed master password in Secrets Manager, 14-day PITR backups, deletion protection, final snapshot |
| Documents and exports | Private S3, versioned, SSE, TLS-only policy | Content-addressed keys under `orgs/<org>/…` |
| Job broker | SQS with a dead-letter queue | 1-hour visibility timeout. Job state, progress and cancellation live in PostgreSQL; duplicate delivery is safe |
| Secrets | Secrets Manager | App secret (signing key, provider API keys) set out-of-band, never in Terraform state |
| Sign-in | Cognito (optional) or institutional OIDC | Groups `cfs-admin/editor/reviewer/viewer` map to roles; users without a group are denied |
| Monitoring | CloudWatch logs (30 days), Container Insights, alarms → SNS | 5xx, unhealthy tasks, dead-letter queue, database CPU and storage |

Region defaults to `ca-central-1`. **Choosing a Canadian region does not determine where OpenAI or Anthropic
process the excerpts sent to them.** Model provider hosting is a separate decision. Use the provider allowlist and
per-document AI policy to control what is sent.

## First deployment
1. **Remote state.** Configure the S3 backend in `infra/terraform/versions.tf`.
2. **Variables.** Copy `terraform.tfvars.example` to `staging.tfvars` and edit it.
3. **Bootstrap the repository and secret container:**
   ```bash
   terraform -chdir=infra/terraform init
   terraform -chdir=infra/terraform apply -var-file=staging.tfvars \
     -target=aws_ecr_repository.api -target=aws_secretsmanager_secret.app
   ```
4. **Set the app secret.** Use a long random signing key plus your provider keys:
   ```bash
   aws secretsmanager put-secret-value --secret-id "$(terraform -chdir=infra/terraform output -raw app_secret_arn)" \
     --secret-string '{"secret_key":"'"$(openssl rand -hex 32)"'","openai_api_key":"…","anthropic_api_key":"…"}'
   ```
5. **Deploy.** `scripts/deploy.sh staging` builds and pushes the image, registers task definitions, runs migrations,
   rolls out the services, and publishes the web app. Each `terraform apply` shows its plan and asks for
   confirmation.
6. **Add users** in Cognito (or your IdP) and put them in one of the four `cfs-*` groups.
7. **Seed data (optional).** Real documents are imported through the UI. The synthetic fixture is for development
   and should not be seeded in production.

## Subsequent deployments
`scripts/deploy.sh <env> [git-sha]`. The ECS deployment circuit breaker rolls back automatically if new tasks fail
health checks. See [operations.md](operations.md) for rollback, restore, and incident procedures.

## Rough monthly cost (staging defaults)
Order-of-magnitude only. **Verify with the AWS Pricing Calculator for your region before applying.**

- **Largest fixed items:** the NAT gateway, RDS `db.t4g.small`, three Fargate tasks (2 API, 1 worker), and the
  ALB. Together roughly **US$150–250/month** before traffic.
- **Smaller items:** CloudFront, S3, SQS, Secrets Manager and CloudWatch are small at this scale.
- **Model provider usage** is billed separately by OpenAI and Anthropic, and is capped in the app by
  `CFS_AI_MONTHLY_BUDGET_USD`.
- **Production:** Multi-AZ RDS (`db_multi_az = true`) and one NAT gateway per AZ (`single_nat_gateway = false`)
  roughly double the fixed networking and database cost.

## Security notes
- **CloudFront → ALB:** traffic is HTTP unless `alb_certificate_arn` is set. It stays inside AWS networks, but set a
  certificate for end-to-end TLS.
- **Origin header:** the secret origin-verification header value is kept in Terraform state. Protect the state
  bucket accordingly.
- **Rate limits:** these are per API task (in-process). Model spend is independently capped per job and per month.
  Add AWS WAF (managed rule groups plus rate-based rules) on the CloudFront distribution for internet-facing
  deployments. It is not included here.
