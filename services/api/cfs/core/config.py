"""Application configuration. All settings come from environment variables (see .env.example)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), env_prefix="CFS_", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://cfs:cfs@localhost:5433/cfs"
    # Alternative to database_url (ECS injects the RDS-managed password from Secrets Manager separately)
    db_host: str | None = None
    db_port: int = 5432
    db_name: str = "cfs"
    db_user: str | None = None
    db_password: str | None = None
    db_sslmode: str = "require"
    broker_url: str = "redis://localhost:6380/0"  # production: "sqs://" (IAM role credentials)
    aws_region: str = "ca-central-1"
    sqs_queue_url: str | None = None  # predefined queue (no ListQueues/CreateQueue permissions needed)
    sqs_visibility_timeout: int = 3600  # must exceed the longest job; duplicate delivery is still safe
    # When true, Celery tasks execute inline (tests, or running without a worker).
    task_always_eager: bool = False

    storage_backend: Literal["local", "s3"] = "local"
    storage_root: str = "./data/objects"
    s3_bucket: str | None = None
    s3_region: str | None = None

    # Local single-user mode. Refused when env=production (fails closed).
    auth_mode: Literal["local_single_user", "oidc"] = "local_single_user"
    # Signs short-lived stream tokens. MUST be set (≥32 chars) in production.
    secret_key: str = "dev-insecure-secret-change-me-dev-insecure-secret"
    # OIDC (Amazon Cognito or institutional IdP)
    oidc_issuer: str | None = None  # e.g. https://cognito-idp.ca-central-1.amazonaws.com/<pool-id>
    oidc_audience: str | None = None  # app client id
    oidc_jwks_url: str | None = None  # default: discovered from the issuer
    oidc_groups_claim: str = "cognito:groups"
    oidc_role_map_json: str = (
        '{"cfs-admin": "admin", "cfs-editor": "editor", "cfs-reviewer": "reviewer", "cfs-viewer": "viewer"}'
    )
    oidc_org_slug: str = "default"
    oidc_org_name: str = "Default workspace"
    # Browser-facing OIDC settings served to the SPA at /api/v1/auth/config
    oidc_client_id_public: str | None = None
    oidc_scopes: str = "openid email profile"
    # Controlled URL fetcher (webpage import)
    fetch_allowed_domains: str = "ucalgary.ca"  # comma-separated suffixes; empty = any public host
    fetch_max_bytes: int = 5 * 1024 * 1024
    # Rate limits (requests per minute per user; in-process per API task)
    rate_limit_ai_per_minute: int = 20
    rate_limit_upload_per_minute: int = 30
    bind_host: str = "127.0.0.1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])

    max_upload_bytes: int = 50 * 1024 * 1024

    # Provider credentials stay on the backend only.
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    # Providers that workspace content may be sent to (comma-separated). Removing one disables it everywhere.
    ai_provider_allowlist: str = "openai,anthropic"
    # Optional JSON route overrides, e.g. {"extract": {"provider": "anthropic", "model": "claude-sonnet-5"}}
    ai_routes_json: str | None = None
    # Optional JSON overriding per-model USD prices per 1M tokens: {"gpt-6-sol": {"input": 2, "output": 10}}
    ai_prices_json: str | None = None
    ai_max_cost_per_job_usd: float = 2.0
    ai_monthly_budget_usd: float = 50.0
    ai_timeout_seconds: float = 180.0
    ai_max_retries: int = 2
    ai_max_concurrency: int = 4
    ai_embeddings_enabled: bool = True

    def validate_for_runtime(self) -> None:
        """Fail closed: production refuses insecure configurations at startup."""
        if self.env != "production":
            return
        problems = []
        if self.auth_mode == "local_single_user":
            problems.append("local_single_user auth mode is not permitted when CFS_ENV=production")
        if self.auth_mode == "oidc" and not (self.oidc_issuer and self.oidc_audience):
            problems.append("CFS_OIDC_ISSUER and CFS_OIDC_AUDIENCE are required for OIDC")
        if self.secret_key.startswith("dev-insecure") or len(self.secret_key) < 32:
            problems.append("CFS_SECRET_KEY must be set to a random value of at least 32 characters")
        if self.storage_backend != "s3":
            problems.append("CFS_STORAGE_BACKEND must be s3 in production")
        if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origins):
            problems.append("CFS_CORS_ORIGINS must not include localhost in production")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))

    def model_post_init(self, __context) -> None:
        if self.db_host and self.db_user and self.db_password:
            from urllib.parse import quote

            user, pw = quote(self.db_user, safe=""), quote(self.db_password, safe="")
            host = f"{self.db_host}:{self.db_port}/{self.db_name}"
            self.database_url = f"postgresql+psycopg://{user}:{pw}@{host}?sslmode={self.db_sslmode}"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.validate_for_runtime()
    return s
