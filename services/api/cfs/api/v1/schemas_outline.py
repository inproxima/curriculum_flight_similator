"""Course outline import: request/response models (typed so the TS client knows the plan's shape)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PlanEnd(BaseModel):
    ref: str | None = None
    entity_id: str | None = None
    key: str | None = None
    type: str
    label: str | None = None
    number: str | None = None
    short: str | None = None
    new: bool | None = None


class PlanEvidence(BaseModel):
    span_id: str | None = None
    page: int | None = None
    quote: str | None = None
    source: Literal["outline", "element"]
    field: str | None = None


class PlanDiff(BaseModel):
    field: str
    before: Any = None
    after: Any = None


class PlanLink(BaseModel):
    source: PlanEnd
    target: PlanEnd
    rel_type: Literal["assesses", "supports", "contributes_to"]
    basis: Literal["stated", "inferred"]
    level: Literal["introduce", "reinforce", "assess"] | None = None
    strength: float | None = None
    model_strength: float | None = None
    rationale: str | None = None


class PlanAgent(BaseModel):
    decision: str | None = None
    reason: str | None = None
    decided_by: str | None = None
    skip: str | None = None


class PlanItem(BaseModel):
    id: str
    section: Literal["outcomes", "assessments", "topics", "course", "links"]
    action: Literal["add", "replace", "keep", "retire", "detach", "set_field", "link"]
    title: str
    noop: bool
    selectable: bool
    default_selected: bool
    reason: str
    blocked_reason: str | None = None
    target: PlanEnd | None = None
    proposed: dict[str, Any] | None = None
    key_preview: str | None = None
    diff: list[PlanDiff] = []
    evidence: list[PlanEvidence] = []
    link: PlanLink | None = None
    depends_on: list[str] = []
    excludes: list[str] = []
    fills: list[str] = []
    carries_links: int = 0
    drops_links: int = 0
    agent: PlanAgent | None = None


class MissingPiece(BaseModel):
    id: str
    kind: str
    severity: str
    message: str
    entity_ids: list[str]
    fillable: bool
    source: str | None = None
    target: str | None = None
    why: str | None = None


class PlanSection(BaseModel):
    key: Literal["outcomes", "assessments", "topics"]
    label: str
    status: Literal["present", "absent"]
    complete: bool | None = None
    reason: str
    existing: int
    extracted: int
    existing_edition: str | None = None
    compare: int | None = None


class OutlineYear(BaseModel):
    start_year: int | None = None
    academic_year: str | None = None
    publication_date: str | None = None
    source: str | None = None
    quote: str | None = None


class VersionYear(BaseModel):
    academic_year: str | None = None
    start_year: int | None = None
    label: str | None = None


class PlanEdition(BaseModel):
    outline: OutlineYear
    version: VersionYear
    fit: Literal["match", "older", "later", "unknown"]


class PlanSummary(BaseModel):
    selected: int
    changes: dict[str, int]
    missing_before: int
    missing_after: int
    closes: list[str]
    introduces: list[MissingPiece]
    program_before: int
    program_after: int
    program_closes: list[str]


class PlanDrop(BaseModel):
    what: str
    reason: str


class OutlinePlan(BaseModel):
    course: dict[str, Any]
    document: dict[str, Any]
    edition: PlanEdition
    sections: list[PlanSection]
    items: list[PlanItem]
    missing_before: list[MissingPiece]
    program_missing: list[MissingPiece]
    unfillable: list[MissingPiece]
    warnings: list[str]
    dropped: list[PlanDrop]
    notes: list[str]
    agent: dict[str, Any]
    summary: PlanSummary


class OutlineResult(BaseModel):
    applied: list[str]
    edits: dict[str, Any]
    counts: dict[str, int]
    new_entities: dict[str, str]
    missing_after: list[MissingPiece]
    program_after: list[MissingPiece]
    summary: dict[str, Any]
    map_job_id: str | None = None
    map_estimate_usd: float | None = None


class OutlineImportBrief(BaseModel):
    id: uuid.UUID
    curriculum_version_id: uuid.UUID
    course_entity_id: uuid.UUID
    course_key: str | None = None
    document_version_id: uuid.UUID
    document_title: str | None = None
    job_id: uuid.UUID | None = None
    status: Literal["queued", "running", "ready", "applied", "discarded", "failed", "reverted"]
    options: dict[str, Any]
    outline_year: str | None = None
    warnings: list[str] = []
    error: dict[str, Any] | None = None
    counts: dict[str, int] | None = None
    created_at: datetime
    updated_at: datetime
    applied_at: datetime | None = None
    reverted_at: datetime | None = None


class OutlineImportOut(OutlineImportBrief):
    plan: OutlinePlan | None = None
    result: OutlineResult | None = None
    etag: str | None = None


class OutlineImportCreated(BaseModel):
    outline_import: OutlineImportBrief
    job_id: uuid.UUID | None
    duplicate_document: bool
    reused: bool


class LinkEdit(BaseModel):
    strength: float | None = Field(default=None, ge=0, le=1)
    level: Literal["introduce", "reinforce", "assess", ""] | None = None


class SelectionIn(BaseModel):
    selected_item_ids: list[str] = Field(max_length=2000)
    edits: dict[str, LinkEdit] = {}
    # after applying, map all of the course's connections with the AI (proposed links + "none found" verdicts)
    map_after: bool = False


class RerunIn(BaseModel):
    academic_year: str | None = None
    alias_codes: list[str] | None = None


class CourseMissingOut(BaseModel):
    course_id: str
    course_key: str
    items: list[MissingPiece]
    program: list[MissingPiece]
    counts: dict[str, int]
    total: int
    fillable: int
