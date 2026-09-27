"""Organization scoping helpers. Every lookup of a scoped row goes through here."""

import uuid
from typing import TypeVar

from sqlalchemy.orm import Session

from cfs.core.auth import Principal
from cfs.core.errors import NotFound

T = TypeVar("T")


def get_scoped(db: Session, model: type[T], id_: uuid.UUID, p: Principal, what: str | None = None) -> T:
    obj = db.get(model, id_)
    # Rows in another organization are reported as not found (no existence disclosure).
    if obj is None or getattr(obj, "organization_id", None) != p.org_id:
        raise NotFound(f"{what or model.__name__} not found")
    return obj
