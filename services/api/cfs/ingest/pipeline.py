"""Durable document-ingestion pipeline (spec §8).

Stages (each idempotent, recorded in ingest_stage_runs keyed on document version + stage + parser version):
  validate → extract_pages → quality_ocr → chunk → extract_candidates → evidence → resolve → index → refresh

Document contents are untrusted data: nothing in a document can change pipeline behaviour.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cfs.core.errors import AppError
from cfs.core.jobs import JobContext, register
from cfs.ingest import ocr
from cfs.ingest.candidates import EXTRACTOR_VERSION, extract_candidates
from cfs.ingest.extract import PARSER_VERSION, extract_pages, pdf_line_boxes
from cfs.ingest.text import find_span, normalize
from cfs.models import (
    CurriculumSource,
    DocumentChunk,
    DocumentPage,
    DocumentVersion,
    EvidenceSpan,
    IngestStageRun,
    Job,
    ReviewItem,
)
from cfs.models.enums import OcrState, ProcessingStatus, ReviewItemKind
from cfs.storage import get_store

PIPELINE_VERSION = f"{PARSER_VERSION}+{EXTRACTOR_VERSION}"


def _done(db: Session, dv_id: uuid.UUID, stage: str) -> IngestStageRun | None:
    return db.scalar(
        select(IngestStageRun).where(
            IngestStageRun.document_version_id == dv_id,
            IngestStageRun.stage == stage,
            IngestStageRun.parser_version == PIPELINE_VERSION,
        )
    )


def _mark(db: Session, dv_id: uuid.UUID, stage: str, output: dict | None = None) -> None:
    db.add(IngestStageRun(document_version_id=dv_id, stage=stage, parser_version=PIPELINE_VERSION, output=output))
    db.commit()


def review_item(
    db: Session, *, org_id: uuid.UUID, kind: ReviewItemKind, title: str, dedupe_key: str, **kw: Any
) -> ReviewItem | None:
    if db.scalar(select(ReviewItem.id).where(ReviewItem.dedupe_key == dedupe_key)):
        return None
    item = ReviewItem(organization_id=org_id, kind=kind, title=title, dedupe_key=dedupe_key, **kw)
    db.add(item)
    db.flush()
    return item


def make_span(
    db: Session,
    dv: DocumentVersion,
    page_number: int,
    quote: str,
    *,
    raw: bytes | None = None,
    page_text: str | None = None,
) -> EvidenceSpan | None:
    """Create an evidence span only if the quote is found in the stored page text (documented normalization)."""
    if page_text is None:
        page = db.scalar(
            select(DocumentPage).where(
                DocumentPage.document_version_id == dv.id, DocumentPage.page_number == page_number
            )
        )
        if page is None:
            return None
        page_text = page.text
    loc = find_span(page_text, quote)
    if loc is None:
        return None
    start, end = loc
    existing = db.scalar(
        select(EvidenceSpan).where(
            EvidenceSpan.document_version_id == dv.id,
            EvidenceSpan.page_number == page_number,
            EvidenceSpan.char_start == start,
            EvidenceSpan.char_end == end,
        )
    )
    if existing:
        return existing
    bbox = None
    if dv.mime_type == "application/pdf" and raw is not None:
        bbox = pdf_line_boxes(raw, page_number, page_text[start:end])
    span = EvidenceSpan(
        organization_id=dv.organization_id,
        document_version_id=dv.id,
        page_number=page_number,
        char_start=start,
        char_end=end,
        quote=page_text[start:end],
        bbox=bbox,
    )
    db.add(span)
    db.flush()
    return span


SECTION_RE = re.compile(r"^\s*(?:[A-Z]{2,5}\s?\d{3}[A-Z]?\s*:|Program learning outcomes|[A-Z][A-Za-z ]{2,60}$)")


def _chunks(pages: list[DocumentPage], max_words: int = 350) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for p in pages:
        for line in p.text.splitlines():
            if SECTION_RE.match(line) and len(line) < 120 or cur is None or len(cur["words"]) > max_words:
                if cur and cur["words"]:
                    out.append(cur)
                cur = {
                    "section": line.strip()[:500],
                    "words": [],
                    "page_start": p.page_number,
                    "page_end": p.page_number,
                }
            cur["words"].extend(line.split())
            cur["page_end"] = p.page_number
    if cur and cur["words"]:
        out.append(cur)
    return out


@register("ingest_document")
def ingest_document(db: Session, job: Job, ctx: JobContext) -> dict[str, Any]:
    dv = db.get(DocumentVersion, uuid.UUID(job.payload["document_version_id"]))
    if dv is None or dv.organization_id != job.organization_id:
        raise AppError("document version not found in job scope")
    dv.processing_status = ProcessingStatus.processing
    db.commit()
    store = get_store()
    warnings: list[str] = []

    # 1. validate
    ctx.stage("validate", 0.05, "Validating stored original")
    raw = store.get(dv.object_key)
    if hashlib.sha256(raw).hexdigest() != dv.sha256:
        raise AppError("stored object hash does not match recorded hash")

    # 2. extract pages
    ctx.stage("extract_pages", 0.15, "Extracting page text")
    if not _done(db, dv.id, "extract_pages"):
        db.execute(delete(DocumentPage).where(DocumentPage.document_version_id == dv.id))
        pages = extract_pages(raw, dv.mime_type)
        for pt in pages:
            db.add(
                DocumentPage(
                    document_version_id=dv.id,
                    page_number=pt.page_number,
                    text=pt.text,
                    normalized_text=normalize(pt.text),
                    extraction_method=pt.method,
                    parser_version=PARSER_VERSION,
                    char_count=len(pt.text),
                    width=pt.width,
                    height=pt.height,
                    ocr_state=OcrState.needed if pt.low_quality else OcrState.not_needed,
                )
            )
        dv.page_count = len(pages)
        db.commit()
        _mark(db, dv.id, "extract_pages", {"pages": len(pages)})

    # 3. quality check + OCR fallback
    ctx.stage("quality_ocr", 0.3, "Checking extraction quality")
    if not _done(db, dv.id, "quality_ocr"):
        needing = list(
            db.scalars(
                select(DocumentPage).where(
                    DocumentPage.document_version_id == dv.id, DocumentPage.ocr_state == OcrState.needed
                )
            )
        )
        for page in needing:
            ctx.check_cancel()
            text = ocr.ocr_pdf_page(raw, page.page_number) if dv.mime_type == "application/pdf" else None
            if text and len(normalize(text)) >= 10:
                page.text, page.normalized_text = text, normalize(text)
                page.extraction_method, page.parser_version = "tesseract", ocr.OCR_VERSION
                page.char_count, page.ocr_state = len(text), OcrState.done
                ctx.event(f"OCR applied to page {page.page_number}")
            else:
                page.ocr_state = OcrState.unavailable
                warnings.append(f"page {page.page_number} unreadable")
                review_item(
                    db,
                    org_id=dv.organization_id,
                    kind=ReviewItemKind.unreadable_page,
                    title=f"Page {page.page_number} could not be read",
                    detail="No usable text layer, and OCR was unavailable or produced no text. "
                    "Content on this page is not represented in the curriculum.",
                    document_version_id=dv.id,
                    payload={"page_number": page.page_number},
                    dedupe_key=f"unreadable:{dv.id}:{page.page_number}",
                )
        db.commit()
        _mark(db, dv.id, "quality_ocr", {"ocr_pages": len(needing)})

    pages = list(
        db.scalars(
            select(DocumentPage).where(DocumentPage.document_version_id == dv.id).order_by(DocumentPage.page_number)
        )
    )

    # 4. section-aware chunks (FTS vectors are generated columns)
    ctx.stage("chunk", 0.45, "Building searchable chunks")
    if not _done(db, dv.id, "chunk"):
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == dv.id))
        for i, c in enumerate(_chunks(pages)):
            db.add(
                DocumentChunk(
                    organization_id=dv.organization_id,
                    document_version_id=dv.id,
                    ordinal=i,
                    section_title=c["section"],
                    text=" ".join(c["words"]),
                    page_start=c["page_start"],
                    page_end=c["page_end"],
                    token_count=int(len(c["words"]) * 1.33),
                )
            )
        db.commit()
        _mark(db, dv.id, "chunk")

    # 5. deterministic candidate extraction + 6. evidence validation
    ctx.stage("extract_candidates", 0.6, "Extracting candidate courses, requisites, and outcomes")
    run = _done(db, dv.id, "extract_candidates")
    if not run:
        by_page = {p.page_number: p.text for p in pages}
        cands = extract_candidates([(p.page_number, p.text) for p in pages])
        validated, dropped = [], 0
        for c in cands:
            if len(validated) % 25 == 0:
                ctx.check_cancel()
            span = make_span(db, dv, c["page"], c["quote"], raw=raw, page_text=by_page.get(c["page"], ""))
            if span is None:
                dropped += 1
                continue
            c["span_id"] = str(span.id)
            for f in c.get("fields", {}).values():
                fs = make_span(db, dv, f["page"], f["quote"], raw=raw, page_text=by_page.get(f["page"], ""))
                f["span_id"] = str(fs.id) if fs else None
            validated.append(c)
        db.commit()
        _mark(db, dv.id, "extract_candidates", {"candidates": validated, "dropped_unverifiable": dropped})
        run = _done(db, dv.id, "extract_candidates")
        if dropped:
            warnings.append(f"{dropped} candidates dropped: quote not found in source text")

    # metadata completeness
    if not dv.academic_year:
        review_item(
            db,
            org_id=dv.organization_id,
            kind=ReviewItemKind.missing_academic_year,
            title=f"Academic year missing for '{dv.original_filename}'",
            detail="Set the academic year and cohort applicability before relying on this source.",
            document_version_id=dv.id,
            dedupe_key=f"missing-year:{dv.id}",
        )
        db.commit()

    # 7. resolve against every curriculum version this document is explicitly assigned to
    ctx.stage("resolve", 0.8, "Resolving identities against assigned curriculum versions")
    from cfs.ingest.resolve import resolve_candidates

    resolved = {}
    for src in db.scalars(select(CurriculumSource).where(CurriculumSource.document_version_id == dv.id)):
        resolved[str(src.curriculum_version_id)] = resolve_candidates(db, dv, src, run.output["candidates"])
    db.commit()
    if not resolved:
        ctx.event("Document is not assigned to a curriculum version yet; candidates will be resolved on assignment.")

    ctx.stage("index", 0.9, "Indexing")
    ctx.event(
        "Full-text index updated. Embeddings skipped: no embedding provider configured (Phase 5).", status="warning"
    )
    ctx.stage("refresh", 0.97, "Refreshing curriculum graph")
    dv.processing_status = ProcessingStatus.partial if warnings else ProcessingStatus.processed
    db.commit()
    return {
        "pages": len(pages),
        "candidates": len(run.output["candidates"]),
        "resolved": resolved,
        "warnings": warnings,
        "partial": bool(warnings),
    }


@register("resolve_source")
def resolve_source(db: Session, job: Job, ctx: JobContext) -> dict[str, Any]:
    from cfs.ingest.resolve import resolve_candidates

    src = db.get(CurriculumSource, uuid.UUID(job.payload["curriculum_source_id"]))
    dv = db.get(DocumentVersion, src.document_version_id)
    ctx.stage("resolve", 0.5, "Resolving candidates for newly assigned curriculum version")
    run = _done(db, dv.id, "extract_candidates")
    if not run:
        return {"skipped": "document not yet processed; resolution will run when ingestion completes"}
    out = resolve_candidates(db, dv, src, run.output["candidates"])
    db.commit()
    return out
