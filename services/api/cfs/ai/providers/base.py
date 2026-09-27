"""Provider-neutral request/response types. Each adapter runs its own native tool loop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """Provider failed after SDK retries (network, 5xx, 429). Eligible for fallback."""


class ProviderRefusal(Exception):
    """The model declined. Never silently retried on another provider."""


class OutputInvalid(Exception):
    """Truncated or schema-invalid output."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # strict JSON schema (additionalProperties: false, all properties required)
    handler: Callable[[dict], Any]


@dataclass
class GenRequest:
    system: str
    user: str
    history: list[dict] = field(default_factory=list)  # [{"role": "user"|"assistant", "content": str}]
    schema: dict | None = None
    schema_name: str = "output"
    tools: list[ToolSpec] = field(default_factory=list)
    max_tool_steps: int = 8
    max_output_tokens: int | None = None
    effort: str | None = None
    # called before every model call and tool execution; may raise to cancel
    on_step: Callable[[str, dict], None] | None = None


@dataclass
class ToolLog:
    name: str
    arguments: dict
    result_summary: dict | None
    status: str  # ok | error


@dataclass
class GenResult:
    text: str
    parsed: dict | None
    returned_model: str
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    tool_calls: list[ToolLog] = field(default_factory=list)
    steps: int = 1


def run_tool(spec: ToolSpec, args: dict) -> tuple[str, ToolLog]:
    import json

    try:
        result = spec.handler(args)
        payload = result if isinstance(result, str) else json.dumps(result, default=str)
        summary = {"chars": len(payload)}
        if isinstance(result, dict):
            summary["keys"] = sorted(result.keys())[:12]
        return payload, ToolLog(spec.name, args, summary, "ok")
    except Exception as e:  # noqa: BLE001 — tool errors are returned to the model, not raised
        msg = f"Tool error: {type(e).__name__}: {e}"
        return json.dumps({"error": msg}), ToolLog(spec.name, args, {"error": msg[:300]}, "error")
