"""Delete a DRAFT curriculum version and everything that only exists for it.

Removed: scenarios based on it (with their changes, analysis runs and findings), assistant conversations about it,
its analysis runs, and the version itself, which cascades its memberships, placements, rules, document assignments,
review items, outline imports and AI verdicts. Entities, relationships, documents and evidence are shared,
immutable history and stay. Published versions, and versions that another version was created from, are refused.

    python -m cfs.ops.delete_version <version-id> [...]        # report only
    python -m cfs.ops.delete_version <version-id> [...] --yes  # delete
    python -m cfs.ops.delete_version --from-env                 # delete the ids in CFS_DELETE_DRAFT_VERSIONS

The --from-env form runs at startup on the hosted prototype (infra/Dockerfile.render). It is idempotent: an id that
is already gone is skipped.
"""

from __future__ import annotations

import os
import sys
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from cfs.models import (
    AnalysisRun,
    Conversation,
    CurriculumEntityMembership,
    CurriculumRelationshipMembership,
    CurriculumSource,
    CurriculumVersion,
    ReviewItem,
    Scenario,
)
from cfs.models.enums import VersionStatus


class Refused(Exception):
    pass


def _count(db: Session, model, column, vid: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(model).where(column == vid)) or 0


def describe(db: Session, vid: uuid.UUID) -> dict | None:
    v = db.get(CurriculumVersion, vid)
    if v is None:
        return None
    return {
        "label": v.label,
        "status": v.status.value,
        "scenarios": _count(db, Scenario, Scenario.base_version_id, vid),
        "conversations": _count(db, Conversation, Conversation.curriculum_version_id, vid),
        "analysis_runs": _count(db, AnalysisRun, AnalysisRun.curriculum_version_id, vid),
        "element_memberships": _count(
            db, CurriculumEntityMembership, CurriculumEntityMembership.curriculum_version_id, vid
        ),
        "link_memberships": _count(
            db, CurriculumRelationshipMembership, CurriculumRelationshipMembership.curriculum_version_id, vid
        ),
        "document_assignments": _count(db, CurriculumSource, CurriculumSource.curriculum_version_id, vid),
        "review_items": _count(db, ReviewItem, ReviewItem.curriculum_version_id, vid),
    }


def delete_draft_version(db: Session, vid: uuid.UUID) -> dict | None:
    """Delete one draft version in one transaction. Returns what it held, or None if it does not exist."""
    v = db.get(CurriculumVersion, vid)
    if v is None:
        return None
    if v.status != VersionStatus.draft:
        raise Refused(f"{v.label} is {v.status.value}; only drafts can be deleted")
    children = _count(db, CurriculumVersion, CurriculumVersion.parent_version_id, vid)
    if children:
        raise Refused(f"{v.label} is the parent of {children} other version(s)")
    held = describe(db, vid)
    db.execute(delete(Scenario).where(Scenario.base_version_id == vid))  # cascades changes, runs, findings
    db.execute(delete(Conversation).where(Conversation.curriculum_version_id == vid))  # cascades messages
    db.execute(delete(AnalysisRun).where(AnalysisRun.curriculum_version_id == vid))
    # A plain DELETE, so the database's ON DELETE CASCADE removes everything else that belongs to the version.
    db.execute(delete(CurriculumVersion).where(CurriculumVersion.id == vid))
    db.commit()
    return held


def _ids(values: list[str]) -> list[uuid.UUID]:
    return [uuid.UUID(x.strip()) for x in values if x.strip()]


def main(argv: list[str]) -> int:
    from cfs.core.db import get_sessionmaker

    if argv == ["--from-env"]:
        ids, apply = _ids(os.environ.get("CFS_DELETE_DRAFT_VERSIONS", "").split(",")), True
        if not ids:
            return 0
    else:
        apply = "--yes" in argv
        ids = _ids([a for a in argv if not a.startswith("--")])
        if not ids:
            print(__doc__)
            return 2
    status = 0
    for vid in ids:
        with get_sessionmaker()() as db:
            try:
                if not apply:
                    info = describe(db, vid)
                    print(f"{vid}: {'not found' if info is None else info}  (report only; add --yes to delete)")
                    continue
                held = delete_draft_version(db, vid)
                print(f"{vid}: {'already gone' if held is None else 'deleted ' + str(held)}")
            except Refused as e:
                print(f"{vid}: refused: {e}")
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
