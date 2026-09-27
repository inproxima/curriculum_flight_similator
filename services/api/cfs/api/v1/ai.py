"""AI endpoints: status/usage, document extraction and mapping jobs, assistant conversations, critique,
analysis explanation, and per-document provider policy."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cfs.ai.gateway import provider_status, spend
from cfs.core.audit import audit
from cfs.core.auth import Principal, get_principal
from cfs.core.config import get_settings
from cfs.core.db import get_db
from cfs.core.errors import AppError
from cfs.core.jobs import create_job, enqueue
from cfs.core.scope import get_scoped
from cfs.core.security import limit_ai
from cfs.models import (
    AnalysisRun,
    Conversation,
    CurriculumVersion,
    DocumentVersion,
    Message,
    ModelRun,
    Program,
    Scenario,
    ToolCall,
)
from cfs.models.enums import Role

router = APIRouter(tags=["ai"])


def _require_ai(route: str) -> None:
    from cfs.ai.gateway import require_route

    require_route(route)


@router.get("/ai/status")
def ai_status(p: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> dict[str, Any]:
    s = get_settings()
    return {
        **provider_status(),
        "limits": {"monthly_budget_usd": s.ai_monthly_budget_usd, "max_cost_per_job_usd": s.ai_max_cost_per_job_usd},
        "spend": spend(db, p.org_id),
    }


@router.get("/ai/usage")
def ai_usage(p: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.execute(
        select(
            ModelRun.route,
            ModelRun.provider,
            ModelRun.requested_model,
            ModelRun.status,
            func.count(),
            func.coalesce(func.sum(ModelRun.input_tokens), 0),
            func.coalesce(func.sum(ModelRun.output_tokens), 0),
            func.coalesce(func.sum(ModelRun.cost_usd), 0),
        )
        .where(ModelRun.organization_id == p.org_id)
        .group_by(ModelRun.route, ModelRun.provider, ModelRun.requested_model, ModelRun.status)
    ).all()
    recent = db.scalars(
        select(ModelRun).where(ModelRun.organization_id == p.org_id).order_by(ModelRun.created_at.desc()).limit(40)
    ).all()
    return {
        "by_route": [
            {
                "route": r,
                "provider": pr,
                "model": m,
                "status": st,
                "runs": n,
                "input_tokens": int(i),
                "output_tokens": int(o),
                "cost_usd": float(c),
            }
            for r, pr, m, st, n, i, o, c in rows
        ],
        "recent": [
            {
                "id": str(r.id),
                "route": r.route,
                "purpose": r.purpose,
                "provider": r.provider,
                "requested_model": r.requested_model,
                "returned_model": r.returned_model,
                "status": r.status,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": float(r.cost_usd) if r.cost_usd is not None else None,
                "duration_ms": r.duration_ms,
                "fallback_from": r.fallback_from,
                "error": r.error,
                "prompt_version": r.prompt_version,
                "evidence_ids": len(r.evidence_ids or []),
                "created_at": r.created_at,
            }
            for r in recent
        ],
    }


@router.get("/ai/runs/{run_id}")
def model_run(run_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    r = get_scoped(db, ModelRun, run_id, p, "Model run")
    calls = db.scalars(select(ToolCall).where(ToolCall.model_run_id == r.id).order_by(ToolCall.created_at)).all()
    return {
        "id": str(r.id),
        "route": r.route,
        "purpose": r.purpose,
        "provider": r.provider,
        "requested_model": r.requested_model,
        "returned_model": r.returned_model,
        "status": r.status,
        "prompt_version": r.prompt_version,
        "schema_version": r.schema_version,
        "evidence_ids": r.evidence_ids,
        "tool_calls": [
            {"tool": c.tool_name, "arguments": c.arguments, "status": c.status, "result_summary": c.result_summary}
            for c in calls
        ],
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cost_usd": float(r.cost_usd) if r.cost_usd is not None else None,
        "error": r.error,
    }


class ProviderPolicy(BaseModel):
    ai_providers: list[Literal["openai", "anthropic"]] | None


@router.put("/document-versions/{dv_id}/ai-policy")
def set_policy(
    dv_id: uuid.UUID, body: ProviderPolicy, p: Principal = Depends(get_principal), db: Session = Depends(get_db)
):
    p.require(Role.editor)
    dv = get_scoped(db, DocumentVersion, dv_id, p, "Document version")
    dv.ai_providers = body.ai_providers
    audit(db, p, "document.ai_policy", "document_version", dv.id, {"ai_providers": body.ai_providers})
    db.commit()
    return {"document_version_id": str(dv.id), "ai_providers": dv.ai_providers}


@router.post("/document-versions/{dv_id}/ai-extract", status_code=202)
def ai_extract(dv_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Model-assisted extraction. Sends this document's extracted text to the `extract` route's provider."""
    limit_ai(p)
    p.require(Role.editor)
    _require_ai("extract")
    dv = get_scoped(db, DocumentVersion, dv_id, p, "Document version")
    if dv.ai_providers == []:
        raise AppError(
            "This document's AI policy forbids sending it to any model provider.", code="ai_forbidden", status=403
        )
    job = create_job(db, p.org_id, "ai_extract_document", {"document_version_id": str(dv.id)}, user_id=p.user_id)
    audit(db, p, "ai.extract_requested", "document_version", dv.id, {})
    db.commit()
    enqueue(job.id)
    return {"job_id": str(job.id)}


@router.post("/versions/{version_id}/ai-mappings", status_code=202)
def ai_mappings(version_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    limit_ai(p)
    p.require(Role.editor)
    _require_ai("synthesize")
    get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    job = create_job(db, p.org_id, "ai_propose_mappings", {"curriculum_version_id": str(version_id)}, user_id=p.user_id)
    db.commit()
    enqueue(job.id)
    return {"job_id": str(job.id)}


class ConversationIn(BaseModel):
    curriculum_version_id: uuid.UUID
    scenario_id: uuid.UUID | None = None
    title: str | None = None


def _conv_view(c: Conversation) -> dict:
    return {
        "id": str(c.id),
        "program_id": str(c.program_id),
        "curriculum_version_id": str(c.curriculum_version_id),
        "scenario_id": str(c.scenario_id) if c.scenario_id else None,
        "title": c.title,
        "created_at": c.created_at,
    }


@router.post("/conversations", status_code=201)
def create_conversation(body: ConversationIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    v = get_scoped(db, CurriculumVersion, body.curriculum_version_id, p, "Curriculum version")
    if body.scenario_id:
        s = get_scoped(db, Scenario, body.scenario_id, p, "Scenario")
        if s.base_version_id != v.id:
            raise AppError("Scenario is not based on this curriculum version", code="scope_mismatch")
    c = Conversation(
        organization_id=p.org_id,
        program_id=v.program_id,
        curriculum_version_id=v.id,
        scenario_id=body.scenario_id,
        title=body.title,
        created_by=p.user_id,
    )
    db.add(c)
    db.commit()
    return _conv_view(c)


@router.get("/conversations")
def list_conversations(
    curriculum_version_id: uuid.UUID | None = None, p: Principal = Depends(get_principal), db: Session = Depends(get_db)
):
    q = select(Conversation).where(Conversation.organization_id == p.org_id).order_by(Conversation.created_at.desc())
    if curriculum_version_id:
        q = q.where(Conversation.curriculum_version_id == curriculum_version_id)
    return [_conv_view(c) for c in db.scalars(q.limit(50))]


@router.get("/conversations/{cid}/messages")
def list_messages(cid: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    c = get_scoped(db, Conversation, cid, p, "Conversation")
    return {
        "conversation": _conv_view(c),
        "messages": [
            {"id": str(m.id), "role": m.role, "content": m.content, "created_at": m.created_at}
            for m in db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at))
        ],
    }


class MessageIn(BaseModel):
    text: str
    mode: Literal["explore", "investigate", "simulate"] = "explore"


@router.post("/conversations/{cid}/messages", status_code=202)
def post_message(cid: uuid.UUID, body: MessageIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Queues an assistant turn. Stream progress from /jobs/{job_id}/stream; the answer is stored as a message."""
    from cfs.ai.assistant import MODE_ROUTE

    c = get_scoped(db, Conversation, cid, p, "Conversation")
    _require_ai(MODE_ROUTE[body.mode])
    limit_ai(p)
    if not body.text.strip() or len(body.text) > 4000:
        raise AppError("Message must be 1–4000 characters", code="invalid_message")
    m = Message(conversation_id=c.id, role="user", content={"text": body.text, "mode": body.mode})
    db.add(m)
    db.flush()
    job = create_job(
        db,
        p.org_id,
        "assistant_turn",
        {"conversation_id": str(c.id), "message_id": str(m.id), "mode": body.mode},
        user_id=p.user_id,
    )
    if not c.title:
        c.title = body.text[:80]
    db.commit()
    enqueue(job.id)
    return {"message_id": str(m.id), "job_id": str(job.id)}


@router.post("/messages/{mid}/critique", status_code=202)
def critique(mid: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    limit_ai(p)
    m = db.get(Message, mid)
    if m is None:
        raise AppError("Message not found", status=404, code="not_found")
    get_scoped(db, Conversation, m.conversation_id, p, "Conversation")
    if m.role != "assistant" or not m.content.get("proposed_changes") and not m.content.get("answer"):
        raise AppError("Only assistant answers can be critiqued", code="invalid_target")
    _require_ai("critique")
    job = create_job(db, p.org_id, "assistant_critique", {"message_id": str(m.id)}, user_id=p.user_id)
    db.commit()
    enqueue(job.id)
    return {"job_id": str(job.id)}


@router.post("/analyses/{run_id}/explain")
def explain(run_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    limit_ai(p)
    from cfs.ai.assistant import explain_run

    run = get_scoped(db, AnalysisRun, run_id, p, "Analysis run")
    _require_ai("synthesize")
    s = db.get(Scenario, run.scenario_id) if run.scenario_id else None
    return explain_run(db, p.org_id, run, s)


class ProgramIn(BaseModel):
    code: str
    name: str
    institution: str | None = None
    parent_degree: str | None = None
    description: str | None = None


@router.post("/programs", status_code=201, tags=["programs"])
def create_program(body: ProgramIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.require(Role.editor)
    if db.scalar(select(Program).where(Program.organization_id == p.org_id, Program.code == body.code)):
        raise AppError("A program with that code exists", code="conflict", status=409)
    prog = Program(organization_id=p.org_id, is_synthetic=False, **body.model_dump())
    db.add(prog)
    db.flush()
    audit(db, p, "program.created", "program", prog.id, body.model_dump())
    db.commit()
    return {"id": str(prog.id), **body.model_dump(), "is_synthetic": False}


class BulkDecision(BaseModel):
    ids: list[uuid.UUID]
    decision: Literal["accept", "reject", "defer"]
    rationale: str | None = None


@router.post("/reviews/bulk", tags=["reviews"])
def bulk_decide(body: BulkDecision, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Apply one decision to many items. Each item is decided and audited individually; failures are reported."""
    from cfs.models import ReviewItem
    from cfs.reviews import decide

    results = []
    # courses first so that outcomes/rules/groups referencing them can resolve
    items = [get_scoped(db, ReviewItem, i, p, "Review item") for i in body.ids[:500]]
    items.sort(key=lambda i: (0 if i.payload.get("entity_type") == "course" else 1, str(i.created_at)))
    for it in items:
        try:
            applied = decide(db, p, it, body.decision, rationale=body.rationale)
            db.commit()
            results.append({"id": str(it.id), "ok": True, "applied": applied})
        except AppError as e:
            db.rollback()
            results.append({"id": str(it.id), "ok": False, "error": e.message})
    return {"results": results, "ok": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results)}
