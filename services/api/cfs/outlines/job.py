"""The `outline_import` job: read the document, extract, compare, reconcile, verify, plan. Nothing is written to the
curriculum here; the plan waits for a person to apply it."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from cfs.ai.extraction import run_extraction
from cfs.core.errors import AppError
from cfs.core.jobs import JobCancelled, JobContext, register
from cfs.graph.snapshot import load_snapshot
from cfs.ingest.pipeline import is_ingested, make_span, run_ingest
from cfs.models import Document, DocumentVersion, Entity, Job, OutlineImport
from cfs.models.base import utcnow
from cfs.models.enums import OutlineImportStatus
from cfs.outlines.agent import reconcile
from cfs.outlines.context import build_context, codes_found, course_candidates, pages_of
from cfs.outlines.edition import outline_edition
from cfs.outlines.plan import build_plan, default_selection, plan_hash
from cfs.outlines.project import preview
from cfs.outlines.state import course_state_hash
from cfs.storage import get_store

CLAIMABLE = (OutlineImportStatus.queued, OutlineImportStatus.running, OutlineImportStatus.failed)


@register("outline_import")
def outline_import_job(db, job: Job, ctx: JobContext) -> dict[str, Any]:
    imp_id = uuid.UUID(job.payload["import_id"])
    # Claim: only the import's current job may run it, and only while it is queued, running or failed (a retried
    # job). A stale or duplicate job never touches an import that has moved on (ready, applied, reverted, discarded).
    imp = db.scalar(
        select(OutlineImport)
        .where(OutlineImport.id == imp_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if imp is None or imp.organization_id != job.organization_id:
        raise AppError("outline import not found in job scope")
    if imp.job_id != job.id or imp.status not in CLAIMABLE:
        db.rollback()
        return {"skipped": f"import is {imp.status.value}; this job is not its current job"}
    imp.status, imp.error, imp.updated_at = OutlineImportStatus.running, None, utcnow()
    db.commit()
    try:
        return run_import(db, imp, ctx, job)
    except Exception as e:
        db.rollback()
        imp = db.get(OutlineImport, imp_id)
        imp.status = OutlineImportStatus.failed
        if isinstance(e, JobCancelled):
            imp.error = {"code": "cancelled", "message": "Cancelled."}
        elif isinstance(e, AppError):
            imp.error = {"code": e.code, "message": e.message, "details": e.details}
        else:  # internal detail stays in the server log and the job record, not in what every member sees
            imp.error = {"code": "internal_error", "message": "Planning failed because of an internal error."}
        imp.updated_at = utcnow()
        db.commit()
        raise


def run_import(db, imp: OutlineImport, ctx: JobContext, job: Job) -> dict[str, Any]:
    dv = db.get(DocumentVersion, imp.document_version_id)
    code_key = db.get(Entity, imp.course_entity_id).stable_key
    step = lambda *_: ctx.check_cancel()  # noqa: E731

    # 1. read the document (idempotent stages; skipped if already done)
    if not is_ingested(db, dv):
        run_ingest(db, dv, ctx.scaled(0.02, 0.3), resolve=False, job_id=job.id, skip_if_done=True)
    ctx.stage("read", 0.3, f"Read {dv.page_count or 0} page(s) of '{dv.original_filename}'")

    # 2. extract (cached by file hash; reused as-is on rerun)
    if imp.extraction is None:
        ctx.stage("extract", 0.32, "Extracting the outline's outcomes, assessments and topics (AI, verified quotes)")
        cands, report, run_id = run_extraction(db, dv, job_id=job.id, on_step=step)
        raw = get_store().get(dv.object_key)
        mine = course_candidates(cands, code_key, imp.options.get("alias_codes") or [])
        for c in mine:
            sp = make_span(db, dv, c["page"], c["quote"], raw=raw)
            c["span_id"] = str(sp.id) if sp else None
            for f in (c.get("fields") or {}).values():
                fs = make_span(db, dv, f["page"], f["quote"], raw=raw)
                f["span_id"] = str(fs.id) if fs else None
        imp.extraction = {
            "candidates": mine,
            "codes_found": codes_found(cands),
            "document": report.get("document"),
            "dropped": report.get("dropped", []),
            "accepted": report.get("accepted", {}),
            "notes": report.get("notes", []),
            "model": report.get("model"),
            "model_run_id": str(run_id),
        }
        imp.model_run_ids = [*imp.model_run_ids, str(run_id)]
        db.commit()
        ctx.event(
            f"Extraction verified {len(mine)} item(s) for {code_key}; "
            f"{len(report.get('dropped', []))} unverifiable item(s) dropped in the whole document."
        )
    ex = imp.extraction
    course_id = str(imp.course_entity_id)
    snap = load_snapshot(db, imp.curriculum_version_id)
    if course_id not in snap.entities:
        raise AppError("The course is no longer part of this curriculum version.", code="course_not_in_version")
    if not ex["candidates"]:
        found = [c for c in ex.get("codes_found") or [] if c]
        raise AppError(
            f"This document doesn't describe {code_key}"
            + (f" (it describes {', '.join(found[:6])})." if found else " (no course content could be verified)."),
            code="course_mismatch",
            details={"codes_found": found},
        )

    # 3. compare with the program
    ctx.stage("compare", 0.55, f"Comparing with what the program records for {code_key}")
    pages = pages_of(db, dv.id)
    title = db.get(Document, dv.document_id).title
    first = pages.get(min(pages)) if pages else None
    outline = outline_edition(
        dv, entered=imp.options.get("academic_year"), extracted=ex.get("document"), first_page=first, title=title
    )
    # what the document itself says, to check a year the user entered
    detected = outline_edition(dv, entered=None, extracted=ex.get("document"), first_page=first, title=title)
    outline["detected"] = {k: detected.get(k) for k in ("start_year", "academic_year", "source", "quote")}
    ictx = build_context(
        db,
        snap,
        course_id,
        dv,
        candidates=ex["candidates"],
        dropped=ex.get("dropped") or [],
        outline=outline,
        aliases=imp.options.get("alias_codes") or [],
    )

    # 4. the agent
    ctx.stage("reconcile", 0.62, "Asking the agent what the program needs from this outline")
    raw, run_id, model = reconcile(db, ictx, job_id=job.id, on_step=step)
    imp.model_run_ids = [*imp.model_run_ids, str(run_id)]

    # 5. verify and plan
    ctx.stage("plan", 0.86, "Checking every decision against the outline and the replacement rules")
    raw_pdf = get_store().get(dv.object_key) if dv.mime_type == "application/pdf" else None
    plan = build_plan(db, ictx, raw, options=imp.options, raw_pdf=raw_pdf, run_id=str(run_id), model=model)
    plan["summary"] = preview(snap, plan, set(default_selection(plan)), str(imp.id))
    imp.plan = plan
    imp.edition = plan["edition"]
    imp.warnings = plan["warnings"]
    imp.state_hash = course_state_hash(snap, course_id, extra=(dv.academic_year,))
    imp.plan_hash = plan_hash(plan)
    imp.status = OutlineImportStatus.ready
    imp.updated_at = utcnow()
    db.commit()
    s = plan["summary"]
    ctx.stage(
        "ready",
        0.99,
        f"Plan ready: {len(plan['items'])} item(s), {s['selected']} pre-selected; "
        f"closes {len(s['closes'])} of {s['missing_before']} missing piece(s)",
    )
    return {"import_id": str(imp.id), "items": len(plan["items"]), "selected": s["selected"]}
