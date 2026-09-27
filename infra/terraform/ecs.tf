resource "aws_ecs_cluster" "main" {
  name = local.prefix
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "app" {
  for_each          = toset(["api", "worker", "migrate"])
  name              = "/cfs/${var.environment}/${each.key}"
  retention_in_days = var.log_retention_days
}

locals {
  image = "${aws_ecr_repository.api.repository_url}:${var.image_tag}"
  app_env = [
    { name = "CFS_ENV", value = "production" },
    { name = "CFS_AUTH_MODE", value = "oidc" },
    { name = "CFS_OIDC_ISSUER", value = local.oidc_issuer },
    { name = "CFS_OIDC_AUDIENCE", value = local.oidc_client_id },
    { name = "CFS_OIDC_CLIENT_ID_PUBLIC", value = local.oidc_client_id },
    { name = "CFS_STORAGE_BACKEND", value = "s3" },
    { name = "CFS_S3_BUCKET", value = aws_s3_bucket.documents.bucket },
    { name = "CFS_S3_REGION", value = var.region },
    { name = "CFS_BROKER_URL", value = "sqs://" },
    { name = "CFS_SQS_QUEUE_URL", value = aws_sqs_queue.jobs.url },
    { name = "CFS_AWS_REGION", value = var.region },
    { name = "AWS_DEFAULT_REGION", value = var.region },
    { name = "CFS_DB_HOST", value = aws_db_instance.main.address },
    { name = "CFS_DB_NAME", value = "cfs" },
    { name = "CFS_CORS_ORIGINS", value = jsonencode([local.app_origin]) },
    { name = "CFS_AI_PROVIDER_ALLOWLIST", value = var.ai_provider_allowlist },
    { name = "CFS_AI_MONTHLY_BUDGET_USD", value = tostring(var.ai_monthly_budget_usd) },
    { name = "CFS_FETCH_ALLOWED_DOMAINS", value = var.fetch_allowed_domains },
  ]
  app_secrets = [
    { name = "CFS_DB_USER", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:username::" },
    { name = "CFS_DB_PASSWORD", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::" },
    { name = "CFS_SECRET_KEY", valueFrom = "${aws_secretsmanager_secret.app.arn}:secret_key::" },
    { name = "CFS_OPENAI_API_KEY", valueFrom = "${aws_secretsmanager_secret.app.arn}:openai_api_key::" },
    { name = "CFS_ANTHROPIC_API_KEY", valueFrom = "${aws_secretsmanager_secret.app.arn}:anthropic_api_key::" },
  ]
  container_common = {
    image                  = local.image
    essential              = true
    environment            = local.app_env
    secrets                = local.app_secrets
    readonlyRootFilesystem = false
    linuxParameters        = { initProcessEnabled = true }
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([merge(local.container_common, {
    name         = "api"
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = { awslogs-group = aws_cloudwatch_log_group.app["api"].name, awslogs-region = var.region,
      awslogs-stream-prefix = "api" }
    }
  })])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.prefix}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([merge(local.container_common, {
    name        = "worker"
    command     = ["celery", "-A", "cfs_worker.app", "worker", "--loglevel=INFO", "--concurrency=2", "--without-heartbeat", "--without-mingle", "--without-gossip"]
    healthCheck = null
    logConfiguration = {
      logDriver = "awslogs"
      options = { awslogs-group = aws_cloudwatch_log_group.app["worker"].name, awslogs-region = var.region,
      awslogs-stream-prefix = "worker" }
    }
  })])
}

# One-off task: `aws ecs run-task --task-definition <family>-migrate ...` before shifting traffic.
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.prefix}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([merge(local.container_common, {
    name    = "migrate"
    command = ["alembic", "upgrade", "head"]
    logConfiguration = {
      logDriver = "awslogs"
      options = { awslogs-group = aws_cloudwatch_log_group.app["migrate"].name, awslogs-region = var.region,
      awslogs-stream-prefix = "migrate" }
    }
  })])
}

resource "aws_ecs_service" "api" {
  name                               = "api"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.api.arn
  desired_count                      = var.api_desired_count
  launch_type                        = "FARGATE"
  health_check_grace_period_seconds  = 60
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true # failed deployments roll back to the previous task definition automatically
  }
  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.api.id]
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  lifecycle { ignore_changes = [desired_count] }
  depends_on = [aws_lb_listener_rule.from_cloudfront]
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.worker.id]
  }
  lifecycle { ignore_changes = [desired_count] }
}

resource "aws_appautoscaling_target" "api" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.api_desired_count
  max_capacity       = var.api_desired_count * 3
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${local.prefix}-api-cpu"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  policy_type        = "TargetTrackingScaling"
  target_tracking_scaling_policy_configuration {
    target_value = 60
    predefined_metric_specification { predefined_metric_type = "ECSServiceAverageCPUUtilization" }
  }
}

resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.worker_desired_count
  max_capacity       = var.worker_desired_count * 4
}

resource "aws_appautoscaling_policy" "worker_queue" {
  name               = "${local.prefix}-worker-queue"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension
  policy_type        = "TargetTrackingScaling"
  target_tracking_scaling_policy_configuration {
    target_value = 5 # visible jobs per worker task
    customized_metric_specification {
      metric_name = "ApproximateNumberOfMessagesVisible"
      namespace   = "AWS/SQS"
      statistic   = "Average"
      dimensions {
        name  = "QueueName"
        value = aws_sqs_queue.jobs.name
      }
    }
  }
}
