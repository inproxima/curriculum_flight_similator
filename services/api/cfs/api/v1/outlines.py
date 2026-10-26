"""Course outline import: upload (or pick) an outline for one course of a draft version, let the agent plan what the
program needs from it, then apply a reviewed selection (and revert it). See cfs.outlines and ADR 0004."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Header, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.api.v1.ai import _require_ai
from cfs.api.v1.alignment import map_job_for
from cfs.api.v1.catalog import version_snapshot
from cfs.api.v1.schemas_outline import (
    CourseMissingOut,
    OutlineImportBrief,
    OutlineImportCreated,
    OutlineImportOut,
    PlanSummary,
    RerunIn,
    SelectionIn,
)
from cfs.core.auth import Principal, get_principal
from cfs.core.config import get_settings
from cfs.core.db import get_db
from cfs.core.errors import AppError, Conflict, NotFound
from cfs.core.jobs import enqueue
from cfs.core.scope import get_scoped
from cfs.core.security import limit_ai, limit_upload
from cfs.curriculum.rules import normalize_code
from cfs.curriculum.service import assert_draft, membership
from cfs.documents import store_upload
from cfs.graph.snapshot import load_snapshot
from cfs.models import CurriculumVersion, Document, DocumentVersion, Entity, OutlineImport
from cfs.models.enums import DocumentType, EntityType, Role
from cfs.outlines.apply import apply_import, revert_import, validate_selection
from cfs.outlines.missing import course_missing
from cfs.outlines.plan import etag
from cfs.outlines.project import preview
from cfs.outlines.service import discard_import, rerun_import, start_import
from cfs.outlines.state import course_state_hash

router = APIRouter(tags=["outlines"])


def _course(db: Session, p: Principal, version_id: uuid.UUID, course_id: uuid.UUID) -> Entity:
    course = get_scoped(db, Entity, course_id, p, "Course")
    if course.entity_type != EntityType.course or membership(db, version_id, course.id) is None:
        raise NotFound("Course is not part of this curriculum version")
    return course


def _brief(db: Session, imp: OutlineImport) -> dict[str, Any]:
    course = db.get(Entity, imp.course_entity_id)
    dv = db.get(DocumentVersion, imp.document_version_id)
    doc = db.get(Document, dv.document_id) if dv else None
    return {
        "id": imp.id,
        "curriculum_version_id": imp.curriculum_version_id,
        "course_entity_id": imp.course_entity_id,
        "course_key": course.stable_key if course else None,
        "document_version_id": imp.document_version_id,
        "document_title": doc.title if doc else None,
        "job_id": imp.job_id,
        "status": imp.status.value,
        "options": imp.options or {},
        "outline_year": ((imp.edition or {}).get("outline") or {}).get("academic_year"),
        "warnings": imp.warnings or [],
        "error": imp.error,
        "counts": (imp.result or {}).get("counts"),
        "created_at": imp.created_at,
        "updated_at": imp.updated_at,
        "applied_at": imp.applied_at,
        "reverted_at": imp.reverted_at,
    }


def _full(db: Session, imp: OutlineImport) -> dict[str, Any]:
    res = imp.result
    return {
        **_brief(db, imp),
        "plan": imp.plan,
        "result": {k: v for k, v in res.items() if k != "undo"} if res else None,
        "etag": etag(imp.state_hash, imp.plan_hash) if imp.plan else None,
    }


def _get(db: Session, p: Principal, import_id: uuid.UUID) -> OutlineImport:
    return get_scoped(db, OutlineImport, import_id, p, "Outline import")


@router.get("/versions/{version_id}/courses/{course_id}/missing", response_model=CourseMissingOut)
def get_course_missing(
    version_id: uuid.UUID, course_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    """What the program is missing for one course: content, unknowns, and alignment gaps of its elements."""
    snap = version_snapshot(db, p, version_id)
    cid = str(course_id)
    if cid not in snap.entities or snap.entities[cid]["type"] != "course":
        raise NotFound("Course is not part of this curriculum version")
    return course_missing(snap, cid)


@router.post(
    "/versions/{version_id}/courses/{course_id}/outline-imports",
    response_model=OutlineImportCreated,
    status_code=202,
)
async def create_outline_import(
    version_id: uuid.UUID,
    course_id: uuid.UUID,
    file: UploadFile | None = File(default=None),
    pasted_text: str | None = Form(default=None),
    document_version_id: uuid.UUID | None = Form(default=None),
    academic_year: str | None = Form(default=None),
    publication_date: date | None = Form(default=None),
    title: str | None = Form(default=None),
    alias_codes: str | None = Form(default=None),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    """Import a course outline for this course: a PDF, DOCX or text file, pasted text, or a document already
    uploaded. The job reads it, extracts its outcomes, assessments and topics (route `extract`), and asks the
    reconcile agent (route `synthesize`) what the program needs from it. The outline text and this course's
    elements are sent to those routes' providers. Nothing changes until a plan is applied."""
    p.require(Role.editor)
    limit_ai(p)
    _require_ai("extract")
    _require_ai("synthesize")
    cv = get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    assert_draft(db, cv.id)
    course = _course(db, p, cv.id, course_id)
    duplicate = False
    if document_version_id is not None:
        dv = get_scoped(db, DocumentVersion, document_version_id, p, "Document version")
    else:
        limit_upload(p)
        limit = get_settings().max_upload_bytes
        if file is not None:
            data, filename, dtype = await file.read(limit + 1), file.filename, DocumentType.course_outline
        elif pasted_text and pasted_text.strip():
            data, filename, dtype = pasted_text.encode("utf-8"), "pasted.txt", DocumentType.course_outline
        else:
            raise AppError("Provide a file, pasted text, or a document to use", code="missing_content")
        dv, _job, created = store_upload(
            db,
            p,
            data,
            filename=filename,
            title=(title or "").strip() or f"{course.stable_key} course outline",
            document_type=dtype,
            publication_date=publication_date,
            ingest=False,  # the entered academic year stays with the import until a person applies its plan
        )
        duplicate = not created
    if dv.ai_providers == []:
        raise AppError(
            "This document's AI policy forbids sending it to any model provider.", code="ai_forbidden", status=403
        )
    aliases = sorted({normalize_code(a) for a in (alias_codes or "").split(",") if a.strip()})
    options = {
        "academic_year": (academic_year or "").strip() or None,
        "publication_date": publication_date.isoformat() if publication_date else None,
        "alias_codes": aliases,
    }
    imp, job, reused = start_import(db, p, cv, course, dv, options=options)
    db.commit()
    if job is not None:
        enqueue(job.id)
    db.refresh(imp)
    return {
        "outline_import": _brief(db, imp),
        "job_id": job.id if job else imp.job_id,
        "duplicate_document": duplicate,
        "reused": reused,
    }


@router.get("/versions/{version_id}/courses/{course_id}/outline-imports", response_model=list[OutlineImportBrief])
def list_outline_imports(
    version_id: uuid.UUID, course_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    rows = db.scalars(
        select(OutlineImport)
        .where(
            OutlineImport.organization_id == p.org_id,
            OutlineImport.curriculum_version_id == version_id,
            OutlineImport.course_entity_id == course_id,
        )
        .order_by(OutlineImport.created_at.desc())
        .limit(20)
    ).all()
    return [_brief(db, r) for r in rows]


@router.get("/outline-imports/{import_id}", response_model=OutlineImportOut)
def get_outline_import(
    import_id: uuid.UUID, response: Response, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    imp = _get(db, p, import_id)
    out = _full(db, imp)
    if out["etag"]:
        response.headers["ETag"] = f'"{out["etag"]}"'
    return out


@router.post("/outline-imports/{import_id}/preview", response_model=PlanSummary)
def preview_outline_import(
    import_id: uuid.UUID, body: SelectionIn, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    """Missing pieces a selection would close (and introduce), computed on a copy of the version."""
    imp = _get(db, p, import_id)
    if not imp.plan:
        raise AppError("This import has no plan yet.", code="not_ready", status=409)
    edits = {k: v.model_dump(exclude_unset=True) for k, v in body.edits.items()}
    sel, edits = validate_selection(imp.plan, body.selected_item_ids, edits)
    snap = load_snapshot(db, imp.curriculum_version_id)
    cid = str(imp.course_entity_id)
    dv = db.get(DocumentVersion, imp.document_version_id)
    if cid not in snap.entities or course_state_hash(snap, cid, extra=(dv.academic_year,)) != imp.state_hash:
        raise Conflict("The course changed after this plan was made; re-run it.", code="plan_stale")
    return preview(snap, imp.plan, sel, str(imp.id), edits)


@router.post("/outline-imports/{import_id}/apply", response_model=OutlineImportOut)
def apply_outline_import(
    import_id: uuid.UUID,
    body: SelectionIn,
    if_match: str | None = Header(default=None),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    """Apply the selected plan items to the draft version in one transaction (If-Match: the plan's ETag)."""
    _get(db, p, import_id)
    edits = {k: v.model_dump(exclude_unset=True) for k, v in body.edits.items()}
    imp = apply_import(db, p, import_id, selected=body.selected_item_ids, edits=edits, if_match=if_match)
    job = None
    if body.map_after and _map_available():
        job, est = map_job_for(db, p, imp.curriculum_version_id, course_ids=[str(imp.course_entity_id)], forward=True)
        if job is not None:
            imp.result = {**imp.result, "map_job_id": str(job.id), "map_estimate_usd": est["estimate_usd"]}
    db.commit()
    if job is not None:
        enqueue(job.id)
    return _full(db, imp)


def _map_available() -> bool:
    try:
        _require_ai("synthesize")
        return True
    except AppError:
        return False


@router.post("/outline-imports/{import_id}/revert", response_model=OutlineImportOut)
def revert_outline_import(import_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    """Undo an applied import, if nothing it changed has been changed since (409 revert_conflict otherwise)."""
    _get(db, p, import_id)
    imp = revert_import(db, p, import_id)
    db.commit()
    return _full(db, imp)


@router.post("/outline-imports/{import_id}/rerun", status_code=202)
def rerun_outline_import(
    import_id: uuid.UUID,
    body: RerunIn | None = None,
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
) -> dict[str, str]:
    """Plan again against the current version (after a stale plan, or with a corrected academic year)."""
    limit_ai(p)
    _require_ai("synthesize")
    imp = _get(db, p, import_id)
    opts = body.model_dump(exclude_unset=True) if body else None
    if opts and "alias_codes" in opts and opts["alias_codes"] is not None:
        opts["alias_codes"] = sorted({normalize_code(a) for a in opts["alias_codes"] if a.strip()})
    job = rerun_import(db, p, imp, options=opts)
    db.commit()
    enqueue(job.id)
    return {"job_id": str(job.id), "import_id": str(imp.id)}


@router.post("/outline-imports/{import_id}/discard", response_model=OutlineImportBrief)
def discard_outline_import(import_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    imp = discard_import(db, p, _get(db, p, import_id))
    db.commit()
    return _brief(db, imp)
