"""What the program is missing for one course. Deterministic, derived from the snapshot; used by the course
inspector ("Missing pieces"), as input to the reconcile agent, and to preview what a plan would fill.

Kinds (course scope):
    course_no_outcomes · course_no_assessments · course_no_topics · course_no_description · course_no_credits
        content an outline can supply
    course_no_year · course_no_term · course_no_prerequisite
        program structure an outline doesn't settle (term of the offering ≠ placement in the program; requisites
        come from the calendar through the review inbox)
    clo_no_plo · clo_unassessed · assessment_no_clo · topic_no_clo
        alignment gaps of the course's own elements (cfs.analysis.alignment)
    course_pair_no_link
        a prerequisite pair with no outcome-level bridge (needs the other course too)
Program scope (reported separately): plo_no_clo · plo_never_assessed.
"""

from __future__ import annotations

from typing import Any

from cfs.analysis.alignment import alignment_index
from cfs.graph.snapshot import Snapshot
from cfs.outlines.state import course_members

FILLABLE = {
    "course_no_outcomes",
    "course_no_assessments",
    "course_no_topics",
    "course_no_description",
    "course_no_credits",
    "clo_no_plo",
    "clo_unassessed",
    "assessment_no_clo",
    "topic_no_clo",
}
ELEMENT_GAPS = {"clo_no_plo", "clo_unassessed", "assessment_no_clo", "topic_no_clo"}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _item(kind: str, severity: str, message: str, ids: list[str], **kw: Any) -> dict[str, Any]:
    return {
        "id": f"gap:{kind}:{'|'.join(ids)}",
        "kind": kind,
        "severity": severity,
        "message": message,
        "entity_ids": ids,
        "fillable": kind in FILLABLE,
        "source": kw.get("source"),
        "target": kw.get("target"),
    }


def course_missing(snap: Snapshot, course_id: str) -> dict[str, Any]:
    c = snap.entities[course_id]
    members = course_members(snap, course_id)
    items: list[dict[str, Any]] = []
    key = c["key"]
    if not members["course_outcome"]:
        items.append(
            _item("course_no_outcomes", "high", f"No learning outcomes are documented for {key}.", [course_id])
        )
    if not members["assessment"]:
        items.append(_item("course_no_assessments", "medium", f"No assessments are documented for {key}.", [course_id]))
    if not members["topic"]:
        items.append(_item("course_no_topics", "low", f"No topics are documented for {key}.", [course_id]))
    if not (c.get("description") or "").strip():
        items.append(_item("course_no_description", "low", f"{key} has no course description.", [course_id]))
    if c["details"].get("credits") is None:
        items.append(_item("course_no_credits", "low", f"The credit value of {key} is not documented.", [course_id]))
    pl = snap.primary_placement(course_id)
    if not pl or pl.get("year") is None:
        items.append(_item("course_no_year", "low", f"The year of study of {key} is not documented.", [course_id]))
    if not pl or pl.get("term") in (None, "unknown"):
        items.append(
            _item("course_no_term", "low", f"The term of {key} in the program is not documented.", [course_id])
        )
    if not snap.rules_for(course_id, "prerequisite"):
        items.append(
            _item(
                "course_no_prerequisite",
                "low",
                f"No prerequisite statement for {key} has been imported (outlines usually defer to the calendar).",
                [course_id],
            )
        )
    inner = {course_id, *(i for ids in members.values() for i in ids)}
    idx = alignment_index(snap)
    program = []
    for g in idx["gaps"]:
        if g["kind"] in ELEMENT_GAPS and inner & set(g["entity_ids"]):
            items.append({**g, "fillable": True})
        elif g["kind"] == "course_pair_no_link" and course_id in (g["source"], g["target"]):
            items.append({**g, "fillable": False})
        elif g["kind"] in ("plo_no_clo", "plo_never_assessed"):
            program.append({**g, "fillable": g["kind"] == "plo_no_clo"})
    items.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 9), i["kind"], i["id"]))
    counts: dict[str, int] = {}
    for i in items:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    return {
        "course_id": course_id,
        "course_key": key,
        "items": items,
        "program": program,
        "counts": counts,
        "total": len(items),
        "fillable": sum(1 for i in items if i["fillable"]),
    }
