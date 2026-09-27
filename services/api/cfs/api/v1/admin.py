"""Authentication config, current user, stream tokens, and membership administration."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.core.audit import audit
from cfs.core.auth import Principal, get_principal
from cfs.core.config import get_settings
from cfs.core.db import get_db
from cfs.core.errors import AppError
from cfs.core.scope import get_scoped
from cfs.models import Job, Membership, Organization, User
from cfs.models.enums import Role

router = APIRouter(tags=["auth"])


@router.get("/auth/config")
def auth_config() -> dict:
    """Public: what the browser needs to start sign-in. Contains no secrets."""
    s = get_settings()
    if s.auth_mode != "oidc":
        return {"mode": s.auth_mode}
    return {
        "mode": "oidc",
        "issuer": s.oidc_issuer,
        "client_id": s.oidc_client_id_public or s.oidc_audience,
        "scopes": s.oidc_scopes,
    }


@router.get("/me")
def me(p: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> dict:
    u = db.get(User, p.user_id)
    org = db.get(Organization, p.org_id)
    return {
        "user_id": str(p.user_id),
        "email": u.email,
        "display_name": u.display_name,
        "role": p.role.value,
        "organization": {"id": str(org.id), "name": org.name},
        "auth_mode": get_settings().auth_mode,
    }


@router.post("/jobs/{job_id}/stream-token")
def stream_token(job_id: uuid.UUID, p: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> dict:
    """Short-lived (5 min) token for EventSource, which cannot send Authorization headers."""
    from cfs.core.oidc import issue_stream_token

    get_scoped(db, Job, job_id, p, "Job")
    return {"token": issue_stream_token(p.user_id, p.org_id, p.role, job_id), "expires_in": 300}


@router.get("/admin/memberships")
def list_members(p: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> list[dict]:
    p.require(Role.admin)
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == p.org_id)
        .order_by(User.email)
    ).all()
    return [
        {
            "user_id": str(u.id),
            "email": u.email,
            "display_name": u.display_name,
            "role": m.role.value,
            "oidc": bool(u.oidc_subject),
        }
        for m, u in rows
    ]


class RoleIn(BaseModel):
    role: Literal["admin", "editor", "reviewer", "viewer"]


@router.put("/admin/memberships/{user_id}")
def set_role(user_id: uuid.UUID, body: RoleIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    """Set a member's role. Note: when the IdP sends group claims, those override this at the user's next request."""
    p.require(Role.admin)
    m = db.scalar(select(Membership).where(Membership.organization_id == p.org_id, Membership.user_id == user_id))
    if m is None:
        raise AppError("User is not a member of this workspace", status=404, code="not_found")
    if user_id == p.user_id and body.role != "admin":
        raise AppError("Administrators cannot remove their own admin role", code="self_demotion")
    old = m.role.value
    m.role = Role(body.role)
    audit(db, p, "membership.role_changed", "user", user_id, {"from": old, "to": body.role})
    db.commit()
    return {"user_id": str(user_id), "role": body.role}
