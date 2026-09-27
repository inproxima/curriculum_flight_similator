"""Typed response models for the Alignment Web (source of the generated TypeScript types)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

StrengthLabel = Literal["strong", "moderate", "weak", "missing"]
NodeStatus = Literal["unchanged", "added", "removed", "modified", "strengthened", "weakened"]


class StrengthComponents(BaseModel):
    trust: float
    intensity: float
    support: float


class EdgeStrength(BaseModel):
    effective: float | None
    label: StrengthLabel | None
    components: StrengthComponents | None = None
    asserted: float | None = None
    asserted_source: str | None = None


class EdgeBundle(BaseModel):
    count: int
    keys: list[str]
    min: float | None
    max: float | None


class WebNode(BaseModel):
    id: str
    type: Literal["course", "program_outcome", "course_outcome", "assessment", "topic"]
    key: str
    title: str
    description: str | None = None
    course_id: str | None = None
    course_ids: list[str] = []
    year: int | None = None
    term: str | None = None
    time_index: float | None = None
    classification: str | None = None
    details: dict[str, Any] = {}
    origin: str
    is_synthetic: bool = False
    evidence: dict[str, Any] = {}
    status: NodeStatus = "unchanged"
    metrics: dict[str, Any] = {}
    metrics_before: dict[str, Any] | None = None
    aggregate: dict[str, int] | None = None


class WebEdge(BaseModel):
    id: str
    key: str | None
    revision_id: str | None = None
    source: str
    target: str
    type: str
    category: Literal["vertical", "horizontal", "containment", "overlap"]
    level: str | None = None
    origin: str
    basis: str
    review_state: str
    provenance: Literal["documented", "accepted_inference", "proposed", "scenario"]
    strength: EdgeStrength | None = None
    strength_before: float | None = None
    rationale: str | None = None
    has_evidence: bool = False
    contradicted: bool = False
    status: NodeStatus = "unchanged"
    bundle: EdgeBundle | None = None


class Gap(BaseModel):
    id: str
    kind: str
    severity: str
    message: str
    entity_ids: list[str]
    source: str | None = None
    target: str | None = None


class ThreadStage(BaseModel):
    course_id: str | None
    course_key: str | None
    year: int | None
    term: str | None
    clo_id: str | None
    clo_key: str | None
    relationship_key: str
    level: str | None
    effective: float | None
    label: StrengthLabel | None
    provenance: str
    assessed: bool
    assessment_ids: list[str]


class PloThread(BaseModel):
    plo_id: str
    plo_key: str
    status: Literal["complete", "out_of_order", "never_assessed", "incomplete", "uncovered", "levels_unstated"]
    missing: list[str]
    strength: float | None
    label: StrengthLabel | None
    firsts: dict[str, str | None]
    stages: list[ThreadStage]


class CoursePair(BaseModel):
    source_course: str
    target_course: str
    source_key: str
    target_key: str
    via: str
    course_edge_keys: list[str]
    edge_keys: list[str]
    strength: float | None
    label: StrengthLabel | None


class Scorecard(BaseModel):
    plo_documented_coverage: float | None
    plo_assessed_coverage: float | None
    threads_complete: dict[str, int]
    avg_alignment_strength: float | None
    links: dict[str, int]
    gaps: dict[str, int]
    broken_chains: int
    weak_chains: int
    orphans: dict[str, int]


class WebColumn(BaseModel):
    course_id: str
    key: str
    title: str
    year: int | None
    term: str | None
    time_index: float | None
    band: str


class WebFocus(BaseModel):
    id: str
    kind: str | None
    node_ids: list[str]
    edge_ids: list[str]


class WebScenario(BaseModel):
    id: str
    title: str
    revision: int


class WebView(BaseModel):
    curriculum_version: dict[str, Any]
    scenario: WebScenario | None = None
    detail: Literal["full", "overview"]
    strength_model: dict[str, Any]
    columns: list[WebColumn]
    nodes: list[WebNode]
    edges: list[WebEdge]
    gaps: list[Gap]
    gaps_before: list[Gap] | None = None
    plo_threads: list[PloThread]
    course_pairs: list[CoursePair]
    scorecard: Scorecard
    scorecard_baseline: Scorecard | None = None
    focus: WebFocus | None = None
    stats: dict[str, Any]
    truncated: dict[str, Any] | None = None
    change_errors: list[dict[str, Any]] = []
