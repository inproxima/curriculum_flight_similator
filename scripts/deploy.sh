#!/usr/bin/env bash
# Deploy a new version to an EXISTING AWS environment created with infra/terraform.
# This script creates billable resources only via `terraform apply`, which asks for confirmation.
# Usage: scripts/deploy.sh <environment> [image_tag]      (run from the repository root)
set -euo pipefail
ENVIRONMENT="${1:?environment (e.g. staging)}"
TAG="${2:-$(git rev-parse --short=12 HEAD)}"
TF="terraform -chdir=infra/terraform"
VARS="-var-file=${ENVIRONMENT}.tfvars -var=image_tag=${TAG}"

REGION=$($TF output -raw region)
REPO=$($TF output -raw ecr_repository_url)
echo "==> Building and pushing ${REPO}:${TAG} (linux/arm64)"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%/*}"
docker buildx build --platform linux/arm64 -f infra/Dockerfile.api -t "${REPO}:${TAG}" --push .

echo "==> Registering task definitions for ${TAG} (review the plan)"
$TF apply $VARS -target=aws_ecs_task_definition.migrate -target=aws_ecs_task_definition.api \
  -target=aws_ecs_task_definition.worker

echo "==> Running database migrations"
CLUSTER=$($TF output -raw ecs_cluster)
SUBNETS=$($TF output -json private_subnet_ids | tr -d '[]" ')
SG=$($TF output -raw worker_security_group_id)
TASK=$(aws ecs run-task --cluster "$CLUSTER" --launch-type FARGATE --task-definition "$($TF output -raw migrate_task_definition)" \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SG}],assignPublicIp=DISABLED}" \
  --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK"
CODE=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK" --query 'tasks[0].containers[0].exitCode' --output text)
[ "$CODE" = "0" ] || { echo "Migration failed (exit $CODE); services NOT updated. See CloudWatch /cfs/${ENVIRONMENT}/migrate"; exit 1; }

echo "==> Rolling out API and worker (ECS circuit breaker rolls back automatically on failure)"
$TF apply $VARS

echo "==> Publishing the web app"
( cd apps/web && pnpm install --frozen-lockfile && pnpm build )
aws s3 sync apps/web/dist "s3://$($TF output -raw frontend_bucket)" --delete \
  --cache-control "public,max-age=31536000,immutable" --exclude index.html
aws s3 cp apps/web/dist/index.html "s3://$($TF output -raw frontend_bucket)/index.html" --cache-control "no-cache"
aws cloudfront create-invalidation --distribution-id "$($TF output -raw cloudfront_distribution_id)" --paths /index.html >/dev/null
echo "==> Deployed ${TAG} to $($TF output -raw app_url)"
