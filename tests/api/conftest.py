"""Backend test fixtures. Uses the separate cfs_test database (created by infra/initdb) and inline jobs.

All curriculum content in these tests is the SYNTHETIC fixture; no University of Calgary data is used.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("CFS_ENV", "test")
os.environ.setdefault("CFS_DATABASE_URL", "postgresql+psycopg://cfs:cfs@localhost:5433/cfs_test")
os.environ["CFS_TASK_ALWAYS_EAGER"] = "true"
os.environ.setdefault("CFS_STORAGE_ROOT", tempfile.mkdtemp(prefix="cfs-test-objects-"))

import pathlib  # noqa: E402

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from alembic import command  # noqa: E402

API_DIR = pathlib.Path(__file__).resolve().parents[2] / "services" / "api"


def _alembic_cfg() -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", os.environ["CFS_DATABASE_URL"])
    return cfg


@pytest.fixture(scope="session")
def seeded():
    command.downgrade(_alembic_cfg(), "base")
    command.upgrade(_alembic_cfg(), "head")
    from cfs.core.db import get_sessionmaker
    from cfs.fixtures.seed import seed

    db = get_sessionmaker()()
    try:
        ids = seed(db, quiet=True)
    finally:
        db.close()
    return ids


@pytest.fixture(scope="session")
def client(seeded):
    from cfs.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db(seeded):
    from cfs.core.db import get_sessionmaker

    s = get_sessionmaker()()
    yield s
    s.rollback()
    s.close()


@pytest.fixture(scope="session")
def v1(seeded):
    return seeded["v1"]


@pytest.fixture(scope="session")
def v2(seeded):
    return seeded["v2"]


@pytest.fixture(scope="session")
def ent(client, v1):
    """Look up an entity id by key in the published synthetic version."""
    cache: dict[str, str] = {}

    def get(key: str, version: str | None = None) -> str:
        ver = version or v1
        ck = f"{ver}:{key}"
        if ck not in cache:
            rows = client.get(f"/api/v1/versions/{ver}/entities").json()
            cache.update({f"{ver}:{r['key']}": r["id"] for r in rows})
        return cache[ck]

    return get


def new_scenario(client, base: str, title: str) -> dict:
    r = client.post("/api/v1/scenarios", json={"base_version_id": base, "title": title})
    assert r.status_code == 201, r.text
    return r.json()


def add_change(client, s: dict, change: dict, expect: int = 201) -> dict:
    r = client.post(f"/api/v1/scenarios/{s['id']}/changes", json=change, headers={"If-Match": str(s["revision"])})
    assert r.status_code == expect, r.text
    return r.json()
