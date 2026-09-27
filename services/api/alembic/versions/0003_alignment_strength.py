"""Alignment strength on relationship revisions.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("relationship_revisions", sa.Column("strength", sa.Numeric(4, 3), nullable=True))
    op.add_column("relationship_revisions", sa.Column("strength_source", sa.String(20), nullable=True))
    op.add_column("relationship_revisions", sa.Column("model_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_relationship_revisions_model_run_id_model_runs",
        "relationship_revisions",
        "model_runs",
        ["model_run_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_relationship_revisions_strength_range",
        "relationship_revisions",
        "strength IS NULL OR (strength >= 0 AND strength <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_relationship_revisions_strength_range", "relationship_revisions", type_="check")
    op.drop_constraint("fk_relationship_revisions_model_run_id_model_runs", "relationship_revisions", type_="foreignkey")
    for c in ("model_run_id", "strength_source", "strength"):
        op.drop_column("relationship_revisions", c)
