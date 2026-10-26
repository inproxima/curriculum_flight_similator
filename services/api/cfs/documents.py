"""Document upload and storage service."""

from __future__ import annotations

import hashlib
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.core.audit import audit
from cfs.core.auth import Principal
from cfs.core.config import get_settings
from cfs.core.errors import AppError, Conflict
from cfs.core.jobs import create_job
from cfs.ingest.extract import EXT_FOR_MIME, UnsupportedFile, detect_mime
from cfs.models import CurriculumSource, CurriculumVersion, Document, DocumentVersion, Job
from cfs.models.enums import ApprovalStatus, DocumentType, SourceAuthority
from cfs.storage import content_key, get_store


def store_upload(
    db: Session,
    p: Principal,
    data: bytes,
    *,
    filename: str | None,
    title: str | None,
    document_type: DocumentType = DocumentType.other,
    source_url: str | None = None,
    publication_date: date | None = None,
    retrieval_date: date | None = None,
    academic_year: str | None = None,
    cohort_applicability: str | None = None,
    is_synthetic: bool = False,
    document_id: uuid.UUID | None = None,
    ingest: bool = True,
) -> tuple[DocumentVersion, Job | None, bool]:
    """Returns (document_version, ingest_job, created). Duplicate content within the org is not re-stored.
    `ingest=False` stores the original without queuing ingestion (a caller that runs the stages itself)."""
    if len(data) == 0:
        raise AppError("Empty file", code="empty_file")
    if len(data) > get_settings().max_upload_bytes:
        raise AppError("File exceeds the upload size limit", code="file_too_large", status=413)
    try:
        mime = detect_mime(data, filename)
    except UnsupportedFile as e:
        raise AppError(str(e), code="unsupported_media_type", status=415) from e
    sha = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(DocumentVersion).where(DocumentVersion.organization_id == p.org_id, DocumentVersion.sha256 == sha)
    )
    if existing:
        return existing, None, False

    safe_name = (filename or "upload").replace("/", "_").replace("\\", "_")[:200]
    if document_id:
        doc = db.get(Document, document_id)
        if doc is None or doc.organization_id != p.org_id:
            raise AppError("Document not found", status=404, code="not_found")
    else:
        doc = Document(
            organization_id=p.org_id, title=title or safe_name, document_type=document_type, source_url=source_url
        )
        db.add(doc)
        db.flush()
    key = content_key(str(p.org_id), sha, EXT_FOR_MIME.get(mime, "bin"))
    get_store().put(key, data, mime)
    dv = DocumentVersion(
        organization_id=p.org_id,
        document_id=doc.id,
        sha256=sha,
        object_key=key,
        mime_type=mime,
        size_bytes=len(data),
        original_filename=safe_name,
        publication_date=publication_date,
        retrieval_date=retrieval_date,
        academic_year=academic_year,
        cohort_applicability=cohort_applicability,
        is_synthetic=is_synthetic,
        uploaded_by=p.user_id,
    )
    db.add(dv)
    db.flush()
    job = None
    if ingest:
        job = create_job(
            db,
            p.org_id,
            "ingest_document",
            {"document_version_id": str(dv.id)},
            user_id=p.user_id,
            idempotency_key=f"ingest:{dv.id}",
        )
    audit(db, p, "document.uploaded", "document_version", dv.id, {"sha256": sha, "mime": mime})
    return dv, job, True


def _same_year(a: str, b: str) -> bool:
    """Academic years compared by value ("2025-26" = "2025–2026"); unparseable ones as written."""
    from cfs.outlines.edition import parse_academic_year

    pa, pb = parse_academic_year(a), parse_academic_year(b)
    return pa == pb if pa is not None and pb is not None else a.strip() == b.strip()


def assign_source(
    db: Session,
    p: Principal,
    dv: DocumentVersion,
    cv: CurriculumVersion,
    *,
    authority: SourceAuthority | str,
    applicability: str | None = None,
    notes: str | None = None,
    exploratory: bool = False,
    enqueue_resolve: bool = True,
) -> tuple[CurriculumSource, Job | None]:
    """Explicitly assign a document version to a curriculum version (never inferred, never merged implicitly).
    An authoritative source must match the version's academic year unless it is marked exploratory. Returns the
    source and, when `enqueue_resolve`, the resolve job the caller must enqueue after commit."""
    authority = SourceAuthority(authority)
    if db.scalar(
        select(CurriculumSource).where(
            CurriculumSource.document_version_id == dv.id, CurriculumSource.curriculum_version_id == cv.id
        )
    ):
        raise Conflict("This document version is already assigned to that curriculum version")
    if (
        dv.academic_year
        and cv.academic_year
        and not _same_year(dv.academic_year, cv.academic_year)
        and authority == SourceAuthority.authoritative
        and not exploratory
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
        authority=authority,
        applicability=applicability,
        is_exploratory_cross_version=exploratory,
        notes=notes,
        approval_status=ApprovalStatus.approved,
        assigned_by=p.user_id,
    )
    db.add(src)
    db.flush()
    job = None
    if enqueue_resolve:
        job = create_job(db, p.org_id, "resolve_source", {"curriculum_source_id": str(src.id)}, user_id=p.user_id)
    audit(
        db,
        p,
        "source.assigned",
        "curriculum_source",
        src.id,
        {
            "document_version_id": str(dv.id),
            "curriculum_version_id": str(cv.id),
            "authority": authority.value,
            "applicability": applicability,
            "is_exploratory_cross_version": exploratory,
            "notes": notes,
        },
    )
    return src, job
