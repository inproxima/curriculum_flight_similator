"""Application configuration. All settings come from environment variables (see .env.example)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), env_prefix="CFS_", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://cfs:cfs@localhost:5433/cfs"
    broker_url: str = "redis://localhost:6380/0"
    # When true, Celery tasks execute inline (tests, or running without a worker).
    task_always_eager: bool = False

    storage_backend: Literal["local", "s3"] = "local"
    storage_root: str = "./data/objects"
    s3_bucket: str | None = None
    s3_region: str | None = None

    # Local single-user mode. Refused when env=production (fails closed).
    auth_mode: Literal["local_single_user", "oidc"] = "local_single_user"
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
        if self.env == "production" and self.auth_mode == "local_single_user":
            raise RuntimeError("local_single_user auth mode is not permitted when CFS_ENV=production")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.validate_for_runtime()
    return s
