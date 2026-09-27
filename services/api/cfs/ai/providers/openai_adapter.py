"""OpenAI adapter (Responses API). Stateless: store=False, encrypted reasoning items are passed back."""

from __future__ import annotations

import json
from typing import Any

import openai

from cfs.ai.providers.base import GenRequest, GenResult, OutputInvalid, ProviderError, ProviderRefusal, run_tool
from cfs.ai.routes import Route
from cfs.core.config import get_settings

_client: openai.OpenAI | None = None


def client() -> openai.OpenAI:
    global _client
    if _client is None:
        s = get_settings()
        _client = openai.OpenAI(api_key=s.openai_api_key, timeout=s.ai_timeout_seconds, max_retries=s.ai_max_retries)
    return _client


def generate(route_model: str, route: Route, req: GenRequest) -> GenResult:
    items: list[Any] = [{"role": m["role"], "content": m["content"]} for m in req.history]
    items.append({"role": "user", "content": req.user})
    tools = [
        {"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters, "strict": True}
        for t in req.tools
    ]
    by_name = {t.name: t for t in req.tools}
    kwargs: dict[str, Any] = {
        "model": route_model,
        "instructions": req.system,
        "store": False,
        "include": ["reasoning.encrypted_content"],
        "max_output_tokens": req.max_output_tokens or route.max_output_tokens,
    }
    if tools:
        kwargs["tools"] = tools
    if req.schema is not None:
        kwargs["text"] = {
            "format": {"type": "json_schema", "name": req.schema_name, "schema": req.schema, "strict": True}
        }
    res = GenResult(text="", parsed=None, returned_model=route_model, stop_reason="", steps=0)
    for step in range(req.max_tool_steps + 1):
        if req.on_step:
            req.on_step("model_call", {"step": step})
        try:
            r = client().responses.create(input=items, **kwargs)
        except openai.BadRequestError as e:
            raise OutputInvalid(f"OpenAI rejected the request: {e.message}") from e
        except (
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
            openai.APITimeoutError,
        ) as e:
            raise ProviderError(f"OpenAI unavailable: {type(e).__name__}") from e
        except openai.APIStatusError as e:
            raise ProviderError(f"OpenAI error {e.status_code}") from e
        res.steps += 1
        res.returned_model = r.model
        if r.usage:
            res.input_tokens += r.usage.input_tokens
            res.output_tokens += r.usage.output_tokens
            det = getattr(r.usage, "input_tokens_details", None)
            res.cached_input_tokens += getattr(det, "cached_tokens", 0) or 0
        for o in r.output:
            if o.type == "message":
                for c in o.content:
                    if c.type == "refusal":
                        raise ProviderRefusal(c.refusal)
        calls = [o for o in r.output if o.type == "function_call"]
        if not calls:
            if r.status == "incomplete":
                reason = getattr(r.incomplete_details, "reason", "incomplete")
                raise OutputInvalid(f"OpenAI response incomplete: {reason}")
            res.text = r.output_text or ""
            res.stop_reason = r.status
            break
        if step == req.max_tool_steps:
            raise OutputInvalid("Tool-step limit reached before a final answer")
        items.extend(o.model_dump(exclude_none=True) for o in r.output)
        for call in calls:
            spec = by_name.get(call.name)
            try:
                args = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                args = None
            if spec is None or args is None:
                out = json.dumps({"error": "Unknown tool or invalid JSON arguments"})
                from cfs.ai.providers.base import ToolLog

                res.tool_calls.append(ToolLog(call.name, {"raw": call.arguments[:500]}, {"error": "invalid"}, "error"))
            else:
                if req.on_step:
                    req.on_step("tool_call", {"tool": call.name, "arguments": args})
                out, log = run_tool(spec, args)
                res.tool_calls.append(log)
            items.append({"type": "function_call_output", "call_id": call.call_id, "output": out})
    if req.schema is not None:
        try:
            res.parsed = json.loads(res.text)
        except json.JSONDecodeError as e:
            raise OutputInvalid("Structured output was not valid JSON") from e
    return res


def embed(model: str, texts: list[str], dimensions: int) -> tuple[list[list[float]], int]:
    try:
        r = client().embeddings.create(model=model, input=texts, dimensions=dimensions)
    except (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError, openai.APITimeoutError) as e:
        raise ProviderError(f"OpenAI embeddings unavailable: {type(e).__name__}") from e
    return [d.embedding for d in r.data], r.usage.total_tokens
