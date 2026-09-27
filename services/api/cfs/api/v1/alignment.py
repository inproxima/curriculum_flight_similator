"""Alignment Web write paths: inline review of proposed links and AI-proposed missing links (draft versions only)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
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
