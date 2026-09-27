output "app_url" {
  value = local.app_origin
}
output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}
output "frontend_bucket" {
  value = aws_s3_bucket.frontend.bucket
}
output "documents_bucket" {
  value = aws_s3_bucket.documents.bucket
}
output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.app.id
}
output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}
output "migrate_task_definition" {
  value = aws_ecs_task_definition.migrate.family
}
output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}
output "worker_security_group_id" {
  value = aws_security_group.worker.id
}
output "app_secret_arn" {
  value = aws_secretsmanager_secret.app.arn
}
output "oidc_issuer" {
  value = local.oidc_issuer
}
output "oidc_client_id" {
  value = local.oidc_client_id
}
output "jobs_queue_url" {
  value = aws_sqs_queue.jobs.url
}
output "region" {
  value = var.region
}
