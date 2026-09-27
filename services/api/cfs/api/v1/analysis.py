"""Outcome/assessment matrices and baseline issue detection for a curriculum version."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from cfs.analysis.coverage import assessment_alignment, detect_issues, outcome_matrix
from cfs.api.v1.catalog import version_snapshot
from cfs.core.auth import Principal, get_principal
from cfs.core.db import get_db

router = APIRouter()


@router.get("/versions/{version_id}/outcome-matrix", tags=["analysis"])
def get_outcome_matrix(
    version_id: uuid.UUID,
    pathway: str | None = None,
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return outcome_matrix(version_snapshot(db, p, version_id), pathway)


@router.get("/versions/{version_id}/assessment-alignment", tags=["analysis"])
def get_assessment_alignment(
    version_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
) -> dict[str, Any]:
    return assessment_alignment(version_snapshot(db, p, version_id))


@router.get("/versions/{version_id}/issues", tags=["analysis"])
def get_issues(
    version_id: uuid.UUID,
    pathway: str | None = None,
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
) -> dict[str, Any]:
    snap = version_snapshot(db, p, version_id)
    return {
        "curriculum_version": snap.version,
        "pathway_assumption": pathway or "required path",
        "issues": detect_issues(snap, pathway),
        "note": "Potential issues for faculty review, not verdicts. Repetition may be deliberate.",
    }
