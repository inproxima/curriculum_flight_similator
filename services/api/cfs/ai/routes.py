"""Model routes: which provider/model serves each task, with prices and fallbacks.

Assignments are starting points to evaluate, not claims that a provider is best at a task.
Model IDs were verified against both accounts' model lists on 2026-09-26 (`/v1/models`).
Prices (USD per 1M tokens) come from the providers' published pricing pages, retrieved 2026-09-26:
  OpenAI: https://developers.openai.com/api/docs/pricing · Anthropic: platform.claude.com model overview.
Override with CFS_AI_ROUTES_JSON / CFS_AI_PRICES_JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import lru_cache

from cfs.core.config import get_settings


@dataclass(frozen=True)
class Route:
    name: str
    provider: str  # openai | anthropic
    model: str
    purpose: str
    effort: str | None = None  # reasoning/effort hint where the provider supports it
    max_output_tokens: int = 16000
    fallback: tuple[str, str] | None = None  # (provider, model) used only if allowed for the content
    kind: str = "generate"  # generate | embed


DEFAULT_ROUTES: dict[str, Route] = {
    r.name: r
    for r in [
        Route(
            "classify",
            "openai",
            "gpt-6-luna",
            "Lightweight document classification and metadata normalization",
            max_output_tokens=4000,
            fallback=("anthropic", "claude-sonnet-5"),
            effort="low",
        ),
        Route(
            "extract",
            "openai",
            "gpt-6-sol",
            "Structured extraction, grounded questions, tool selection",
            max_output_tokens=32000,
            fallback=("anthropic", "claude-sonnet-5"),
        ),
        Route(
            "synthesize",
            "openai",
            "gpt-6-astra",
            "Difficult interpretation, cross-course synthesis, complex scenario explanations (used selectively)",
            max_output_tokens=32000,
            fallback=("anthropic", "claude-opus-5-5"),
        ),
        Route(
            "pedagogy",
            "anthropic",
            "claude-sonnet-5",
            "Pedagogical feedback and drafting proposed course or assessment changes",
            fallback=("openai", "gpt-6-sol"),
            effort="medium",
        ),
        Route(
            "critique",
            "anthropic",
            "claude-opus-5-5",
            "Optional deep critique of a consequential proposal",
            fallback=("openai", "gpt-6-astra"),
            effort="high",
        ),
        Route("embed", "openai", "text-embedding-3-small", "Embeddings (1,536 dimensions)", kind="embed"),
    ]
}

DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "output": 0.50},
    "gpt-6-sol": {"input": 2.00, "cached_input": 0.20, "output": 10.00},
    "gpt-6-astra": {"input": 10.00, "cached_input": 1.00, "output": 50.00},
    "text-embedding-3-small": {"input": 0.02},
    "text-embedding-3-large": {"input": 0.13},
    "claude-sonnet-5": {"input": 2.00, "cached_input": 0.20, "output": 10.00},
    "claude-opus-5-5": {"input": 4.00, "cached_input": 0.20, "output": 20.00},
}
EMBEDDING_DIM = 1536


@lru_cache
def routes() -> dict[str, Route]:
    out = dict(DEFAULT_ROUTES)
    raw = get_settings().ai_routes_json
    if raw:
        for name, cfg in json.loads(raw).items():
            base = out.get(name) or Route(name, cfg["provider"], cfg["model"], cfg.get("purpose", name))
            fb = cfg.get("fallback")
            out[name] = replace(
                base,
                provider=cfg.get("provider", base.provider),
                model=cfg.get("model", base.model),
                effort=cfg.get("effort", base.effort),
                fallback=tuple(fb) if fb else (None if "fallback" in cfg else base.fallback),
            )
    return out


@lru_cache
def prices() -> dict[str, dict[str, float]]:
    p = dict(DEFAULT_PRICES)
    raw = get_settings().ai_prices_json
    if raw:
        p.update(json.loads(raw))
    return p


def cost_usd(model: str, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float | None:
    pr = prices().get(model)
    if not pr:
        return None
    uncached = max(0, input_tokens - cached_input_tokens)
    c = uncached * pr.get("input", 0) + cached_input_tokens * pr.get("cached_input", pr.get("input", 0))
    c += output_tokens * pr.get("output", 0)
    return round(c / 1_000_000, 6)


@dataclass
class Availability:
    configured: dict[str, bool] = field(default_factory=dict)
    allowlisted: dict[str, bool] = field(default_factory=dict)


def provider_state() -> Availability:
    s = get_settings()
    allow = {p.strip() for p in s.ai_provider_allowlist.split(",") if p.strip()}
    return Availability(
        configured={"openai": bool(s.openai_api_key), "anthropic": bool(s.anthropic_api_key)},
        allowlisted={p: p in allow for p in ("openai", "anthropic")},
    )
