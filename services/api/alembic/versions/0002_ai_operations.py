"""AI operations: model-run accounting and caching, per-document provider policy.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_runs", sa.Column("output", postgresql.JSONB(), nullable=True))
    op.add_column("model_runs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("model_runs", sa.Column("fallback_from", sa.String(120), nullable=True))
    op.add_column("model_runs", sa.Column("cached_input_tokens", sa.Integer(), nullable=True))
    op.add_column("model_runs", sa.Column("purpose", sa.String(120), nullable=True))
    op.create_index("ix_model_runs_org_created", "model_runs", ["organization_id", "created_at"])
    # null = any allowlisted provider; otherwise explicit list, e.g. ["anthropic"] or [] (never send to a model)
    op.add_column("document_versions", sa.Column("ai_providers", postgresql.JSONB(), nullable=True))
    op.add_column("review_items", sa.Column("model_run_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_items", "model_run_id")
    op.drop_column("document_versions", "ai_providers")
    op.drop_index("ix_model_runs_org_created", table_name="model_runs")
    for c in ("purpose", "cached_input_tokens", "fallback_from", "duration_ms", "output"):
        op.drop_column("model_runs", c)
