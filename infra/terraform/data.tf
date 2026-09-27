# PostgreSQL 16 on RDS supports pgvector on every 16.x minor version (0.8.2 on 16.13+, per the RDS extension
# release notes retrieved 2026-09-26). Migration 0001 runs CREATE EXTENSION vector as the RDS master user.
resource "aws_db_subnet_group" "main" {
  name       = local.prefix
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "pg16" {
  name   = "${local.prefix}-pg16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
}

resource "aws_db_instance" "main" {
  identifier                            = local.prefix
  engine                                = "postgres"
  engine_version                        = "16"
  auto_minor_version_upgrade            = true
  instance_class                        = var.db_instance_class
  allocated_storage                     = var.db_allocated_storage
  max_allocated_storage                 = var.db_allocated_storage * 5
  storage_type                          = "gp3"
  storage_encrypted                     = true
  db_name                               = "cfs"
  username                              = "cfs_admin"
  manage_master_user_password           = true # password lives in Secrets Manager, rotated by RDS
  db_subnet_group_name                  = aws_db_subnet_group.main.name
  vpc_security_group_ids                = [aws_security_group.db.id]
  parameter_group_name                  = aws_db_parameter_group.pg16.name
  multi_az                              = var.db_multi_az
  publicly_accessible                   = false
  backup_retention_period               = var.db_backup_retention_days
  backup_window                         = "07:00-08:00"
  maintenance_window                    = "sun:08:30-sun:09:30"
  copy_tags_to_snapshot                 = true
  deletion_protection                   = true
  skip_final_snapshot                   = false
  final_snapshot_identifier             = "${local.prefix}-final"
  performance_insights_enabled          = true
  performance_insights_retention_period = 7
  enabled_cloudwatch_logs_exports       = ["postgresql"]
}
