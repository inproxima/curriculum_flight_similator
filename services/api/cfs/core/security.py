"""Security middleware and per-user rate limits.

Rate limits are in-process token buckets per (user, bucket). With several API tasks the effective limit is
per task; that bounds cost/abuse without extra infrastructure. Model spend is independently capped by the
AI budget limits in the gateway.
"""

from __future__ import annotations

import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from cfs.core.auth import Principal
from cfs.core.config import get_settings
from cfs.core.errors import AppError


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


_buckets: dict[tuple[str, str], tuple[float, float]] = {}
_lock = threading.Lock()


def rate_limit(p: Principal, bucket: str, per_minute: int) -> None:
    if per_minute <= 0:
        return
    now = time.monotonic()
    key = (str(p.user_id), bucket)
    with _lock:
        tokens, last = _buckets.get(key, (float(per_minute), now))
        tokens = min(float(per_minute), tokens + (now - last) * per_minute / 60.0)
        if tokens < 1:
            raise RateLimited(
                f"Too many {bucket} requests; try again shortly.",
                details={"retry_after_seconds": int((1 - tokens) * 60 / per_minute) + 1},
            )
        _buckets[key] = (tokens - 1, now)


def limit_ai(p: Principal) -> None:
    rate_limit(p, "ai", get_settings().rate_limit_ai_per_minute)


def limit_upload(p: Principal) -> None:
    rate_limit(p, "upload", get_settings().rate_limit_upload_per_minute)


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/api/") and "Content-Security-Policy" not in h:
            # API responses are data (JSON/SSE/PDF); nothing should execute or be framed.
            h["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; sandbox"
        if get_settings().env == "production":
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/api/") and "Cache-Control" not in h:
            h["Cache-Control"] = "no-store"
        return resp
