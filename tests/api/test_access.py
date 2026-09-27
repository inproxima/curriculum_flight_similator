"""Cross-workspace isolation and role restrictions."""

import io
import uuid

import pytest
from sqlalchemy import select

from cfs.models import EvidenceSpan, Membership, Organization, User
from cfs.models.enums import Role


@pytest.fixture(scope="module")
def outsider(seeded):
    from cfs.core.db import get_sessionmaker

    db = get_sessionmaker()()
    org = Organization(slug=f"other-{uuid.uuid4().hex[:6]}", name="Other workspace")
    viewer_org = db.scalar(select(Organization).where(Organization.slug == "local-workspace"))
    u = User(email=f"outsider-{uuid.uuid4().hex[:6]}@example.test", display_name="Outsider")
    v = User(email=f"viewer-{uuid.uuid4().hex[:6]}@example.test", display_name="Viewer")
    db.add_all([org, u, v])
    db.flush()
    db.add(Membership(organization_id=org.id, user_id=u.id, role=Role.admin))
    db.add(Membership(organization_id=viewer_org.id, user_id=v.id, role=Role.viewer))
    db.commit()
    ids = {"outsider": str(u.id), "viewer": str(v.id)}
    db.close()
    return ids


def test_cross_workspace_isolation(client, outsider, v1, db):
    h = {"X-CFS-User": outsider["outsider"]}
    assert client.get("/api/v1/programs", headers=h).json() == []
    assert client.get(f"/api/v1/versions/{v1}/graph", headers=h).status_code == 404
    span = db.scalar(select(EvidenceSpan))
    assert client.get(f"/api/v1/evidence/{span.id}", headers=h).status_code == 404
    assert client.get(f"/api/v1/document-versions/{span.document_version_id}/file", headers=h).status_code == 404
    assert client.get("/api/v1/reviews", headers=h).json()["total"] == 0
    assert client.get("/api/v1/scenarios", headers=h).json() == []


def test_same_content_in_other_org_is_not_deduplicated_across_orgs(client, outsider):
    data = b"Cross-org content SYN 101"
    r1 = client.post("/api/v1/documents", files={"file": ("x.txt", io.BytesIO(data))})
    r2 = client.post(
        "/api/v1/documents", files={"file": ("x.txt", io.BytesIO(data))}, headers={"X-CFS-User": outsider["outsider"]}
    )
    assert r2.json()["duplicate"] is False
    assert r1.json()["document_version"]["id"] != r2.json()["document_version"]["id"]


def test_viewer_cannot_edit_or_publish(client, outsider, v1):
    h = {"X-CFS-User": outsider["viewer"]}
    assert client.get(f"/api/v1/versions/{v1}/graph", headers=h).status_code == 200
    assert client.post("/api/v1/documents", data={"pasted_text": "x"}, headers=h).status_code == 403
    assert client.post("/api/v1/scenarios", json={"base_version_id": v1, "title": "x"}, headers=h).status_code == 403
    s = client.post("/api/v1/scenarios", json={"base_version_id": v1, "title": "by admin"}).json()
    r = client.post(
        f"/api/v1/scenarios/{s['id']}/publish", json={"label": "nope"}, headers={**h, "If-Match": str(s["revision"])}
    )
    assert r.status_code == 403
    items = client.get("/api/v1/reviews", headers=h).json()["items"]
    assert (
        client.post(f"/api/v1/reviews/{items[0]['id']}/decision", json={"decision": "defer"}, headers=h).status_code
        == 403
    )


def test_production_refuses_local_auth():
    from cfs.core.config import Settings

    with pytest.raises(RuntimeError):
        Settings(env="production", auth_mode="local_single_user").validate_for_runtime()
