from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from cfs.ai.gateway import provider_status
from cfs.core.config import get_settings
from cfs.core.db import get_db
from cfs.ingest.ocr import ocr_available

router = APIRouter()


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", tags=["system"])
def ready(db: Session = Depends(get_db)) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        db.execute(text("select 1"))
        ext = db.execute(text("select extversion from pg_extension where extname='vector'")).scalar()
        checks["database"] = {"ok": True, "pgvector": ext}
    except Exception as e:  # noqa: BLE001
        checks["database"] = {"ok": False, "error": type(e).__name__}
    s = get_settings()
    if s.task_always_eager:
        checks["broker"] = {"ok": True, "mode": "eager (inline)"}
    else:
        try:
            import redis

            redis.Redis.from_url(s.broker_url, socket_timeout=2).ping()
            checks["broker"] = {"ok": True}
        except Exception as e:  # noqa: BLE001
            checks["broker"] = {"ok": False, "error": type(e).__name__}
    try:
        from cfs.storage import get_store

        get_store().healthcheck()
        checks["storage"] = {"ok": True, "backend": s.storage_backend}
    except Exception as e:  # noqa: BLE001
        checks["storage"] = {"ok": False, "error": type(e).__name__}
    checks["ocr"] = {"ok": True, "tesseract": ocr_available()}
    checks["ai"] = {"ok": True, **{k: v for k, v in provider_status().items() if k in ("implemented", "providers")}}
    return {"ready": all(c.get("ok") for c in checks.values()), "checks": checks}
