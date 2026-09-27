"""Worker entry point: `celery -A cfs_worker.app worker`. Uses the same `cfs` modules as the API."""
import cfs.handlers  # noqa: F401
from cfs.worker_tasks import celery_app as app  # noqa: F401
