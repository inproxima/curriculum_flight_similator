"""Durable application-level jobs. PostgreSQL is the source of truth for job state; the broker only
delivers "run job X" messages. Handlers must be idempotent (duplicate delivery is expected with SQS)."""

from __future__ import annotations

import logging
import traceback
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cfs.core.config import get_settings
from cfs.core.db import get_sessionmaker
from cfs.models import Job, JobEvent
from cfs.models.base import utcnow
from cfs.models.enums import JobStatus

log = logging.getLogger(__name__)

HANDLERS: dict[str, Callable[[Session, Job, JobContext], dict[str, Any] | None]] = {}
STALE_AFTER = timedelta(minutes=10)


class JobCancelled(Exception):
    pass


class JobContext:
    def __init__(self, db: Session, job: Job):
        self.db = db
        self.job = job

    def stage(self, name: str, progress: float, message: str = "", data: dict | None = None) -> None:
        self.check_cancel()
        self.job.current_stage = name
        self.job.progress = progress
        self.job.heartbeat_at = utcnow()
        self.job.updated_at = utcnow()
        self.db.add(JobEvent(job_id=self.job.id, stage=name, status="running", message=message or name, data=data))
        self.db.commit()

    def event(self, message: str, *, status: str = "info", data: dict | None = None) -> None:
        self.db.add(
            JobEvent(job_id=self.job.id, stage=self.job.current_stage, status=status, message=message, data=data)
        )
        self.db.commit()

    def check_cancel(self) -> None:
        self.db.refresh(self.job, ["cancel_requested"])
        if self.job.cancel_requested:
            raise JobCancelled()


def register(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn

    return deco


def create_job(
    db: Session,
    org_id: uuid.UUID,
    kind: str,
    payload: dict,
    *,
    user_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> Job:
    if idempotency_key:
        existing = db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing:
            return existing
    job = Job(organization_id=org_id, kind=kind, payload=payload, created_by=user_id, idempotency_key=idempotency_key)
    db.add(job)
    db.flush()
    db.add(JobEvent(job_id=job.id, stage=None, status="queued", message=f"{kind} queued"))
    return job


def enqueue(job_id: uuid.UUID) -> None:
    """Dispatch after the creating transaction commits."""
    if get_settings().task_always_eager:
        run_job(job_id)
        return
    from cfs.worker_tasks import run_job_task

    run_job_task.delay(str(job_id))


def run_job(job_id: uuid.UUID | str) -> None:
    job_id = uuid.UUID(str(job_id))
    db = get_sessionmaker()()
    try:
        # Claim: only queued jobs (or reclaimed stale ones) transition to running. Duplicate deliveries no-op.
        claimed = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.queued)
            .values(status=JobStatus.running, attempts=Job.attempts + 1, heartbeat_at=utcnow(), updated_at=utcnow())
            .returning(Job.id)
        ).first()
        db.commit()
        if not claimed:
            log.info("job %s not claimable (duplicate delivery or already finished)", job_id)
            return
        job = db.get(Job, job_id)
        ctx = JobContext(db, job)
        handler = HANDLERS.get(job.kind)
        if handler is None:
            raise RuntimeError(f"no handler for job kind {job.kind}")
        try:
            result = handler(db, job, ctx) or {}
            job.status = JobStatus.partial if result.get("partial") else JobStatus.succeeded
            job.result = result
            job.progress = 1
            db.add(
                JobEvent(
                    job_id=job.id, stage=job.current_stage, status=job.status.value, message="finished", data=result
                )
            )
        except JobCancelled:
            db.rollback()
            job = db.get(Job, job_id)
            job.status = JobStatus.cancelled
            db.add(JobEvent(job_id=job.id, stage=job.current_stage, status="cancelled", message="cancelled by user"))
        except Exception as e:  # noqa: BLE001
            db.rollback()
            job = db.get(Job, job_id)
            job.status = JobStatus.failed
            job.error = f"{type(e).__name__}: {e}"
            db.add(
                JobEvent(
                    job_id=job.id,
                    stage=job.current_stage,
                    status="failed",
                    message=job.error,
                    data={"trace": traceback.format_exc(limit=5)},
                )
            )
            log.exception("job %s failed", job_id)
        job.updated_at = utcnow()
        db.commit()
    finally:
        db.close()


def retry_job(db: Session, job: Job) -> None:
    if job.status not in (JobStatus.failed, JobStatus.cancelled, JobStatus.partial):
        from cfs.core.errors import Conflict

        raise Conflict("Only failed, partial, or cancelled jobs can be retried")
    job.status = JobStatus.queued
    job.cancel_requested = False
    job.error = None
    db.add(JobEvent(job_id=job.id, stage=job.current_stage, status="queued", message="retry requested"))


def reclaim_stale_jobs(db: Session) -> int:
    """Crash recovery: running jobs without a recent heartbeat go back to queued (stages are idempotent)."""
    cutoff = utcnow() - STALE_AFTER
    rows = db.execute(
        update(Job)
        .where(Job.status == JobStatus.running, Job.heartbeat_at < cutoff)
        .values(status=JobStatus.queued)
        .returning(Job.id)
    ).all()
    for (jid,) in rows:
        db.add(JobEvent(job_id=jid, status="queued", message="reclaimed after worker interruption"))
    db.commit()
    return len(rows)
