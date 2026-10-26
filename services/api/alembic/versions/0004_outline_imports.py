"""Course outline imports: a reviewed plan per (draft version, course, outline) with an undo log.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

STATUSES = ("queued", "running", "ready", "applied", "discarded", "failed", "reverted")


def upgrade() -> None:
    op.create_table(
        "outline_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("course_entity_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(*STATUSES, name="outlineimportstatus", native_enum=False, create_constraint=True, length=40),
            nullable=False,
        ),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("edition", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("extraction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_run_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=True),
        sa.Column("plan_hash", sa.String(length=64), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("applied_by", sa.Uuid(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverted_by", sa.Uuid(), nullable=True),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name=op.f("fk_outline_imports_organization_id_organizations"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"], ["curriculum_versions.id"],
            name=op.f("fk_outline_imports_curriculum_version_id_curriculum_versions"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["course_entity_id"], ["entities.id"],
            name=op.f("fk_outline_imports_course_entity_id_entities"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["document_versions.id"],
            name=op.f("fk_outline_imports_document_version_id_document_versions"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_outline_imports_created_by_users")),
        sa.ForeignKeyConstraint(["applied_by"], ["users.id"], name=op.f("fk_outline_imports_applied_by_users")),
        sa.ForeignKeyConstraint(["reverted_by"], ["users.id"], name=op.f("fk_outline_imports_reverted_by_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outline_imports")),
    )
    op.create_index(op.f("ix_outline_imports_organization_id"), "outline_imports", ["organization_id"])
    op.create_index(op.f("ix_outline_imports_curriculum_version_id"), "outline_imports", ["curriculum_version_id"])
    op.create_index(op.f("ix_outline_imports_course_entity_id"), "outline_imports", ["course_entity_id"])
    op.create_index(op.f("ix_outline_imports_document_version_id"), "outline_imports", ["document_version_id"])
    op.create_index(
        "ix_outline_imports_course", "outline_imports", ["curriculum_version_id", "course_entity_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_outline_imports_course", table_name="outline_imports")
    for c in ("document_version_id", "course_entity_id", "curriculum_version_id", "organization_id"):
        op.drop_index(op.f(f"ix_outline_imports_{c}"), table_name="outline_imports")
    op.drop_table("outline_imports")
