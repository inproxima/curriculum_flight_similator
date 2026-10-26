"""Create, rerun and discard outline imports. Callers commit and then enqueue the returned job."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.core.audit import audit
from cfs.core.auth import Principal
from cfs.core.errors import Conflict
from cfs.core.jobs import create_job
from cfs.curriculum.service import assert_draft
from cfs.models import CurriculumVersion, DocumentVersion, Entity, Job, OutlineImport
from cfs.models.base import utcnow
from cfs.models.enums import OutlineImportStatus, Role
from cfs.outlines.apply import lock_import

ACTIVE = (OutlineImportStatus.queued, OutlineImportStatus.running, OutlineImportStatus.ready)


def start_import(
    db: Session,
    p: Principal,
    cv: CurriculumVersion,
    course: Entity,
    dv: DocumentVersion,
    *,
    options: dict[str, Any],
) -> tuple[OutlineImport, Job | None, bool]:
    """(import, job, reused). An active import of the same file for the same course and options is reused."""
    active = db.scalar(
        select(OutlineImport)
        .where(
            OutlineImport.curriculum_version_id == cv.id,
            OutlineImport.course_entity_id == course.id,
            OutlineImport.document_version_id == dv.id,
            OutlineImport.status.in_(ACTIVE),
        )
        .order_by(OutlineImport.created_at.desc())
    )
    if active is not None and (active.options or {}) == options:
        return active, None, True
    imp = OutlineImport(
        organization_id=p.org_id,
        curriculum_version_id=cv.id,
        course_entity_id=course.id,
        document_version_id=dv.id,
        options=options,
        status=OutlineImportStatus.queued,
        warnings=[],
        model_run_ids=[],
        created_by=p.user_id,
    )
    db.add(imp)
    db.flush()
    job = create_job(db, p.org_id, "outline_import", {"import_id": str(imp.id)}, user_id=p.user_id)
    imp.job_id = job.id
    audit(
        db,
        p,
        "outline_import.created",
        "outline_import",
        imp.id,
        {"course": course.stable_key, "curriculum_version_id": str(cv.id), "document_version_id": str(dv.id)},
    )
    return imp, job, False


def rerun_import(db: Session, p: Principal, imp: OutlineImport, *, options: dict[str, Any] | None = None) -> Job:
    """Plan again against the current version (the verified extraction is reused unless the course aliases change)."""
    p.require(Role.editor)
    imp = lock_import(db, p, imp.id)  # a concurrent apply holds this lock; re-check its status afterwards
    if imp.status not in (OutlineImportStatus.ready, OutlineImportStatus.failed, OutlineImportStatus.discarded):
        raise Conflict(f"This import is {imp.status.value}; it can't be re-run now.", code="not_rerunnable")
    assert_draft(db, imp.curriculum_version_id)
    if options:
        new = {**(imp.options or {}), **{k: v for k, v in options.items() if v is not None}}
        if new.get("alias_codes") != (imp.options or {}).get("alias_codes"):
            imp.extraction = None
        imp.options = new
    imp.status, imp.plan, imp.plan_hash, imp.state_hash, imp.error = OutlineImportStatus.queued, None, None, None, None
    imp.updated_at = utcnow()
    job = create_job(db, p.org_id, "outline_import", {"import_id": str(imp.id)}, user_id=p.user_id)
    imp.job_id = job.id
    audit(db, p, "outline_import.rerun", "outline_import", imp.id, {"options": imp.options})
    return job


def discard_import(db: Session, p: Principal, imp: OutlineImport) -> OutlineImport:
    p.require(Role.editor)
    imp = lock_import(db, p, imp.id)
    if imp.status not in (OutlineImportStatus.ready, OutlineImportStatus.failed, OutlineImportStatus.queued):
        raise Conflict(
            f"This import is {imp.status.value}; only an unapplied plan can be discarded.", code="not_discardable"
        )
    imp.status, imp.updated_at = OutlineImportStatus.discarded, utcnow()
    audit(db, p, "outline_import.discarded", "outline_import", imp.id, {})
    return imp
