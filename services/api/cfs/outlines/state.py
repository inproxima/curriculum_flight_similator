"""The course subgraph a plan was built on. If it changes before Apply, the plan is stale (409 plan_stale)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cfs.graph.snapshot import Snapshot

CONTAINMENT = {"has_outcome": "course_outcome", "has_assessment": "assessment", "covers_topic": "topic"}


def _active(r: dict[str, Any]) -> bool:
    return r["review_state"] not in ("rejected", "superseded")


def course_members(snap: Snapshot, course_id: str) -> dict[str, list[str]]:
    """Member element ids by type (active containment links only), each list sorted by key."""
    out: dict[str, list[str]] = {t: [] for t in CONTAINMENT.values()}
    for r in snap.rels(*CONTAINMENT, source=course_id):
        if _active(r) and r["target"] in snap.entities:
            out[CONTAINMENT[r["type"]]].append(r["target"])
    for t in out:
        out[t] = sorted(set(out[t]), key=lambda i: snap.entities[i]["key"])
    return out


def program_outcomes(snap: Snapshot) -> list[str]:
    return sorted(
        (e["id"] for e in snap.entities.values() if e["type"] == "program_outcome"),
        key=lambda i: snap.entities[i]["key"],
    )


def course_state_hash(snap: Snapshot, course_id: str, extra: tuple = ()) -> str:
    """Revisions of the course, its members and the program outcomes; every link touching the course or a member;
    the course's placements and rules; the version's academic year and any `extra` inputs the plan depends on (the
    outline document's recorded year). Edits elsewhere in the program don't make a plan stale."""
    members = course_members(snap, course_id)
    inner = {course_id, *(i for ids in members.values() for i in ids)}
    scope = inner | set(program_outcomes(snap))
    ents = sorted((i, snap.entities[i]["revision_id"]) for i in scope if i in snap.entities)
    rels = sorted(
        (k, r["revision_id"], r["review_state"])
        for k, r in snap.relationships.items()
        if r["source"] in inner or r["target"] in inner
    )
    year = snap.version.get("academic_year")
    body = [ents, rels, snap.placements.get(course_id, []), snap.rules_for(course_id), year, list(extra)]
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
