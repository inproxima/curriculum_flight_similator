"""Celery application and task entry points (imported by services/worker)."""

from celery import Celery
from celery.signals import worker_ready

from cfs.core.config import get_settings

settings = get_settings()


def celery_config(s) -> dict:
    conf = {
        "task_acks_late": True,  # redelivery on crash; handlers are idempotent (jobs are claimed atomically)
        "task_reject_on_worker_lost": True,
        "worker_prefetch_multiplier": 1,
        "task_ignore_result": True,  # results and progress live in PostgreSQL, never in the broker
        "task_always_eager": s.task_always_eager,
        "task_default_queue": "celery",
    }
    if s.broker_url.startswith("sqs://"):
        # SQS has no remote control or events; cancellation and progress are application-level (PostgreSQL).
        opts = {
            "region": s.aws_region,
            "visibility_timeout": s.sqs_visibility_timeout,
            "wait_time_seconds": 20,
            "polling_interval": 1,
        }
        if s.sqs_queue_url:
            opts["predefined_queues"] = {"celery": {"url": s.sqs_queue_url}}
        conf.update(
            broker_transport_options=opts,
            worker_enable_remote_control=False,
            worker_send_task_events=False,
            task_send_sent_event=False,
            broker_connection_retry_on_startup=True,
        )
    else:
        conf["broker_transport_options"] = {"visibility_timeout": s.sqs_visibility_timeout}
    return conf


celery_app = Celery("cfs", broker=settings.broker_url)
celery_app.conf.update(celery_config(settings))


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
