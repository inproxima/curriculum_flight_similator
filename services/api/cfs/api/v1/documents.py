"""Documents, curriculum-source assignment, jobs (with SSE progress), and the review inbox."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from cfs.api.v1.schemas import (
    DocumentOut,
    DocumentVersionOut,
    DocumentVersionPatch,
    JobEventOut,
    JobOut,
    Page,
    PageOut,
    ReviewDecisionIn,
    ReviewDecisionOut,
    ReviewItemOut,
    SourceAssign,
    SourceOut,
    UploadResult,
)
from cfs.core.audit import audit
from cfs.core.auth import Principal, get_principal
from cfs.core.db import get_db, get_sessionmaker
from cfs.core.errors import AppError, Conflict
from cfs.core.jobs import create_job, enqueue, retry_job
from cfs.core.scope import get_scoped
from cfs.documents import store_upload
from cfs.models import (
    CurriculumSource,
    CurriculumVersion,
    Document,
    DocumentPage,
    DocumentVersion,
    Job,
    JobEvent,
    ReviewItem,
)
from cfs.models.enums import ApprovalStatus, DocumentType, JobStatus, Role, SourceAuthority
from cfs.reviews import decide
from cfs.storage import get_store

router = APIRouter()


@router.get("/documents", response_model=list[DocumentOut], tags=["documents"])
def list_documents(db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    return db.scalars(
        select(Document)
        .where(Document.organization_id == p.org_id)
        .options(selectinload(Document.versions))
        .order_by(Document.created_at.desc())
    ).all()


@router.post("/documents", response_model=UploadResult, status_code=201, tags=["documents"])
async def upload_document(
    file: UploadFile | None = File(default=None),
    pasted_text: str | None = Form(default=None),
    title: str | None = Form(default=None),
    document_type: DocumentType = Form(default=DocumentType.other),
    source_url: str | None = Form(default=None),
    publication_date: date | None = Form(default=None),
    retrieval_date: date | None = Form(default=None),
    academic_year: str | None = Form(default=None),
    cohort_applicability: str | None = Form(default=None),
    document_id: uuid.UUID | None = Form(default=None),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    """Upload a PDF, DOCX, or text file, or paste text. Content is sniffed; the declared MIME type is ignored."""
    from cfs.core.config import get_settings

    p.require(Role.editor)
    from cfs.core.security import limit_upload

    limit_upload(p)
    limit = get_settings().max_upload_bytes
    if file is not None:
        data = await file.read(limit + 1)
        filename = file.filename
    elif pasted_text:
        data, filename = pasted_text.encode("utf-8"), "pasted.txt"
        document_type = DocumentType.pasted_text if document_type == DocumentType.other else document_type
    else:
        raise AppError("Provide a file or pasted_text", code="missing_content")
    dv, job, created = store_upload(
        db,
        p,
        data,
        filename=filename,
        title=title,
        document_type=document_type,
        source_url=source_url,
        publication_date=publication_date,
        retrieval_date=retrieval_date,
        academic_year=academic_year,
        cohort_applicability=cohort_applicability,
        document_id=document_id,
    )
    db.commit()
    if job is not None:
        enqueue(job.id)
    db.refresh(dv)
    return UploadResult(
        document_version=DocumentVersionOut.model_validate(dv), duplicate=not created, job_id=job.id if job else None
    )


class FetchIn(BaseModel):
    url: str
    title: str | None = None
    academic_year: str | None = None
    cohort_applicability: str | None = None


@router.post("/documents/fetch", response_model=UploadResult, status_code=201, tags=["documents"])
def fetch_document(body: FetchIn, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    """Import a public webpage or PDF through the controlled fetcher (allowlisted domains, no internal
    addresses, re-validated redirects, size/time limits). HTML is stored as extracted text."""
    from datetime import date

    from cfs.core.security import limit_upload
    from cfs.ingest.fetch import fetch, html_to_text

    p.require(Role.editor)
    limit_upload(p)
    f = fetch(body.url)
    if f.content_type in ("text/html", "application/xhtml+xml"):
        text, page_title = html_to_text(f.data.decode("utf-8", errors="replace"), f.final_url)
        data, name, dtype = text.encode("utf-8"), "webpage.txt", DocumentType.webpage_extract
    else:
        data, page_title = f.data, None
        name = "download.pdf" if f.content_type == "application/pdf" else "download.txt"
        dtype = DocumentType.other
    dv, job, created = store_upload(
        db,
        p,
        data,
        filename=name,
        title=body.title or page_title or f.final_url,
        document_type=dtype,
        source_url=f.final_url,
        retrieval_date=date.today(),
        academic_year=body.academic_year,
        cohort_applicability=body.cohort_applicability,
    )
    audit(
        db,
        p,
        "document.fetched",
        "document_version",
        dv.id,
        {"url": body.url, "final_url": f.final_url, "redirects": f.redirects, "content_type": f.content_type},
    )
    db.commit()
    if job is not None:
        enqueue(job.id)
    db.refresh(dv)
    return UploadResult(
        document_version=DocumentVersionOut.model_validate(dv), duplicate=not created, job_id=job.id if job else None
    )


@router.get("/document-versions/{dv_id}", response_model=DocumentVersionOut, tags=["documents"])
def get_document_version(dv_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    return get_scoped(db, DocumentVersion, dv_id, p, "Document version")


@router.patch("/document-versions/{dv_id}", response_model=DocumentVersionOut, tags=["documents"])
def patch_document_version(
    dv_id: uuid.UUID, body: DocumentVersionPatch, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    p.require(Role.editor)
    dv = get_scoped(db, DocumentVersion, dv_id, p, "Document version")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(dv, k, v)
    audit(
        db, p, "document.metadata_updated", "document_version", dv.id, body.model_dump(mode="json", exclude_unset=True)
    )
    db.commit()
    return dv


@router.get("/document-versions/{dv_id}/file", tags=["documents"])
def get_file(dv_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    dv = get_scoped(db, DocumentVersion, dv_id, p, "Document version")
    data = get_store().get(dv.object_key)
    # Served as an attachment-safe type; PDFs render in the client with PDF.js (no active content).
    headers = {
        "Content-Disposition": f'inline; filename="{dv.sha256[:12]}.{dv.object_key.rsplit(".", 1)[-1]}"',
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, max-age=3600",
    }
    media = dv.mime_type if dv.mime_type == "application/pdf" else "application/octet-stream"
    return Response(content=data, media_type=media, headers=headers)


@router.get("/document-versions/{dv_id}/pages", response_model=list[PageOut], tags=["documents"])
def get_pages(
    dv_id: uuid.UUID, page: int | None = None, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    get_scoped(db, DocumentVersion, dv_id, p, "Document version")
    q = select(DocumentPage).where(DocumentPage.document_version_id == dv_id).order_by(DocumentPage.page_number)
    if page is not None:
        q = q.where(DocumentPage.page_number == page)
    return db.scalars(q).all()


@router.get("/document-versions/{dv_id}/sources", response_model=list[SourceOut], tags=["sources"])
def list_sources(dv_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    get_scoped(db, DocumentVersion, dv_id, p, "Document version")
    return db.scalars(select(CurriculumSource).where(CurriculumSource.document_version_id == dv_id)).all()


@router.get("/versions/{version_id}/sources", tags=["sources"])
def version_sources(
    version_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
) -> list[dict[str, Any]]:
    get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    rows = db.execute(
        select(CurriculumSource, DocumentVersion, Document)
        .join(DocumentVersion, DocumentVersion.id == CurriculumSource.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(CurriculumSource.curriculum_version_id == version_id)
    ).all()
    return [
        {
            "source": SourceOut.model_validate(s).model_dump(mode="json"),
            "document_version": DocumentVersionOut.model_validate(dv).model_dump(mode="json"),
            "document": {
                "id": str(d.id),
                "title": d.title,
                "document_type": d.document_type.value,
                "source_url": d.source_url,
            },
        }
        for s, dv, d in rows
    ]


@router.post("/document-versions/{dv_id}/sources", response_model=SourceOut, status_code=201, tags=["sources"])
def assign_source(
    dv_id: uuid.UUID, body: SourceAssign, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    """Explicitly assign a document version to a curriculum version. Sources are never merged implicitly."""
    p.require(Role.editor)
    dv = get_scoped(db, DocumentVersion, dv_id, p, "Document version")
    cv = get_scoped(db, CurriculumVersion, body.curriculum_version_id, p, "Curriculum version")
    if db.scalar(
        select(CurriculumSource).where(
            CurriculumSource.document_version_id == dv.id, CurriculumSource.curriculum_version_id == cv.id
        )
    ):
        raise Conflict("This document version is already assigned to that curriculum version")
    if (
        dv.academic_year
        and cv.academic_year
        and dv.academic_year != cv.academic_year
        and body.authority == "authoritative"
        and not body.is_exploratory_cross_version
    ):
        raise Conflict(
            f"Document academic year {dv.academic_year} differs from version {cv.academic_year}. "
            "Assign it as supporting/historical, or mark it as exploratory cross-version mapping.",
            code="academic_year_mismatch",
        )
    src = CurriculumSource(
        organization_id=p.org_id,
        document_version_id=dv.id,
        curriculum_version_id=cv.id,
        authority=SourceAuthority(body.authority),
        applicability=body.applicability,
        is_exploratory_cross_version=body.is_exploratory_cross_version,
        notes=body.notes,
        approval_status=ApprovalStatus.approved,
        assigned_by=p.user_id,
    )
    db.add(src)
    db.flush()
    job = create_job(db, p.org_id, "resolve_source", {"curriculum_source_id": str(src.id)}, user_id=p.user_id)
    audit(db, p, "source.assigned", "curriculum_source", src.id, body.model_dump(mode="json"))
    db.commit()
    enqueue(job.id)
    return src


# ── jobs ──


@router.get("/jobs", response_model=list[JobOut], tags=["jobs"])
def list_jobs(
    status: str | None = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    q = select(Job).where(Job.organization_id == p.org_id).order_by(Job.created_at.desc()).limit(limit)
    if status:
        q = q.where(Job.status == status)
    return db.scalars(q).all()


@router.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    return get_scoped(db, Job, job_id, p, "Job")


@router.get("/jobs/{job_id}/events", response_model=list[JobEventOut], tags=["jobs"])
def list_job_events(
    job_id: uuid.UUID, after: int = 0, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    get_scoped(db, Job, job_id, p, "Job")
    return db.scalars(
        select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > after).order_by(JobEvent.id)
    ).all()


def _stream_principal(
    job_id: uuid.UUID, request: Request, st: str | None = None, db: Session = Depends(get_db)
) -> Principal:
    """EventSource cannot send Authorization headers: accept a short-lived stream token (?st=) instead."""
    from cfs.core.config import get_settings
    from cfs.core.oidc import verify_stream_token

    if st:
        return verify_stream_token(st, job_id)
    if get_settings().auth_mode == "oidc":
        from cfs.core.oidc import Unauthorized

        raise Unauthorized("Stream token required")
    return get_principal(request, db, None, None)


@router.get("/jobs/{job_id}/stream", tags=["jobs"])
async def stream_job(
    job_id: uuid.UUID, request: Request, db: Session = Depends(get_db), p: Principal = Depends(_stream_principal)
):
    """Server-sent events. Progress is persisted, so clients can reconnect with Last-Event-ID."""
    get_scoped(db, Job, job_id, p, "Job")
    last = int(request.headers.get("last-event-id") or 0)

    async def gen():
        nonlocal last
        while True:
            if await request.is_disconnected():
                return
            s = get_sessionmaker()()
            try:
                events = s.scalars(
                    select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > last).order_by(JobEvent.id)
                ).all()
                job = s.get(Job, job_id)
                for ev in events:
                    last = ev.id
                    payload = JobEventOut.model_validate(ev).model_dump(mode="json")
                    payload["job"] = {
                        "status": job.status.value,
                        "progress": float(job.progress),
                        "stage": job.current_stage,
                    }
                    yield f"id: {ev.id}\nevent: job\ndata: {json.dumps(payload)}\n\n"
                terminal = job.status in (JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled, JobStatus.partial)
            finally:
                s.close()
            if terminal:
                yield "event: end\ndata: {}\n\n"
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(0.75)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/jobs/{job_id}/cancel", response_model=JobOut, tags=["jobs"])
def cancel_job(job_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    job = get_scoped(db, Job, job_id, p, "Job")
    if job.status == JobStatus.queued:
        job.status = JobStatus.cancelled
        db.add(JobEvent(job_id=job.id, status="cancelled", message="cancelled before start"))
    elif job.status == JobStatus.running:
        job.cancel_requested = True
        db.add(
            JobEvent(
                job_id=job.id,
                stage=job.current_stage,
                status="info",
                message="cancellation requested; stops at the next stage boundary",
            )
        )
    db.commit()
    return job


@router.post("/jobs/{job_id}/retry", response_model=JobOut, tags=["jobs"])
def retry(job_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    job = get_scoped(db, Job, job_id, p, "Job")
    retry_job(db, job)
    db.commit()
    enqueue(job.id)
    db.refresh(job)
    return job


# ── review inbox ──


@router.get("/reviews", response_model=Page, tags=["reviews"])
def list_reviews(
    status: list[str] = Query(default=["open", "deferred"]),
    kind: str | None = None,
    curriculum_version_id: uuid.UUID | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    q = select(ReviewItem).where(ReviewItem.organization_id == p.org_id, ReviewItem.status.in_(status))
    if kind:
        q = q.where(ReviewItem.kind == kind)
    if curriculum_version_id:
        q = q.where(ReviewItem.curriculum_version_id == curriculum_version_id)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(ReviewItem.created_at, ReviewItem.id).limit(limit).offset(offset)).all()
    return Page(items=[ReviewItemOut.model_validate(i) for i in items], total=total, limit=limit, offset=offset)


@router.post("/reviews/{item_id}/decision", response_model=ReviewDecisionOut, tags=["reviews"])
def decide_review(
    item_id: uuid.UUID, body: ReviewDecisionIn, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    item = get_scoped(db, ReviewItem, item_id, p, "Review item")
    applied = decide(
        db, p, item, body.decision, rationale=body.rationale, edited_payload=body.edited_payload, choice=body.choice
    )
    db.commit()
    db.refresh(item)
    return ReviewDecisionOut(item=ReviewItemOut.model_validate(item), applied=applied)
