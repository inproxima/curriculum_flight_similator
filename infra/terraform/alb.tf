resource "random_password" "origin_verify" {
  length  = 40
  special = false
}

resource "aws_lb" "api" {
  name                       = local.prefix
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  idle_timeout               = 120 # job progress streams (SSE) send keepalives well within this
  drop_invalid_header_fields = true
  enable_deletion_protection = true
}

resource "aws_lb_target_group" "api" {
  name                 = "${local.prefix}-api"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30
  health_check {
    path                = "/api/v1/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    matcher             = "200"
  }
}

resource "aws_lb_listener" "api" {
  load_balancer_arn = aws_lb.api.arn
  port              = var.alb_certificate_arn != "" ? 443 : 80
  protocol          = var.alb_certificate_arn != "" ? "HTTPS" : "HTTP"
  certificate_arn   = var.alb_certificate_arn != "" ? var.alb_certificate_arn : null
  ssl_policy        = var.alb_certificate_arn != "" ? "ELBSecurityPolicy-TLS13-1-2-2021-06" : null
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Forbidden"
      status_code  = "403"
    }
  }
}

# Only requests carrying CloudFront's secret origin header reach the API.
resource "aws_lb_listener_rule" "from_cloudfront" {
  listener_arn = aws_lb_listener.api.arn
  priority     = 10
  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [random_password.origin_verify.result]
    }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
