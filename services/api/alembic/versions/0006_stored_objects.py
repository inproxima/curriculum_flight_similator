"""Stored objects: original files kept in PostgreSQL when CFS_STORAGE_BACKEND=db (single-service hosting).

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stored_objects",
        sa.Column("key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=200), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_stored_objects")),
    )


def downgrade() -> None:
    op.drop_table("stored_objects")
