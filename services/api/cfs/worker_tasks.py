"""Celery application and task entry points (imported by services/worker)."""

from celery import Celery
from celery.signals import worker_ready

from cfs.core.config import get_settings

settings = get_settings()
celery_app = Celery("cfs", broker=settings.broker_url)
celery_app.conf.update(
    task_acks_late=True,  # redelivery on crash; handlers are idempotent
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,  # results live in PostgreSQL, not the broker
    broker_transport_options={"visibility_timeout": 3600},
    task_always_eager=settings.task_always_eager,
)


@celery_app.task(name="cfs.run_job")
def run_job_task(job_id: str) -> None:
    import cfs.handlers  # noqa: F401  (register job handlers)
    from cfs.core.jobs import run_job

    run_job(job_id)


@worker_ready.connect
def _reclaim(**_):
    import cfs.handlers  # noqa: F401
    from cfs.core.db import get_sessionmaker
    from cfs.core.jobs import enqueue, reclaim_stale_jobs
    from cfs.models import Job
    from cfs.models.enums import JobStatus

    db = get_sessionmaker()()
    try:
        reclaim_stale_jobs(db)
        for job in db.query(Job).filter(Job.status == JobStatus.queued).all():
            enqueue(job.id)
    finally:
        db.close()
