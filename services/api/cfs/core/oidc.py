"""OIDC bearer-token verification (Amazon Cognito or any standards-compliant IdP).

- RS256/ES256 signatures verified against the issuer's JWKS (cached; refreshed on unknown key id).
- `iss` must equal the configured issuer; `exp`/`iat`/`nbf` enforced with small leeway.
- Audience: ID tokens carry `aud`; Cognito access tokens carry `client_id` instead. Either must equal the
  configured audience. Cognito `token_use` must be "id" or "access".
- Roles come from the configured groups claim (mapped by CFS_OIDC_ROLE_MAP_JSON) and are authoritative when
  present; otherwise an existing membership is used; otherwise access is denied (fail closed).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.core.config import get_settings
from cfs.core.errors import AppError, Forbidden
from cfs.models import Membership, Organization, User
from cfs.models.enums import Role

ROLE_RANK = {Role.viewer: 0, Role.reviewer: 1, Role.editor: 2, Role.admin: 3}


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class _JWKS:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: dict[str, Any] = {}
        self._fetched = 0.0

    def url(self) -> str:
        s = get_settings()
        if s.oidc_jwks_url:
            return s.oidc_jwks_url
        disc = httpx.get(s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=10).json()
        return disc["jwks_uri"]

    def get(self, kid: str) -> Any:
        with self._lock:
            if kid not in self._keys or time.time() - self._fetched > 3600:
                if time.time() - self._fetched < 10 and kid not in self._keys and self._keys:
                    raise Unauthorized("Unknown signing key")
                data = httpx.get(self.url(), timeout=10).json()
                self._keys = {k["kid"]: jwt.PyJWK(k).key for k in data["keys"]}
                self._fetched = time.time()
            if kid not in self._keys:
                raise Unauthorized("Unknown signing key")
            return self._keys[kid]


jwks = _JWKS()


def verify_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in ("RS256", "ES256"):
            raise Unauthorized("Unsupported token algorithm")
        key = jwks.get(header.get("kid", ""))
        claims = jwt.decode(
            token,
            key,
            algorithms=[header["alg"]],
            issuer=s.oidc_issuer,
            leeway=30,
            options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise Unauthorized(f"Invalid token: {type(e).__name__}") from e
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud] if aud else []
    if s.oidc_audience not in auds and claims.get("client_id") != s.oidc_audience:
        raise Unauthorized("Token audience mismatch")
    if claims.get("token_use") not in (None, "id", "access"):
        raise Unauthorized("Unexpected token_use")
    return claims


def roles_from_claims(claims: dict[str, Any]) -> Role | None:
    s = get_settings()
    mapping = json.loads(s.oidc_role_map_json)
    groups = claims.get(s.oidc_groups_claim) or []
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.replace(",", " ").split()]
    roles = [Role(mapping[g]) for g in groups if g in mapping]
    return max(roles, key=ROLE_RANK.get) if roles else None


def resolve_principal(db: Session, claims: dict[str, Any]):
    from cfs.core.auth import Principal

    s = get_settings()
    org = db.scalar(select(Organization).where(Organization.slug == s.oidc_org_slug))
    if org is None:
        org = Organization(slug=s.oidc_org_slug, name=s.oidc_org_name)
        db.add(org)
        db.flush()
    sub = claims["sub"]
    user = db.scalar(select(User).where(User.oidc_subject == sub))
    email = claims.get("email") or f"{sub}@oidc.invalid"
    if user is None:
        user = User(
            email=email if not db.scalar(select(User).where(User.email == email)) else f"{sub}@oidc.invalid",
            display_name=claims.get("name") or claims.get("cognito:username") or email,
            oidc_subject=sub,
        )
        db.add(user)
        db.flush()
    m = db.scalar(select(Membership).where(Membership.organization_id == org.id, Membership.user_id == user.id))
    claim_role = roles_from_claims(claims)
    if claim_role is not None:
        if m is None:
            m = Membership(organization_id=org.id, user_id=user.id, role=claim_role)
            db.add(m)
        elif m.role != claim_role:
            m.role = claim_role  # IdP groups are authoritative when present
    if m is None:
        db.commit()
        raise Forbidden("Your account has no role in this workspace. Ask an administrator for access.")
    db.commit()
    return Principal(user.id, org.id, m.role)


# ── short-lived stream tokens (EventSource cannot send Authorization headers) ──


def issue_stream_token(user_id: uuid.UUID, org_id: uuid.UUID, role: Role, job_id: uuid.UUID) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(user_id),
            "org": str(org_id),
            "role": role.value,
            "job": str(job_id),
            "iat": now,
            "exp": now + 300,
            "typ": "stream",
        },
        get_settings().secret_key,
        algorithm="HS256",
    )


def verify_stream_token(token: str, job_id: uuid.UUID):
    from cfs.core.auth import Principal

    try:
        c = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"], options={"require": ["exp"]})
    except jwt.PyJWTError as e:
        raise Unauthorized("Invalid stream token") from e
    if c.get("typ") != "stream" or c.get("job") != str(job_id):
        raise Unauthorized("Stream token not valid for this job")
    return Principal(uuid.UUID(c["sub"]), uuid.UUID(c["org"]), Role(c["role"]))
