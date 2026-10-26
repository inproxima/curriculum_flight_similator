"""AI alignment verdicts: which elements the mapping examined and whether it found a connection.

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alignment_verdicts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("curriculum_version_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("rel_type", sa.String(length=40), nullable=False),
        sa.Column("verdict", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("model_run_id", sa.Uuid(), nullable=True),
        sa.Column("examined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name=op.f("fk_alignment_verdicts_organization_id_organizations"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"], ["curriculum_versions.id"],
            name=op.f("fk_alignment_verdicts_curriculum_version_id_curriculum_versions"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["entities.id"], name=op.f("fk_alignment_verdicts_entity_id_entities"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"], ["model_runs.id"],
            name=op.f("fk_alignment_verdicts_model_run_id_model_runs"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alignment_verdicts")),
        sa.UniqueConstraint(
            "curriculum_version_id", "entity_id", "rel_type",
            name=op.f("uq_alignment_verdicts_curriculum_version_id_entity_id_rel_type"),
        ),
    )
    op.create_index(op.f("ix_alignment_verdicts_organization_id"), "alignment_verdicts", ["organization_id"])
    op.create_index(
        op.f("ix_alignment_verdicts_curriculum_version_id"), "alignment_verdicts", ["curriculum_version_id"]
    )
    op.create_index(op.f("ix_alignment_verdicts_entity_id"), "alignment_verdicts", ["entity_id"])


def downgrade() -> None:
    for c in ("entity_id", "curriculum_version_id", "organization_id"):
        op.drop_index(op.f(f"ix_alignment_verdicts_{c}"), table_name="alignment_verdicts")
    op.drop_table("alignment_verdicts")
