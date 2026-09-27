"""Pydantic request/response models for the v1 API (source of the generated TypeScript client)."""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProgramOut(ORM):
    id: uuid.UUID
    code: str
    name: str
    institution: str | None
    parent_degree: str | None
    description: str | None
    is_synthetic: bool


class PathwayOut(ORM):
    id: uuid.UUID
    code: str
    name: str


class VersionOut(ORM):
    id: uuid.UUID
    program_id: uuid.UUID
    label: str
    academic_year: str | None
    cohort: str | None
    status: str
    parent_version_id: uuid.UUID | None
    published_at: datetime | None
    is_synthetic: bool
    notes: str | None


class VersionCreate(BaseModel):
    label: str
    from_version_id: uuid.UUID | None = None
    academic_year: str | None = None
    cohort: str | None = None


class DocumentVersionOut(ORM):
    id: uuid.UUID
    document_id: uuid.UUID
    sha256: str
    mime_type: str
    size_bytes: int
    original_filename: str | None
    publication_date: date | None
    retrieval_date: date | None
    academic_year: str | None
    cohort_applicability: str | None
    processing_status: str
    page_count: int | None
    is_synthetic: bool
    created_at: datetime


class DocumentOut(ORM):
    id: uuid.UUID
    title: str
    source_url: str | None
    document_type: str
    created_at: datetime
    versions: list[DocumentVersionOut] = []


class UploadResult(BaseModel):
    document_version: DocumentVersionOut
    duplicate: bool
    job_id: uuid.UUID | None


class DocumentVersionPatch(BaseModel):
    publication_date: date | None = None
    retrieval_date: date | None = None
    academic_year: str | None = None
    cohort_applicability: str | None = None
    authority_note: str | None = None


class SourceAssign(BaseModel):
    curriculum_version_id: uuid.UUID
    authority: Literal["authoritative", "supporting", "historical_reference", "exploratory"]
    applicability: str | None = None
    is_exploratory_cross_version: bool = False
    notes: str | None = None


class SourceOut(ORM):
    id: uuid.UUID
    document_version_id: uuid.UUID
    curriculum_version_id: uuid.UUID
    applicability: str | None
    authority: str
    approval_status: str
    is_exploratory_cross_version: bool
    notes: str | None


class PageOut(ORM):
    page_number: int
    text: str
    extraction_method: str
    parser_version: str
    ocr_state: str
    char_count: int
    width: float | None
    height: float | None


class SpanOut(ORM):
    id: uuid.UUID
    document_version_id: uuid.UUID
    page_number: int
    char_start: int
    char_end: int
    quote: str
    bbox: list | None
    document_title: str | None = None
    document_type: str | None = None
    publication_date: date | None = None
    academic_year: str | None = None
    is_synthetic: bool = False


class JobOut(ORM):
    id: uuid.UUID
    kind: str
    status: str
    current_stage: str | None
    progress: float
    cancel_requested: bool
    attempts: int
    payload: dict
    result: dict | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class JobEventOut(ORM):
    id: int
    stage: str | None
    status: str
    message: str
    data: dict | None
    created_at: datetime


class ReviewItemOut(ORM):
    id: uuid.UUID
    kind: str
    status: str
    curriculum_version_id: uuid.UUID | None
    document_version_id: uuid.UUID | None
    title: str
    detail: str | None
    payload: dict
    evidence_span_ids: list[uuid.UUID]
    subject_entity_id: uuid.UUID | None
    subject_relationship_revision_id: uuid.UUID | None
    created_at: datetime


class ReviewDecisionIn(BaseModel):
    decision: Literal["accept", "reject", "defer", "edit"]
    rationale: str | None = None
    edited_payload: dict | None = None
    # for conflicts: which side applies to this curriculum version
    choice: Literal["existing", "proposed"] | None = None


class ReviewDecisionOut(BaseModel):
    item: ReviewItemOut
    applied: dict[str, Any]


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


class SavedViewIn(BaseModel):
    name: str = "default"
    scenario_id: uuid.UUID | None = None
    viewport: dict | None = None
    positions: dict = Field(default_factory=dict)
    filters: dict = Field(default_factory=dict)
    layers: list = Field(default_factory=list)


class SavedViewOut(ORM):
    id: uuid.UUID
    name: str
    curriculum_version_id: uuid.UUID
    scenario_id: uuid.UUID | None
    viewport: dict | None
    positions: dict
    filters: dict
    layers: list
    updated_at: datetime
