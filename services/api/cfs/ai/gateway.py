"""ModelGateway interface (Phase 5 placeholder).

Only the interface and route configuration exist in this pass. No provider is called; every AI
action reports `provider_unconfigured` rather than fabricating output. Model IDs below are the
spec's *starting assignments* and have NOT been verified against live provider catalogs or account access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from cfs.core.config import get_settings
from cfs.core.errors import ProviderUnconfigured


@dataclass(frozen=True)
class Route:
    name: str
    provider: str
    model: str
    purpose: str
    verified: bool = False


ROUTES: dict[str, Route] = {
    r.name: r
    for r in [
        Route("classify", "openai", "gpt-6-luna", "Lightweight document classification and metadata normalization"),
        Route("extract", "openai", "gpt-6-sol", "Structured extraction, grounded questions, tool selection"),
        Route("synthesize", "openai", "gpt-6-astra", "Difficult interpretation, cross-course synthesis (selective)"),
        Route("pedagogy", "anthropic", "claude-sonnet-5", "Pedagogical feedback and drafting proposed changes"),
        Route("critique", "anthropic", "claude-opus-5-5", "Optional deep critique of consequential proposals"),
        Route("embed", "openai", "text-embedding-3-small", "Embeddings, 1,536 dimensions"),
    ]
}


class ProviderAdapter(Protocol):
    name: str

    def generate(self, route: Route, messages: list[dict[str, Any]], *, schema: dict | None = None) -> dict: ...


def provider_status() -> dict[str, Any]:
    s = get_settings()
    configured = {"openai": bool(s.openai_api_key), "anthropic": bool(s.anthropic_api_key)}
    return {
        "implemented": False,
        "providers": configured,
        "routes": [{**r.__dict__, "available": False} for r in ROUTES.values()],
        "retrieval_mode": "full_text_only",
        "message": "AI features are not implemented in this build (Phase 5). The map, evidence, review, and "
        "deterministic scenario analysis work without any provider.",
    }


def require_route(name: str) -> Route:
    raise ProviderUnconfigured(
        f"AI route '{name}' is unavailable: model providers are not configured or not implemented in this build.",
        details=provider_status(),
    )
