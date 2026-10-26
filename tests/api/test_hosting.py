"""Single-service hosting (public prototype): public_demo sign-in, production config rules for it, the
PostgreSQL object store, the in-process job runner, and serving the built web app from the API."""

import threading
import time
import uuid

import pytest
from sqlalchemy import select
from starlette.requests import Request
from test_storage import _roundtrip

from cfs.core.auth import get_principal
from cfs.core.config import Settings, get_settings
from cfs.core.errors import Forbidden
from cfs.models import Job, Organization
from cfs.models.enums import JobStatus, Role


def _remote_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "client": ("203.0.113.5", 4321)})


# ── public_demo sign-in ──


def test_public_demo_lets_any_visitor_in_as_the_shared_admin(monkeypatch, db):
    s = get_settings()
    monkeypatch.setattr(s, "env", "development")
    with pytest.raises(Forbidden):  # local mode only accepts this machine
        get_principal(_remote_request(), db, None, None)

    monkeypatch.setattr(s, "auth_mode", "public_demo")
    p = get_principal(_remote_request(), db, None, None)
    assert p.role == Role.admin
    assert db.get(Organization, p.org_id).slug == "local-workspace"
    # Naming another user is ignored: everyone is the same shared user.
    assert get_principal(_remote_request(), db, str(uuid.uuid4()), None) == p


def test_public_demo_production_config():
    common = {"env": "production", "auth_mode": "public_demo", "secret_key": "x" * 40}
    Settings(**common, storage_backend="db", cors_origins=["https://cfs.onrender.com"]).validate_for_runtime()
    with pytest.raises(RuntimeError, match="s3 or db"):
        Settings(**common, storage_backend="local", cors_origins=["https://cfs.onrender.com"]).validate_for_runtime()
    with pytest.raises(RuntimeError, match="CFS_SECRET_KEY"):
        Settings(**{**common, "secret_key": "short"}, storage_backend="db", cors_origins=[]).validate_for_runtime()


@pytest.mark.parametrize(
    "url", ["postgres://u:p@h:5432/d", "postgresql://u:p@h:5432/d", ' "postgresql://u:p@h:5432/d"\n', "'postgres://u:p@h:5432/d'"]
)
def test_host_style_database_urls_use_psycopg(url):
    assert Settings(database_url=url).database_url == "postgresql+psycopg://u:p@h:5432/d"


@pytest.mark.parametrize("bad", ["", "PGPASSWORD=secret psql -h h -U u d"])
def test_production_names_a_bad_database_url_without_echoing_it(bad):
    s = Settings(
        env="production", auth_mode="public_demo", secret_key="x" * 40, storage_backend="db",
        cors_origins=["https://cfs.onrender.com"], database_url=bad,
    )
    with pytest.raises(RuntimeError, match="CFS_DATABASE_URL must be a postgresql") as e:
        s.validate_for_runtime()
    assert "secret" not in str(e.value)


# ── PostgreSQL object store ──


def test_db_store(seeded):
    from cfs.storage.db import DbStore

    store = DbStore()
    _roundtrip(store)
    store.put("orgs/x/originals/aa/a.txt", b"one", "text/plain")
    store.put("orgs/x/originals/aa/a.txt", b"two", "text/plain")  # same key overwrites
    assert store.get("orgs/x/originals/aa/a.txt") == b"two"
    store.delete("orgs/x/originals/aa/a.txt")
    with pytest.raises(FileNotFoundError):
        store.get("orgs/x/originals/aa/a.txt")


# ── in-process job runner ──


@pytest.fixture()
def echo_job(monkeypatch, db):
    from cfs.core import jobs, runner

    s = get_settings()
    monkeypatch.setattr(s, "task_always_eager", False)
    monkeypatch.setattr(s, "job_runner", "thread")
    ran = threading.Event()

    @jobs.register("test_echo")
    def _echo(_db, job, ctx):
        ctx.stage("echo", 0.5)
        ran.set()
        return {"echo": job.payload["x"]}

    org = db.scalar(select(Organization).where(Organization.slug == "local-workspace"))

    def make(x: int) -> uuid.UUID:
        job = jobs.create_job(db, org.id, "test_echo", {"x": x})
        db.commit()
        return job.id

    yield make, ran
    runner.stop()
    jobs.HANDLERS.pop("test_echo", None)


def _wait_for(db, job_id, status, timeout=10.0) -> Job:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        db.expire_all()
        job = db.get(Job, job_id)
        if job.status == status:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} is {job.status}, expected {status}")


def test_thread_runner_runs_enqueued_jobs(echo_job, db):
    from cfs.core.jobs import enqueue

    make, ran = echo_job
    jid = make(7)
    enqueue(jid)
    assert ran.wait(10)
    assert _wait_for(db, jid, JobStatus.succeeded).result == {"echo": 7}


def test_sweep_picks_up_queued_jobs_this_process_never_took(echo_job, db, monkeypatch):
    from cfs.core import runner

    make, _ = echo_job
    jid = make(3)  # queued but never enqueued, e.g. created just before a restart
    taken: list[uuid.UUID] = []
    monkeypatch.setattr(runner, "submit", lambda j: taken.append(uuid.UUID(str(j))))
    runner.sweep()
    assert jid in taken
    from cfs.core.jobs import run_job

    run_job(jid)  # finish it, so no queued job outlives the test (later tests stream the newest job)
    assert _wait_for(db, jid, JobStatus.succeeded).result == {"echo": 3}


# ── built web app served from the API ──


def test_web_app_is_served_with_client_side_routes(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cfs.core.errors import AppError, app_error_handler
    from cfs.web_static import mount_web

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><div id="root"></div>')
    (dist / "assets" / "app-1a2b.js").write_text("console.log(1)")
    (dist / "assets" / "pdf.worker-3c4d.mjs").write_text("self.x = 1")
    (tmp_path / "secret.txt").write_text("do not serve")

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok"}

    mount_web(app, str(dist))
    c = TestClient(app)

    assert c.get("/api/v1/health").json() == {"status": "ok"}  # API routes win
    r = c.get("/connections")  # a client-side route loads the app
    assert r.status_code == 200 and 'id="root"' in r.text and r.headers["cache-control"] == "no-cache"
    r = c.get("/assets/app-1a2b.js")
    assert "javascript" in r.headers["content-type"] and "immutable" in r.headers["cache-control"]
    assert "javascript" in c.get("/assets/pdf.worker-3c4d.mjs").headers["content-type"]
    r = c.get("/api/v1/no-such-route")  # unknown API paths stay JSON 404s, not the app
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    assert "do not serve" not in c.get("/..%2Fsecret.txt").text
