import uuid
from typing import Any

from sqlalchemy.orm import Session

from cfs.core.auth import Principal
from cfs.models import AuditEvent


def audit(
    db: Session,
    p: Principal,
    action: str,
    subject_type: str,
    subject_id: uuid.UUID | None,
    data: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            organization_id=p.org_id,
            actor_id=p.user_id,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            data=data,
        )
    )
