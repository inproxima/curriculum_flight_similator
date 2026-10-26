"""Alignment Web write paths: inline review of proposed links and AI-proposed missing links (draft versions only)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cfs.api.v1.ai import _require_ai
from cfs.core.audit import audit
from cfs.core.auth import Principal, get_principal
from cfs.core.db import get_db
from cfs.core.errors import AppError
from cfs.core.jobs import create_job, enqueue
from cfs.core.scope import get_scoped
from cfs.core.security import limit_ai
from cfs.curriculum.service import assert_draft
from cfs.models import CurriculumVersion
from cfs.models.enums import Role
from cfs.reviews import decide_relationship

router = APIRouter(tags=["alignment"])


class LinkDecisionIn(BaseModel):
    decision: Literal["accept", "reject", "edit"]
    strength: float | None = Field(default=None, ge=0, le=1)
    level: Literal["introduce", "reinforce", "assess", ""] | None = None
    rationale: str | None = None


class LinkDecisionOut(BaseModel):
    key: str
    revision_id: str
    review_state: str
    strength: float | None
    strength_source: str | None
    level: str | None


def _out(rev) -> LinkDecisionOut:
    return LinkDecisionOut(
        key=str(rev.relationship_key),
        revision_id=str(rev.id),
        review_state=rev.review_state.value,
        strength=float(rev.strength) if rev.strength is not None else None,
        strength_source=rev.strength_source,
        level=rev.contribution_level.value if rev.contribution_level else None,
    )


@router.post("/versions/{version_id}/relationships/{key}/decision", response_model=LinkDecisionOut)
def link_decision(
    version_id: uuid.UUID,
    key: uuid.UUID,
    body: LinkDecisionIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    """Accept, edit (asserted strength / level / rationale), or reject one link. Accepting keeps an interpretation
    labelled as an interpretation; documented links cannot be rejected here."""
    get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    rev = decide_relationship(
        db, p, version_id, key, body.decision, strength=body.strength, level=body.level, rationale=body.rationale
    )
    db.commit()
    return _out(rev)


class BulkDecisionIn(BaseModel):
    keys: list[uuid.UUID] = Field(min_length=1, max_length=500)
    decision: Literal["accept", "reject"]


@router.post("/versions/{version_id}/relationships/decisions")
def link_decisions(
    version_id: uuid.UUID,
    body: BulkDecisionIn,
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
) -> dict:
    get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    updated, errors = 0, []
    for k in body.keys:
        try:
            with db.begin_nested():
                decide_relationship(db, p, version_id, k, body.decision)
            updated += 1
        except AppError as e:
            errors.append({"key": str(k), "message": e.message})
    db.commit()
    return {"updated": updated, "errors": errors}


class AlignmentRequest(BaseModel):
    course_ids: list[str] | None = None
    kinds: list[Literal["clo_plo", "assessment_clo", "topic_clo", "clo_clo"]] | None = None
    max_calls: int = Field(default=40, ge=1, le=200)


@router.post("/versions/{version_id}/ai-alignment", status_code=202)
def ai_alignment(
    version_id: uuid.UUID,
    body: AlignmentRequest | None = None,
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    """Ask the synthesis model to propose missing alignment links with strengths. Sends element texts (outcome
    statements, assessment titles, topic names) of this draft version to the `synthesize` route's provider."""
    limit_ai(p)
    p.require(Role.editor)
    _require_ai("synthesize")
    get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    assert_draft(db, version_id)
    body = body or AlignmentRequest()
    job = create_job(
        db,
        p.org_id,
        "ai_propose_alignment",
        {"curriculum_version_id": str(version_id), **body.model_dump()},
        user_id=p.user_id,
    )
    audit(db, p, "ai.alignment_requested", "curriculum_version", version_id, body.model_dump())
    db.commit()
    enqueue(job.id)
    return {"job_id": str(job.id)}


class MapRequest(BaseModel):
    course_ids: list[str] | None = None
    forward: bool = True
    max_cost_usd: float | None = Field(default=None, gt=0, le=25)


def map_job_for(
    db: Session, p: Principal, version_id: uuid.UUID, *, course_ids: list[str] | None, forward: bool, max_cost_usd=None
):
    """Plan, price and queue a connection-mapping job (the caller commits, then enqueues). None if nothing to map."""
    from cfs.ai.mapping import estimate, job_cap, plan_map_batches
    from cfs.core.config import get_settings
    from cfs.graph.snapshot import load_snapshot

    snap = load_snapshot(db, version_id)
    batches = plan_map_batches(snap, course_ids=course_ids, forward=forward)
    if not batches:
        return None, None
    est = estimate(snap, batches)
    cap = job_cap(est["estimate_usd"], max_cost_usd, get_settings().ai_max_cost_per_job_usd)
    keys = sorted({b["course"]["key"] for b in batches})
    job = create_job(
        db,
        p.org_id,
        "ai_map_alignment",
        {
            "curriculum_version_id": str(version_id),
            "course_ids": course_ids,
            "forward": forward,
            "cost_cap_usd": cap,
            # shown by the app's job notifications
            "label": ", ".join(keys) if course_ids else f"all {len(keys)} courses",
            "version_label": snap.version.get("label"),
            "estimate_usd": est["estimate_usd"],
            "calls": est["calls"],
        },
        user_id=p.user_id,
    )
    audit(
        db,
        p,
        "ai.map_requested",
        "curriculum_version",
        version_id,
        {"course_ids": course_ids, "forward": forward, "estimate_usd": est["estimate_usd"], "cost_cap_usd": cap},
    )
    return job, {**est, "cost_cap_usd": cap}


@router.get("/versions/{version_id}/ai-map/preview")
def ai_map_preview(
    version_id: uuid.UUID,
    course_id: list[str] = Query(default=[]),
    forward: bool = True,
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
) -> dict:
    """How many model calls mapping would take and what it would roughly cost (nothing is sent)."""
    from cfs.ai.mapping import estimate, job_cap, plan_map_batches
    from cfs.core.config import get_settings
    from cfs.graph.snapshot import load_snapshot

    v = get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    snap = load_snapshot(db, version_id)
    batches = plan_map_batches(snap, course_ids=course_id or None, forward=forward)
    est = estimate(snap, batches)
    return {
        **est,
        "courses": len({b["course"]["id"] for b in batches}),
        "elements": sum(len(b["sources"]) for b in batches),
        "draft": v.status.value == "draft",
        "cost_cap_usd": job_cap(est["estimate_usd"], None, get_settings().ai_max_cost_per_job_usd),
    }


@router.post("/versions/{version_id}/ai-map", status_code=202)
def ai_map(
    version_id: uuid.UUID,
    body: MapRequest | None = None,
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    """Map every outcome, assessment and topic (all courses, or the given ones): the synthesis model proposes each
    element's connections with a level (direct / strong / partial), or reports that it found none. Proposals are
    written to this draft as dashed "AI?" links; nothing becomes documented. Sends element texts to the provider."""
    limit_ai(p)
    p.require(Role.editor)
    _require_ai("synthesize")
    get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    assert_draft(db, version_id)
    body = body or MapRequest()
    job, est = map_job_for(
        db, p, version_id, course_ids=body.course_ids, forward=body.forward, max_cost_usd=body.max_cost_usd
    )
    if job is None:
        raise AppError("There is nothing to map in this selection.", code="nothing_to_map", status=409)
    db.commit()
    enqueue(job.id)
    return {"job_id": str(job.id), **{k: est[k] for k in ("calls", "estimate_usd", "cost_cap_usd", "model")}}
