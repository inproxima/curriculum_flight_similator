"""Phase 6 security: OIDC verification, stream tokens, production fail-closed config, SSRF-safe fetching,
rate limits, security headers, and SQS broker configuration."""

import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from cfs.core.config import Settings, get_settings

ISS = "https://cognito-idp.ca-central-1.amazonaws.com/ca-central-1_TEST"
AUD = "test-client-id"


@pytest.fixture()
def oidc(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    s = get_settings()
    monkeypatch.setattr(s, "auth_mode", "oidc")
    monkeypatch.setattr(s, "oidc_issuer", ISS)
    monkeypatch.setattr(s, "oidc_audience", AUD)
    monkeypatch.setattr(s, "oidc_org_slug", "local-workspace")  # the org that holds the synthetic fixture
    from cfs.core import oidc as mod

    monkeypatch.setattr(
        mod.jwks,
        "get",
        lambda kid: key.public_key() if kid == "k1" else (_ for _ in ()).throw(mod.Unauthorized("Unknown signing key")),
    )

    def token(**over):
        now = int(time.time())
        claims = {
            "iss": ISS,
            "sub": over.pop("sub", "user-" + uuid.uuid4().hex[:8]),
            "aud": AUD,
            "iat": now,
            "exp": now + 600,
            "email": f"{uuid.uuid4().hex[:6]}@ucalgary.example",
            "token_use": "id",
            "cognito:groups": ["cfs-editor"],
        }
        claims.update(over)
        kid = claims.pop("_kid", "k1")
        alg = claims.pop("_alg", "RS256")
        if alg == "none":
            return jwt.encode(claims, None, algorithm="none")
        return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})

    return token


def test_oidc_valid_token_maps_groups_to_roles(client, oidc):
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {oidc()}"})
    assert r.status_code == 200 and r.json()["role"] == "editor" and r.json()["auth_mode"] == "oidc"
    r = client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {oidc(**{'cognito:groups': ['cfs-admin', 'cfs-viewer']})}"}
    )
    assert r.json()["role"] == "admin"  # highest mapped group wins
    # Cognito access tokens carry client_id instead of aud
    t = oidc(aud=None, client_id=AUD, token_use="access")
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {t}"}).status_code == 200


@pytest.mark.parametrize(
    "over,status",
    [
        ({"aud": "someone-else"}, 401),
        ({"iss": "https://evil.example"}, 401),
        ({"exp": int(time.time()) - 120}, 401),
        ({"_kid": "unknown"}, 401),
        ({"_alg": "none"}, 401),
        ({"token_use": "refresh"}, 401),
        ({"cognito:groups": []}, 403),  # authenticated but no role: fail closed
    ],
)
def test_oidc_rejections(client, oidc, over, status):
    r = client.get("/api/v1/programs", headers={"Authorization": f"Bearer {oidc(**over)}"})
    assert r.status_code == status, r.text


def test_oidc_requires_bearer_and_ignores_dev_header(client, oidc):
    assert client.get("/api/v1/programs").status_code == 401
    assert client.get("/api/v1/programs", headers={"X-CFS-User": str(uuid.uuid4())}).status_code == 401
    assert client.get("/api/v1/auth/config").json()["mode"] == "oidc"  # public, no secrets
    assert "secret" not in str(client.get("/api/v1/auth/config").json()).lower()


def test_viewer_token_cannot_edit(client, oidc, v1):
    t = oidc(**{"cognito:groups": ["cfs-viewer"]})
    h = {"Authorization": f"Bearer {t}"}
    assert client.get(f"/api/v1/versions/{v1}/graph", headers=h).status_code == 200
    assert client.post("/api/v1/scenarios", json={"base_version_id": v1, "title": "x"}, headers=h).status_code == 403


def test_stream_token_is_job_scoped(client, oidc):
    h = {"Authorization": f"Bearer {oidc()}"}
    jobs = client.get("/api/v1/jobs?limit=2", headers=h).json()
    j1, j2 = jobs[0]["id"], jobs[1]["id"]
    tok = client.post(f"/api/v1/jobs/{j1}/stream-token", headers=h).json()["token"]
    assert client.get(f"/api/v1/jobs/{j1}/stream?st={tok}").status_code == 200
    assert client.get(f"/api/v1/jobs/{j2}/stream?st={tok}").status_code == 401
    assert client.get(f"/api/v1/jobs/{j1}/stream").status_code == 401


def test_production_config_fails_closed():
    bad = Settings(env="production", auth_mode="local_single_user")
    with pytest.raises(RuntimeError) as e:
        bad.validate_for_runtime()
    msg = str(e.value)
    assert "local_single_user" in msg and "CFS_SECRET_KEY" in msg and "s3" in msg and "localhost" in msg
    good = Settings(
        env="production",
        auth_mode="oidc",
        oidc_issuer=ISS,
        oidc_audience=AUD,
        secret_key="x" * 40,
        storage_backend="s3",
        s3_bucket="b",
        cors_origins=["https://cfs.example.org"],
    )
    good.validate_for_runtime()


# ── controlled fetcher ──


@pytest.mark.parametrize(
    "url,why",
    [
        ("http://127.0.0.1/admin", "public internet"),
        ("http://10.0.0.5/", "public internet"),
        ("http://169.254.169.254/latest/meta-data/", "public internet"),
        ("http://[::1]/", "public internet"),
        ("ftp://www.ucalgary.ca/file", "http and https"),
        ("https://user:pw@www.ucalgary.ca/", "credentials"),
        ("https://www.ucalgary.ca:8443/", "default ports"),
        ("https://evil.example.com/", "allowed domains"),
    ],
)
def test_fetcher_refuses_unsafe_urls(monkeypatch, url, why):
    from cfs.ingest.fetch import FetchRefused, validate_url

    monkeypatch.setattr(get_settings(), "fetch_allowed_domains", "")
    if "evil" in url:
        monkeypatch.setattr(get_settings(), "fetch_allowed_domains", "ucalgary.ca")
    with pytest.raises(FetchRefused) as e:
        validate_url(url)
    assert why in e.value.message


def _fake_dns(monkeypatch, mapping):
    import socket

    def gai(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], port))]

    monkeypatch.setattr(socket, "getaddrinfo", gai)


def test_fetcher_blocks_dns_to_private_and_redirect_to_metadata(monkeypatch):
    from cfs.ingest.fetch import FetchRefused, fetch

    monkeypatch.setattr(get_settings(), "fetch_allowed_domains", "ucalgary.ca")
    _fake_dns(monkeypatch, {"intranet.ucalgary.ca": "192.168.1.10", "www.ucalgary.ca": "136.159.96.10"})
    with pytest.raises(FetchRefused):
        fetch("https://intranet.ucalgary.ca/")  # allowlisted name, private address

    def handler(request: httpx.Request):
        assert request.url.host == "136.159.96.10" and request.headers["host"] == "www.ucalgary.ca"  # pinned IP
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    with pytest.raises(FetchRefused):
        fetch("https://www.ucalgary.ca/page", transport=httpx.MockTransport(handler))


def test_fetcher_imports_html_as_text_without_scripts(monkeypatch):
    from cfs.ingest.fetch import fetch, html_to_text

    monkeypatch.setattr(get_settings(), "fetch_allowed_domains", "ucalgary.ca")
    _fake_dns(monkeypatch, {"cumming.ucalgary.ca": "136.159.96.20"})
    html = (
        "<html><head><title>Biomedical Sciences</title><script>alert('x')</script></head><body><nav>Menu</nav>"
        "<h2>Year 1</h2><p>MDSC 203 - Developing Health Research Literacy I</p>"
        "<a href='/files/outline.pdf'>2025-2026 Program Outline</a></body></html>"
    )
    t = httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=html.encode())
    )
    f = fetch("https://cumming.ucalgary.ca/bhsc/program", transport=t)
    text, title = html_to_text(f.data.decode(), f.final_url)
    assert title == "Biomedical Sciences" and "MDSC 203 - Developing Health Research Literacy I" in text
    assert "alert" not in text and "Menu" not in text
    assert "https://cumming.ucalgary.ca/files/outline.pdf" in text  # linked documents listed, not imported


def test_fetcher_enforces_size_and_type(monkeypatch):
    from cfs.ingest.fetch import FetchRefused, fetch

    monkeypatch.setattr(get_settings(), "fetch_allowed_domains", "ucalgary.ca")
    monkeypatch.setattr(get_settings(), "fetch_max_bytes", 100)
    _fake_dns(monkeypatch, {"www.ucalgary.ca": "136.159.96.10"})
    big = httpx.MockTransport(lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, content=b"x" * 500))
    with pytest.raises(FetchRefused):
        fetch("https://www.ucalgary.ca/big", transport=big)
    exe = httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "application/x-msdownload"}, content=b"MZ")
    )
    with pytest.raises(FetchRefused):
        fetch("https://www.ucalgary.ca/tool.exe", transport=exe)


def test_rate_limit(client, v1, monkeypatch):
    from cfs.core.security import _buckets

    _buckets.clear()
    monkeypatch.setattr(get_settings(), "rate_limit_upload_per_minute", 2)
    codes = [
        client.post("/api/v1/documents", data={"pasted_text": f"rate {i} {uuid.uuid4()}"}).status_code for i in range(3)
    ]
    assert codes[:2] == [201, 201] and codes[2] == 429
    _buckets.clear()


def test_security_headers(client):
    r = client.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"


def test_sqs_worker_configuration():
    from cfs.worker_tasks import celery_config

    s = Settings(broker_url="sqs://", sqs_queue_url="https://sqs.ca-central-1.amazonaws.com/1/cfs-jobs")
    c = celery_config(s)
    assert c["broker_transport_options"]["predefined_queues"]["celery"]["url"].endswith("cfs-jobs")
    assert c["broker_transport_options"]["visibility_timeout"] >= 3600
    assert c["worker_enable_remote_control"] is False and c["task_ignore_result"] is True
