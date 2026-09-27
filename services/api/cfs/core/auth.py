"""Request principal resolution and role checks.

Local single-user mode (development) binds everything to a local admin in a default organization.
For tests and multi-user local trials, `X-CFS-User` may name another existing user (development/test
only). Production requires OIDC (Phase 6) and refuses local mode at startup (see Settings).
"""

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.core.config import get_settings
from cfs.core.db import get_db
from cfs.core.errors import Forbidden
from cfs.models import Membership, Organization, User
from cfs.models.enums import Role

LOCAL_ORG_SLUG = "local-workspace"
LOCAL_USER_EMAIL = "local-admin@localhost"

ROLE_RANK = {Role.viewer: 0, Role.reviewer: 1, Role.editor: 2, Role.admin: 3}


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role

    def require(self, minimum: Role) -> None:
        if ROLE_RANK[self.role] < ROLE_RANK[minimum]:
            raise Forbidden(
                f"This action requires the {minimum.value} role or higher.", details={"role": self.role.value}
            )


def ensure_local_principal(db: Session) -> Principal:
    org = db.scalar(select(Organization).where(Organization.slug == LOCAL_ORG_SLUG))
    if org is None:
        org = Organization(slug=LOCAL_ORG_SLUG, name="Local workspace")
        db.add(org)
        db.flush()
    user = db.scalar(select(User).where(User.email == LOCAL_USER_EMAIL))
    if user is None:
        user = User(email=LOCAL_USER_EMAIL, display_name="Local administrator")
        db.add(user)
        db.flush()
    m = db.scalar(select(Membership).where(Membership.organization_id == org.id, Membership.user_id == user.id))
    if m is None:
        m = Membership(organization_id=org.id, user_id=user.id, role=Role.admin)
        db.add(m)
    db.commit()
    return Principal(user.id, org.id, m.role)


def get_principal(
    request: Request,
    db: Session = Depends(get_db),
    x_cfs_user: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Principal:
    settings = get_settings()
    if settings.auth_mode == "oidc":
        from cfs.core.oidc import Unauthorized, resolve_principal, verify_token

        if not authorization or not authorization.lower().startswith("bearer "):
            raise Unauthorized("Sign-in required")
        return resolve_principal(db, verify_token(authorization[7:].strip()))
    if settings.env != "test":
        client = request.client.host if request.client else ""
        if client not in {"127.0.0.1", "::1", "localhost", "testclient"} and not _is_private_docker(client):
            raise Forbidden("Local single-user mode only accepts connections from this machine.")
    if x_cfs_user:
        if settings.env == "production":
            raise Forbidden("X-CFS-User is not accepted in production")
        try:
            uid = uuid.UUID(x_cfs_user)
        except ValueError as e:
            raise Forbidden("Invalid X-CFS-User header") from e
        m = db.scalar(select(Membership).where(Membership.user_id == uid))
        if m is None:
            raise Forbidden("Unknown user")
        return Principal(uid, m.organization_id, m.role)
    return ensure_local_principal(db)


def _is_private_docker(host: str) -> bool:
    # The Vite dev container proxies to the API over the Compose network; ports are bound to 127.0.0.1 on the host.
    return host.startswith("172.") or host.startswith("192.168.")
