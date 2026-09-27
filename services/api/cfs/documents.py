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
from cfs.core.errors import AppError
from cfs.core.jobs import create_job
from cfs.ingest.extract import EXT_FOR_MIME, UnsupportedFile, detect_mime
from cfs.models import Document, DocumentVersion, Job
from cfs.models.enums import DocumentType
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
) -> tuple[DocumentVersion, Job | None, bool]:
    """Returns (document_version, ingest_job, created). Duplicate content within the org is not re-stored."""
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
