"""Anthropic adapter (Messages API) for Claude routes.

- Structured output via output_config.format; strict tools. Native citations are NOT combined with
  structured output (incompatible); grounding uses application-owned evidence ids validated server-side.
- Thinking is left to each model's default (adaptive on Claude Sonnet 5 / Claude Opus 5.5); effort is set
  explicitly because Claude Opus 5.5 defaults to medium.
- Forced tool_choice is never used (Claude Opus 5.5 rejects it); tools run with the default "auto".
- A `refusal` stop reason is surfaced, never silently retried elsewhere.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

from cfs.ai.providers.base import (
    GenRequest,
    GenResult,
    OutputInvalid,
    ProviderError,
    ProviderRefusal,
    ToolLog,
    run_tool,
)
from cfs.ai.routes import Route
from cfs.core.config import get_settings

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        s = get_settings()
        _client = anthropic.Anthropic(
            api_key=s.anthropic_api_key, timeout=s.ai_timeout_seconds, max_retries=s.ai_max_retries
        )
    return _client


def generate(route_model: str, route: Route, req: GenRequest) -> GenResult:
    messages: list[dict[str, Any]] = [{"role": m["role"], "content": m["content"]} for m in req.history]
    messages.append({"role": "user", "content": req.user})
    tools = [
        {"name": t.name, "description": t.description, "input_schema": t.parameters, "strict": True} for t in req.tools
    ]
    by_name = {t.name: t for t in req.tools}
    output_config: dict[str, Any] = {}
    effort = req.effort or route.effort
    if effort:
        output_config["effort"] = effort
    if req.schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": req.schema}
    kwargs: dict[str, Any] = {
        "model": route_model,
        "system": req.system,
        "max_tokens": req.max_output_tokens or route.max_output_tokens,
    }
    if tools:
        kwargs["tools"] = tools
    if output_config:
        kwargs["output_config"] = output_config
    res = GenResult(text="", parsed=None, returned_model=route_model, stop_reason="", steps=0)
    for step in range(req.max_tool_steps + 1):
        if req.on_step:
            req.on_step("model_call", {"step": step})
        try:
            with client().messages.stream(messages=messages, **kwargs) as stream:
                r = stream.get_final_message()
        except anthropic.BadRequestError as e:
            raise OutputInvalid(f"Anthropic rejected the request: {e.message}") from e
        except (
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APITimeoutError,
        ) as e:
            raise ProviderError(f"Anthropic unavailable: {type(e).__name__}") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Anthropic error {e.status_code}") from e
        res.steps += 1
        res.returned_model = r.model
        res.input_tokens += r.usage.input_tokens + (r.usage.cache_read_input_tokens or 0)
        res.cached_input_tokens += r.usage.cache_read_input_tokens or 0
        res.output_tokens += r.usage.output_tokens
        res.stop_reason = r.stop_reason or ""
        if r.stop_reason == "refusal":
            cat = getattr(getattr(r, "stop_details", None), "category", None)
            raise ProviderRefusal(f"Claude declined this request (category: {cat})")
        if r.stop_reason == "max_tokens":
            raise OutputInvalid("Claude response truncated at max_tokens")
        if r.stop_reason != "tool_use":
            res.text = "".join(b.text for b in r.content if b.type == "text")
            break
        if step == req.max_tool_steps:
            raise OutputInvalid("Tool-step limit reached before a final answer")
        messages.append({"role": "assistant", "content": r.content})
        results = []
        for b in r.content:
            if b.type != "tool_use":
                continue
            spec = by_name.get(b.name)
            if spec is None:
                results.append(
                    {"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": "Unknown tool"}
                )
                res.tool_calls.append(ToolLog(b.name, dict(b.input or {}), {"error": "unknown tool"}, "error"))
                continue
            args = dict(b.input or {})
            if req.on_step:
                req.on_step("tool_call", {"tool": b.name, "arguments": args})
            out, log = run_tool(spec, args)
            res.tool_calls.append(log)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": out,
                    **({"is_error": True} if log.status == "error" else {}),
                }
            )
        messages.append({"role": "user", "content": results})  # all results in a single user message
    if req.schema is not None:
        try:
            res.parsed = json.loads(res.text)
        except json.JSONDecodeError as e:
            raise OutputInvalid("Structured output was not valid JSON") from e
    return res
