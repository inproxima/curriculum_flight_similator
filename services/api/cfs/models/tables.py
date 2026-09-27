"""Relational schema (spec §6). Grouped by table group; see docs/domain-model.md for semantics.

Integrity rules that live in the database, not only in services:
- entity_revisions and relationship_revisions are immutable (trigger in migration 0001).
- Memberships and placements of a *published* curriculum version cannot change (trigger).
- Every scoped row carries organization_id; services filter on it (cfs.core.scope).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cfs.models.base import Base, str_enum, ts_now, uuid_pk
from cfs.models.enums import (
    ApprovalStatus,
    ConsequenceClass,
    ContributionLevel,
    DocumentType,
    EntityType,
    EvidenceBasis,
    EvidenceStance,
    JobStatus,
    OcrState,
    Origin,
    PlacementClass,
    ProcessingStatus,
    RelType,
    RequirementGroupKind,
    ReviewItemKind,
    ReviewItemStatus,
    ReviewState,
    Role,
    RuleKind,
    ScenarioState,
    Severity,
    SourceAuthority,
    Term,
    VersionStatus,
)

EMBEDDING_DIM = 1536


def org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False)


# ───────────────────────────── Identity and scope ─────────────────────────────


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = ts_now()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = ts_now()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = mapped_column(str_enum(Role))
    created_at: Mapped[datetime] = ts_now()


class Program(Base):
    __tablename__ = "programs"
    __table_args__ = (UniqueConstraint("organization_id", "code"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(300))
    institution: Mapped[str | None] = mapped_column(String(300))
    parent_degree: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts_now()


class Pathway(Base):
    __tablename__ = "pathways"
    __table_args__ = (UniqueConstraint("program_id", "code"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(300))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class CurriculumVersion(Base):
    __tablename__ = "curriculum_versions"
    __table_args__ = (UniqueConstraint("program_id", "label"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    academic_year: Mapped[str | None] = mapped_column(String(20))
    cohort: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[VersionStatus] = mapped_column(str_enum(VersionStatus), default=VersionStatus.draft)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("curriculum_versions.id"))
    published_from_scenario_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = ts_now()


# ───────────────────────────────── Documents ─────────────────────────────────


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    title: Mapped[str] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[DocumentType] = mapped_column(str_enum(DocumentType), default=DocumentType.other)
    created_at: Mapped[datetime] = ts_now()
    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("organization_id", "sha256"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    object_key: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    original_filename: Mapped[str | None] = mapped_column(String(300))
    # Distinct dates — never conflate them.
    publication_date: Mapped[date | None] = mapped_column(Date)
    retrieval_date: Mapped[date | None] = mapped_column(Date)
    academic_year: Mapped[str | None] = mapped_column(String(20))
    cohort_applicability: Mapped[str | None] = mapped_column(String(200))
    authority_note: Mapped[str | None] = mapped_column(String(300))
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        str_enum(ProcessingStatus), default=ProcessingStatus.uploaded
    )
    page_count: Mapped[int | None] = mapped_column(Integer)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = ts_now()
    document: Mapped[Document] = relationship(back_populates="versions")


class CurriculumSource(Base):
    """Explicit assignment of a document version to a curriculum version. Never inferred."""

    __tablename__ = "curriculum_sources"
    __table_args__ = (UniqueConstraint("document_version_id", "curriculum_version_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"), index=True
    )
    applicability: Mapped[str | None] = mapped_column(Text)
    authority: Mapped[SourceAuthority] = mapped_column(str_enum(SourceAuthority))
    approval_status: Mapped[ApprovalStatus] = mapped_column(str_enum(ApprovalStatus), default=ApprovalStatus.proposed)
    is_exploratory_cross_version: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = ts_now()


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_version_id", "page_number"),
        Index("ix_document_pages_tsv", "tsv", postgresql_using="gin"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)  # 1-based
    text: Mapped[str] = mapped_column(Text)  # original extracted text
    normalized_text: Mapped[str] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(60))
    parser_version: Mapped[str] = mapped_column(String(40))
    char_count: Mapped[int] = mapped_column(Integer)
    ocr_state: Mapped[OcrState] = mapped_column(str_enum(OcrState), default=OcrState.not_needed)
    width: Mapped[float | None] = mapped_column(Numeric(10, 2))
    height: Mapped[float | None] = mapped_column(Numeric(10, 2))
    # Per-character boxes aligned with `text` offsets: [[x0, top, x1, bottom], ...] (null for OCR/DOCX)
    char_boxes: Mapped[list | None] = mapped_column(JSONB)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('english', normalized_text)", persisted=True))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (Index("ix_document_chunks_tsv", "tsv", postgresql_using="gin"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    section_title: Mapped[str | None] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('english', text)", persisted=True))


class ChunkEmbedding(Base):
    """One embedding space per (model, dimension). Never mix configurations in a search."""

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "embedding_model", "dimension"),
        Index(
            "ix_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_chunks.id", ondelete="CASCADE"), index=True)
    embedding_model: Mapped[str] = mapped_column(String(100))
    dimension: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = ts_now()


class EvidenceSpan(Base):
    """Immutable pointer into an exact document version."""

    __tablename__ = "evidence_spans"
    __table_args__ = (CheckConstraint("char_end > char_start", name="span_order"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)  # offsets into document_pages.text
    char_end: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)
    bbox: Mapped[list | None] = mapped_column(JSONB)  # list of [x0, top, x1, bottom] line boxes, PDF points
    created_at: Mapped[datetime] = ts_now()


class IngestStageRun(Base):
    """Idempotency record for pipeline stages, keyed on (document version, stage, parser version)."""

    __tablename__ = "ingest_stage_runs"
    __table_args__ = (UniqueConstraint("document_version_id", "stage", "parser_version"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(60))
    parser_version: Mapped[str] = mapped_column(String(40))
    output: Mapped[dict | None] = mapped_column(JSONB)
    completed_at: Mapped[datetime] = ts_now()


# ───────────────────────────── Curriculum entities ─────────────────────────────


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("organization_id", "entity_type", "stable_key"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    entity_type: Mapped[EntityType] = mapped_column(str_enum(EntityType), index=True)
    # Human-meaningful identity, e.g. a course code "MDSC 407" or a slug for outcomes/topics.
    stable_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = ts_now()


class EntityRevision(Base):
    __tablename__ = "entity_revisions"
    __table_args__ = (
        UniqueConstraint("entity_id", "revision_number"),
        Index("ix_entity_revisions_fts", "tsv", postgresql_using="gin"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    origin: Mapped[Origin] = mapped_column(str_enum(Origin))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = ts_now()
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(title,'') || ' ' || coalesce(description,''))", persisted=True),
    )


class CurriculumEntityMembership(Base):
    __tablename__ = "curriculum_entity_memberships"
    __table_args__ = (UniqueConstraint("curriculum_version_id", "entity_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    entity_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entity_revisions.id"), index=True)


class CourseRevisionDetails(Base):
    __tablename__ = "course_revision_details"
    entity_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entity_revisions.id", ondelete="CASCADE"), primary_key=True
    )
    course_code: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(500))
    credits: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))  # only when documented


class CoursePlacement(Base):
    __tablename__ = "course_placements"
    __table_args__ = (
        UniqueConstraint("curriculum_version_id", "entity_id", "pathway_id", postgresql_nulls_not_distinct=True),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    entity_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entity_revisions.id"))
    year: Mapped[int | None] = mapped_column(Integer)
    term: Mapped[Term] = mapped_column(str_enum(Term), default=Term.unknown)
    pathway_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pathways.id"))
    classification: Mapped[PlacementClass] = mapped_column(str_enum(PlacementClass))
    offering_conditions: Mapped[str | None] = mapped_column(Text)


class OutcomeRevisionDetails(Base):
    __tablename__ = "outcome_revision_details"
    entity_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entity_revisions.id", ondelete="CASCADE"), primary_key=True
    )
    scope: Mapped[str] = mapped_column(String(20))  # program | course
    statement_verbatim: Mapped[str | None] = mapped_column(Text)  # exactly as documented, if documented
    normalized_label: Mapped[str] = mapped_column(String(300))


class AssessmentRevisionDetails(Base):
    __tablename__ = "assessment_revision_details"
    entity_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entity_revisions.id", ondelete="CASCADE"), primary_key=True
    )
    format: Mapped[str | None] = mapped_column(String(120))
    weight_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    timing_week: Mapped[int | None] = mapped_column(Integer)
    timing_text: Mapped[str | None] = mapped_column(String(200))
    documented_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    estimated_hours_low: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    estimated_hours_typical: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    estimated_hours_high: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))


class TopicRevisionDetails(Base):
    __tablename__ = "topic_revision_details"
    entity_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entity_revisions.id", ondelete="CASCADE"), primary_key=True
    )
    area: Mapped[str | None] = mapped_column(String(200))
    level: Mapped[str | None] = mapped_column(String(40))  # introductory | intermediate | advanced


class ActivityRevisionDetails(Base):
    __tablename__ = "activity_revision_details"
    entity_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entity_revisions.id", ondelete="CASCADE"), primary_key=True
    )
    delivery_format: Mapped[str | None] = mapped_column(String(120))
    contact_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))


class RequirementGroup(Base):
    __tablename__ = "requirement_groups"
    __table_args__ = (UniqueConstraint("curriculum_version_id", "code"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[RequirementGroupKind] = mapped_column(str_enum(RequirementGroupKind))
    min_courses: Mapped[int | None] = mapped_column(Integer)
    min_credits: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    member_entity_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list)
    description: Mapped[str | None] = mapped_column(Text)


class RequirementRule(Base):
    """A documented requisite as an explicit expression (see cfs.curriculum.rules)."""

    __tablename__ = "requirement_rules"
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"), index=True
    )
    target_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    rule_kind: Mapped[RuleKind] = mapped_column(str_enum(RuleKind))
    expression: Mapped[dict] = mapped_column(JSONB)
    source_text: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[Origin] = mapped_column(str_enum(Origin))
    evidence_basis: Mapped[EvidenceBasis] = mapped_column(str_enum(EvidenceBasis))
    review_state: Mapped[ReviewState] = mapped_column(str_enum(ReviewState))
    evidence_span_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence_spans.id"))
    created_at: Mapped[datetime] = ts_now()


# ─────────────────────────────── Relationships ───────────────────────────────


class RelationshipRevision(Base):
    __tablename__ = "relationship_revisions"
    __table_args__ = (
        UniqueConstraint("relationship_key", "revision_number"),
        Index("ix_rel_rev_source_type", "source_entity_id", "rel_type"),
        Index("ix_rel_rev_target_type", "target_entity_id", "rel_type"),
        CheckConstraint("source_entity_id <> target_entity_id", name="no_self_loop"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    relationship_key: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # stable identity across revisions
    revision_number: Mapped[int] = mapped_column(Integer)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    target_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    rel_type: Mapped[RelType] = mapped_column(str_enum(RelType))
    scope: Mapped[str | None] = mapped_column(String(200))  # e.g. pathway code this applies to
    origin: Mapped[Origin] = mapped_column(str_enum(Origin))
    evidence_basis: Mapped[EvidenceBasis] = mapped_column(str_enum(EvidenceBasis))
    review_state: Mapped[ReviewState] = mapped_column(str_enum(ReviewState))
    contribution_level: Mapped[ContributionLevel | None] = mapped_column(str_enum(ContributionLevel))
    requirement_rule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requirement_rules.id"))
    rationale: Mapped[str | None] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = ts_now()


class CurriculumRelationshipMembership(Base):
    __tablename__ = "curriculum_relationship_memberships"
    __table_args__ = (UniqueConstraint("curriculum_version_id", "relationship_key"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"), index=True
    )
    relationship_key: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    relationship_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("relationship_revisions.id"), index=True)


class RelationshipEvidence(Base):
    __tablename__ = "relationship_evidence"
    __table_args__ = (UniqueConstraint("relationship_revision_id", "evidence_span_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    relationship_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("relationship_revisions.id", ondelete="CASCADE"), index=True
    )
    evidence_span_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence_spans.id"), index=True)
    stance: Mapped[EvidenceStance] = mapped_column(str_enum(EvidenceStance), default=EvidenceStance.supporting)
    note: Mapped[str | None] = mapped_column(Text)


class EntityFieldEvidence(Base):
    __tablename__ = "entity_field_evidence"
    __table_args__ = (UniqueConstraint("entity_revision_id", "field_name", "evidence_span_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    entity_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("entity_revisions.id", ondelete="CASCADE"), index=True
    )
    field_name: Mapped[str] = mapped_column(String(80))
    evidence_span_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence_spans.id"), index=True)
    stance: Mapped[EvidenceStance] = mapped_column(str_enum(EvidenceStance), default=EvidenceStance.supporting)


# ───────────────────────────────── Scenarios ─────────────────────────────────


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    base_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("curriculum_versions.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    state: Mapped[ScenarioState] = mapped_column(str_enum(ScenarioState), default=ScenarioState.draft)
    revision: Mapped[int] = mapped_column(Integer, default=0)  # bumps on every edit (optimistic concurrency)
    head: Mapped[int] = mapped_column(Integer, default=0)  # number of applied changes (undo pointer)
    workload_assumptions: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = ts_now()
    updated_at: Mapped[datetime] = ts_now()


class ScenarioChange(Base):
    __tablename__ = "scenario_changes"
    __table_args__ = (UniqueConstraint("scenario_id", "seq"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # 1-based order
    op_type: Mapped[str] = mapped_column(String(60))
    target_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict] = mapped_column(JSONB)  # typed op body (validated by cfs.scenarios.ops)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    assumptions: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = ts_now()


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (Index("ix_analysis_runs_hash", "input_hash"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("curriculum_versions.id"), index=True)
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"), index=True)
    scenario_revision: Mapped[int | None] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    algorithm_version: Mapped[str] = mapped_column(String(40))
    model_config_: Mapped[dict | None] = mapped_column("model_config", JSONB)
    status: Mapped[JobStatus] = mapped_column(str_enum(JobStatus), default=JobStatus.queued)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = ts_now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[uuid.UUID] = uuid_pk()
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(80))
    severity: Mapped[Severity] = mapped_column(str_enum(Severity))
    consequence_class: Mapped[ConsequenceClass] = mapped_column(str_enum(ConsequenceClass))
    evidence_basis: Mapped[EvidenceBasis] = mapped_column(str_enum(EvidenceBasis))
    title: Mapped[str] = mapped_column(String(500))
    explanation: Mapped[str] = mapped_column(Text)
    affected_entity_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list)
    assumptions: Mapped[list] = mapped_column(JSONB, default=list)
    suggested_actions: Mapped[list] = mapped_column(JSONB, default=list)


class FindingEvidence(Base):
    __tablename__ = "finding_evidence"
    id: Mapped[uuid.UUID] = uuid_pk()
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    evidence_span_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evidence_spans.id"))
    relationship_revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("relationship_revisions.id"))
    requirement_rule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("requirement_rules.id"))
    note: Mapped[str | None] = mapped_column(Text)


class FindingPath(Base):
    __tablename__ = "finding_paths"
    id: Mapped[uuid.UUID] = uuid_pk()
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    entity_ids: Mapped[list] = mapped_column(JSONB)  # ordered entity ids
    edges: Mapped[list] = mapped_column(JSONB)  # [{relationship_revision_id, rel_type, evidence_basis}]


# ─────────────────────────── Interaction and operations ───────────────────────────


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), index=True)
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("curriculum_versions.id"), index=True)
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenarios.id", ondelete="SET NULL"))
    title: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = ts_now()


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[dict] = mapped_column(JSONB)  # structured response envelope
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = ts_now()


class ModelRun(Base):
    __tablename__ = "model_runs"
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    route: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(40))
    requested_model: Mapped[str] = mapped_column(String(120))
    returned_model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(40))
    schema_version: Mapped[str | None] = mapped_column(String(40))
    cache_key: Mapped[str | None] = mapped_column(String(64), index=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 5))
    status: Mapped[str] = mapped_column(String(40))
    evidence_ids: Mapped[list] = mapped_column(JSONB, default=list)
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = ts_now()


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[uuid.UUID] = uuid_pk()
    model_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_runs.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(80))
    arguments: Mapped[dict] = mapped_column(JSONB)
    result_summary: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = ts_now()


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    kind: Mapped[str] = mapped_column(String(60))
    status: Mapped[JobStatus] = mapped_column(str_enum(JobStatus), default=JobStatus.queued, index=True)
    current_stage: Mapped[str | None] = mapped_column(String(60))
    progress: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = ts_now()
    updated_at: Mapped[datetime] = ts_now()


class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = ts_now()


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (Index("ix_review_items_status", "organization_id", "status"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    kind: Mapped[ReviewItemKind] = mapped_column(str_enum(ReviewItemKind))
    status: Mapped[ReviewItemStatus] = mapped_column(str_enum(ReviewItemStatus), default=ReviewItemStatus.open)
    curriculum_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"), index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    detail: Mapped[str | None] = mapped_column(Text)
    # Typed proposal body; for candidate_* kinds this is what acceptance materializes.
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    evidence_span_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), default=list)
    subject_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("entities.id", ondelete="SET NULL"))
    subject_relationship_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("relationship_revisions.id", ondelete="SET NULL")
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(300), unique=True)
    created_at: Mapped[datetime] = ts_now()


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    id: Mapped[uuid.UUID] = uuid_pk()
    review_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_items.id", ondelete="CASCADE"), index=True)
    decision: Mapped[ReviewItemStatus] = mapped_column(str_enum(ReviewItemStatus))
    edited_payload: Mapped[dict | None] = mapped_column(JSONB)
    rationale: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime] = ts_now()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    organization_id: Mapped[uuid.UUID] = org_fk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    subject_type: Mapped[str] = mapped_column(String(60))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    data: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = ts_now()


class SavedView(Base):
    """Visual state only (positions, viewport, filters). Never curriculum semantics."""

    __tablename__ = "saved_views"
    __table_args__ = (UniqueConstraint("user_id", "curriculum_version_id", "name"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    curriculum_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("curriculum_versions.id", ondelete="CASCADE"), index=True
    )
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    viewport: Mapped[dict | None] = mapped_column(JSONB)
    positions: Mapped[dict] = mapped_column(JSONB, default=dict)
    filters: Mapped[dict] = mapped_column(JSONB, default=dict)
    layers: Mapped[list] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = ts_now()
