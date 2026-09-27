"""ModelGateway: the only path from application code to model providers.

Responsibilities
- Route resolution with capability/availability checks (configured key + provider allowlist).
- Content policy: a provider is used only if EVERY document whose excerpts are included permits it
  (document_versions.ai_providers). Fallback to the route's second provider happens only under the same
  check. Content is never silently sent to a second provider.
- Spend limits (per job and per workspace per calendar month), bounded concurrency per provider.
- Caching keyed by caller-supplied cache keys (source hashes + version + model config + prompt version).
- A model_runs audit row for every call (including cache hits, failures, refusals) with tokens, cost,
  returned model identity, evidence ids, and tool calls. Hidden reasoning is never stored.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cfs.ai.providers.base import GenRequest, GenResult, OutputInvalid, ProviderError, ProviderRefusal, ToolLog
from cfs.ai.routes import EMBEDDING_DIM, Route, cost_usd, provider_state, routes
from cfs.core.config import get_settings
from cfs.core.db import get_sessionmaker
from cfs.core.errors import AppError, ProviderUnconfigured
from cfs.models import DocumentVersion, ModelRun, ToolCall


class SpendLimitExceeded(AppError):
    status_code = 429
    code = "ai_spend_limit"


class ModelRefused(AppError):
    status_code = 422
    code = "model_refusal"


class ModelOutputInvalid(AppError):
    status_code = 502
    code = "model_output_invalid"


_semaphores: dict[str, threading.BoundedSemaphore] = {}
_sem_lock = threading.Lock()


def _sem(provider: str) -> threading.BoundedSemaphore:
    with _sem_lock:
        if provider not in _semaphores:
            _semaphores[provider] = threading.BoundedSemaphore(get_settings().ai_max_concurrency)
        return _semaphores[provider]


def cache_key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def document_policy(db: Session, doc_version_ids: Iterable[uuid.UUID]) -> set[str] | None:
    """Providers every included document permits (None = no document restriction)."""
    allowed: set[str] | None = None
    ids = list({uuid.UUID(str(i)) for i in doc_version_ids})
    if not ids:
        return None
    for (policy,) in db.execute(select(DocumentVersion.ai_providers).where(DocumentVersion.id.in_(ids))):
        if policy is None:
            continue
        allowed = set(policy) if allowed is None else allowed & set(policy)
    return allowed


def candidates_for(route: Route, doc_policy: set[str] | None) -> tuple[list[tuple[str, str]], list[str]]:
    st = provider_state()
    opts = [(route.provider, route.model)] + ([route.fallback] if route.fallback else [])
    usable, reasons = [], []
    for prov, model in opts:
        if not st.configured.get(prov):
            reasons.append(f"{prov}: no API key configured")
        elif not st.allowlisted.get(prov):
            reasons.append(f"{prov}: not in the workspace provider allowlist")
        elif doc_policy is not None and prov not in doc_policy:
            reasons.append(f"{prov}: not permitted by the source documents' AI policy")
        else:
            usable.append((prov, model))
    return usable, reasons


def route_status() -> list[dict[str, Any]]:
    out = []
    for r in routes().values():
        usable, reasons = candidates_for(r, None)
        out.append(
            {
                "name": r.name,
                "provider": r.provider,
                "model": r.model,
                "purpose": r.purpose,
                "fallback": list(r.fallback) if r.fallback else None,
                "kind": r.kind,
                "available": bool(usable),
                "active": list(usable[0]) if usable else None,
                "unavailable_reasons": reasons,
            }
        )
    return out


def _month_start() -> datetime:
    n = datetime.now(UTC)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def spend(db: Session, org_id: uuid.UUID, job_id: uuid.UUID | None = None) -> dict[str, float]:
    month = (
        db.scalar(
            select(func.coalesce(func.sum(ModelRun.cost_usd), 0)).where(
                ModelRun.organization_id == org_id, ModelRun.created_at >= _month_start()
            )
        )
        or 0
    )
    job = 0
    if job_id:
        job = db.scalar(select(func.coalesce(func.sum(ModelRun.cost_usd), 0)).where(ModelRun.job_id == job_id)) or 0
    return {"month_usd": float(month), "job_usd": float(job)}


def _check_limits(db: Session, org_id: uuid.UUID, job_id: uuid.UUID | None, cap: float | None) -> None:
    s = get_settings()
    sp = spend(db, org_id, job_id)
    if sp["month_usd"] >= s.ai_monthly_budget_usd:
        raise SpendLimitExceeded(
            f"Monthly AI budget reached (${sp['month_usd']:.2f} of ${s.ai_monthly_budget_usd:.2f}).", details=sp
        )
    limit = cap if cap is not None else s.ai_max_cost_per_job_usd
    if job_id and sp["job_usd"] >= limit:
        raise SpendLimitExceeded(f"Per-job AI cost limit reached (${sp['job_usd']:.2f} of ${limit:.2f}).", details=sp)


def _record(org_id: uuid.UUID, **kw: Any) -> uuid.UUID:
    """Accounting uses its own session so records survive caller rollbacks."""
    tool_calls: list[ToolLog] = kw.pop("tool_calls", [])
    s = get_sessionmaker()()
    try:
        run = ModelRun(organization_id=org_id, **kw)
        s.add(run)
        s.flush()
        for t in tool_calls:
            s.add(
                ToolCall(
                    model_run_id=run.id,
                    tool_name=t.name,
                    arguments=t.arguments,
                    result_summary=t.result_summary,
                    status=t.status,
                )
            )
        s.commit()
        return run.id
    finally:
        s.close()


def generate(
    db: Session,
    org_id: uuid.UUID,
    route_name: str,
    req: GenRequest,
    *,
    purpose: str,
    prompt_version: str,
    schema_version: str | None = None,
    doc_version_ids: Iterable[uuid.UUID] = (),
    evidence_ids: Iterable[str] = (),
    job_id: uuid.UUID | None = None,
    use_cache_key: str | None = None,
    cost_cap_usd: float | None = None,
) -> tuple[GenResult, uuid.UUID]:
    route = routes()[route_name]
    usable, reasons = candidates_for(route, document_policy(db, doc_version_ids))
    if not usable:
        raise ProviderUnconfigured(
            f"AI route '{route_name}' is unavailable: " + "; ".join(reasons),
            details={"route": route_name, "reasons": reasons},
        )
    base = {
        "route": route_name,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "job_id": job_id,
        "purpose": purpose,
        "evidence_ids": sorted(set(map(str, evidence_ids))),
    }

    if use_cache_key and not req.tools:
        hit = db.scalar(
            select(ModelRun)
            .where(ModelRun.organization_id == org_id, ModelRun.cache_key == use_cache_key, ModelRun.status == "ok")
            .order_by(ModelRun.created_at.desc())
        )
        if hit is not None and hit.output is not None:
            run_id = _record(
                org_id,
                provider=hit.provider,
                requested_model=hit.requested_model,
                returned_model=hit.returned_model,
                cache_key=use_cache_key,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0,
                status="cache_hit",
                output={"from_run": str(hit.id)},
                **base,
            )
            o = hit.output
            return GenResult(
                text=o.get("text", ""),
                parsed=o.get("parsed"),
                returned_model=hit.returned_model or "",
                stop_reason="cache_hit",
            ), run_id

    _check_limits(db, org_id, job_id, cost_cap_usd)
    last_err: Exception | None = None
    fallback_from = None
    for provider, model in usable:
        t0 = time.monotonic()
        try:
            with _sem(provider):
                if provider == "openai":
                    from cfs.ai.providers import openai_adapter as ad
                else:
                    from cfs.ai.providers import anthropic_adapter as ad
                res = ad.generate(model, route, req)
        except ProviderError as e:
            _record(
                org_id,
                provider=provider,
                requested_model=model,
                status="provider_error",
                error=str(e)[:500],
                duration_ms=int((time.monotonic() - t0) * 1000),
                fallback_from=fallback_from,
                **base,
            )
            last_err, fallback_from = e, f"{provider}:{model}"
            continue
        except ProviderRefusal as e:
            _record(
                org_id,
                provider=provider,
                requested_model=model,
                status="refused",
                error=str(e)[:500],
                duration_ms=int((time.monotonic() - t0) * 1000),
                fallback_from=fallback_from,
                **base,
            )
            raise ModelRefused(str(e)) from e
        except OutputInvalid as e:
            _record(
                org_id,
                provider=provider,
                requested_model=model,
                status="invalid_output",
                error=str(e)[:500],
                duration_ms=int((time.monotonic() - t0) * 1000),
                fallback_from=fallback_from,
                **base,
            )
            raise ModelOutputInvalid(str(e)) from e
        c = cost_usd(model, res.input_tokens, res.output_tokens, res.cached_input_tokens)
        run_id = _record(
            org_id,
            provider=provider,
            requested_model=model,
            returned_model=res.returned_model,
            cache_key=use_cache_key,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            cached_input_tokens=res.cached_input_tokens,
            cost_usd=c,
            status="ok",
            output={"text": res.text[:200000], "parsed": res.parsed, "steps": res.steps},
            duration_ms=int((time.monotonic() - t0) * 1000),
            fallback_from=fallback_from,
            tool_calls=res.tool_calls,
            **base,
        )
        return res, run_id
    raise ProviderUnconfigured(
        f"All providers for route '{route_name}' failed: {last_err}", details={"route": route_name}
    )


def embed(
    db: Session,
    org_id: uuid.UUID,
    texts: list[str],
    *,
    doc_version_ids: Iterable[uuid.UUID] = (),
    job_id: uuid.UUID | None = None,
) -> tuple[list[list[float]], str]:
    """Embeddings never fall back across providers (different vector spaces)."""
    route = routes()["embed"]
    usable, reasons = candidates_for(route, document_policy(db, doc_version_ids))
    usable = [u for u in usable if u[0] == route.provider]
    if not usable or not get_settings().ai_embeddings_enabled:
        raise ProviderUnconfigured("Embeddings unavailable: " + ("; ".join(reasons) or "disabled"))
    _check_limits(db, org_id, job_id, None)
    from cfs.ai.providers import openai_adapter

    t0 = time.monotonic()
    vecs, tokens = openai_adapter.embed(route.model, texts, EMBEDDING_DIM)
    _record(
        org_id,
        route="embed",
        provider=route.provider,
        requested_model=route.model,
        returned_model=route.model,
        prompt_version="embed-v1",
        input_tokens=tokens,
        output_tokens=0,
        cost_usd=cost_usd(route.model, tokens, 0),
        status="ok",
        job_id=job_id,
        purpose="embeddings",
        duration_ms=int((time.monotonic() - t0) * 1000),
        evidence_ids=[],
    )
    return vecs, route.model


def provider_status() -> dict[str, Any]:
    st = provider_state()
    rs = route_status()
    return {
        "implemented": True,
        "providers": st.configured,
        "allowlist": st.allowlisted,
        "routes": rs,
        "retrieval_mode": "hybrid" if any(r["name"] == "embed" and r["available"] for r in rs) else "full_text_only",
        "message": "Configured model calls send selected excerpts of assigned documents to the provider shown "
        "for each route. A local deployment is not offline AI.",
    }


def require_route(name: str) -> Route:
    r = routes()[name]
    usable, reasons = candidates_for(r, None)
    if not usable:
        raise ProviderUnconfigured(f"AI route '{name}' is unavailable: " + "; ".join(reasons))
    return r
