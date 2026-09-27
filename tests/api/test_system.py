"""Health, readiness, and AI-provider behaviour without credentials."""


def test_health_and_ready(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    r = client.get("/api/v1/ready").json()
    assert r["checks"]["database"]["ok"] and r["checks"]["database"]["pgvector"]


def test_ai_reports_unconfigured_instead_of_fabricating(client, v1, monkeypatch):
    from cfs.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_provider_allowlist", "")
    st = client.get("/api/v1/ai/status").json()
    assert not any(r["available"] for r in st["routes"])
    c = client.post("/api/v1/conversations", json={"curriculum_version_id": v1}).json()
    r = client.post(f"/api/v1/conversations/{c['id']}/messages", json={"text": "hi"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "provider_unconfigured"


def test_search_reports_retrieval_mode(client, v1):
    r = client.get(f"/api/v1/versions/{v1}/search?q=regression").json()
    assert r["retrieval_mode"].startswith("full_text_only")
    assert r["documents"] and any("SYN 211" in e["key"] or "regression" in e["title"].lower() for e in r["entities"])
