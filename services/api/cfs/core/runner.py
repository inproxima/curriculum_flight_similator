"""In-process job runner (CFS_JOB_RUNNER=thread), for hosting the API as a single service with no broker or
separate worker.

Jobs stay durable: PostgreSQL holds their state, claims are atomic (cfs.core.jobs.run_job), and a sweeper thread
reclaims jobs whose process died (stale heartbeat) and picks up queued jobs this process has not taken yet.
Run the API with one process (uvicorn --workers 1) so the job pool size means what it says.
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from cfs.core.config import get_settings

log = logging.getLogger(__name__)

SWEEP_SECONDS = 60

_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None
_pending: set[uuid.UUID] = set()  # submitted to the pool and not finished yet
_stop = threading.Event()
_sweeper: threading.Thread | None = None


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=max(1, get_settings().job_threads), thread_name_prefix="cfs-job")
        return _pool


def submit(job_id: uuid.UUID | str) -> None:
    jid = uuid.UUID(str(job_id))
    with _lock:
        if jid in _pending:
            return
        _pending.add(jid)
    _get_pool().submit(_run, jid)


def _run(jid: uuid.UUID) -> None:
    from cfs.core.jobs import run_job

    try:
        run_job(jid)
    except Exception:  # noqa: BLE001  (run_job records failures itself; this only guards the pool thread)
        log.exception("job %s crashed outside its handler", jid)
    finally:
        with _lock:
            _pending.discard(jid)


def sweep() -> None:
    """Reclaim jobs left running by a dead process, then take every queued job this process has not taken."""
    import cfs.handlers  # noqa: F401  (register job handlers)
    from cfs.core.db import get_sessionmaker
    from cfs.core.jobs import reclaim_stale_jobs
    from cfs.models import Job
    from cfs.models.enums import JobStatus

    db = get_sessionmaker()()
    try:
        reclaim_stale_jobs(db)
        queued = db.query(Job.id).filter(Job.status == JobStatus.queued).order_by(Job.created_at).all()
    finally:
        db.close()
    for (jid,) in queued:
        submit(jid)


def _sweep_loop() -> None:
    while not _stop.is_set():
        try:
            sweep()
        except Exception:  # noqa: BLE001
            log.exception("job sweep failed")
        _stop.wait(SWEEP_SECONDS)


def start() -> None:
    global _sweeper
    with _lock:
        if _sweeper is not None:
            return
        _stop.clear()
        _sweeper = threading.Thread(target=_sweep_loop, name="cfs-job-sweeper", daemon=True)
    _sweeper.start()
    log.info("in-process job runner started (%s threads)", get_settings().job_threads)


def stop() -> None:
    global _sweeper, _pool
    _stop.set()
    with _lock:
        _sweeper = None
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)
