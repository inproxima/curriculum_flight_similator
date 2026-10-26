"""A plan selection applied to a COPY of the snapshot, with the provenance apply would write, so the preview of
missing pieces matches what Apply produces. (Scenario ops are not used: they would mark everything as a manual
assumption, which changes link strengths and the scorecard.)"""

from __future__ import annotations

import uuid
from typing import Any

from cfs.graph.queries import owner_courses
from cfs.graph.snapshot import Snapshot
from cfs.outlines.missing import course_missing
from cfs.outlines.state import course_members

CONTAINMENT_FOR = {"course_outcome": "has_outcome", "assessment": "has_assessment", "topic": "covers_topic"}
ORDER = {"set_field": 0, "retire": 1, "detach": 1, "replace": 2, "keep": 3, "add": 4, "link": 5}


def new_id(import_id: str, item_id: str) -> str:
    return str(uuid.uuid5(uuid.UUID(str(import_id)), item_id))


def detach_scope(snap: Snapshot, course_id: str, eid: str) -> dict[str, Any]:
    """What retiring an element from this course removes. An element another course also contains (a shared topic, a
    cross-listed outcome) only loses its link to this course and its links to this course's elements; anything else
    goes from the draft with every link touching it."""
    others = [o for o in owner_courses(snap, eid) if o != course_id]
    if others:
        members = course_members(snap, course_id)
        inner = {course_id, *(i for ids in members.values() for i in ids)} - {eid}
        keys = [
            k
            for k, r in snap.relationships.items()
            if (r["type"] in CONTAINMENT_FOR.values() and r["source"] == course_id and r["target"] == eid)
            or (r["source"] == eid and r["target"] in inner)
            or (r["target"] == eid and r["source"] in inner and r["type"] not in CONTAINMENT_FOR.values())
        ]
        return {"remove_entity": False, "keys": sorted(keys)}
    keys = [k for k, r in snap.relationships.items() if eid in (r["source"], r["target"])]
    return {"remove_entity": True, "keys": sorted(keys)}


def _rel(key: str, source: str, target: str, rtype: str, **kw: Any) -> dict[str, Any]:
    return {
        "key": key,
        "revision_id": f"proj:{key}",
        "source": source,
        "target": target,
        "type": rtype,
        "origin": kw.get("origin", "ai_inferred"),
        "basis": kw.get("basis", "explicit_statement"),
        "review_state": "accepted",
        "level": kw.get("level"),
        "rule_id": None,
        "rationale": None,
        "strength": kw.get("strength"),
        "strength_source": kw.get("strength_source"),
        "scope": None,
        "is_synthetic": False,
        "evidence": {"supporting": 1, "contradicting": 0},
    }


def end_id(end: dict[str, Any], import_id: str, add_of: dict[str, str]) -> str | None:
    if end.get("entity_id"):
        return end["entity_id"]
    iid = add_of.get(end.get("ref") or "")
    return new_id(import_id, iid) if iid else None


def project_plan(
    snap: Snapshot,
    plan: dict[str, Any],
    selected: set[str],
    import_id: str,
    edits: dict[str, dict[str, Any]] | None = None,
) -> Snapshot:
    s = snap.copy()
    cid = plan["course"]["id"]
    items = sorted(
        (i for i in plan["items"] if i["id"] in selected and not i["noop"]), key=lambda i: ORDER[i["action"]]
    )
    add_of = {i["id"].split(":", 1)[1]: i["id"] for i in plan["items"] if i["action"] == "add"}
    for it in items:
        a = it["action"]
        if a == "set_field":
            f, v = it["proposed"]["field"], it["proposed"]["value"]
            c = s.entities[cid]
            if f == "description":
                s.entities[cid] = {**c, "description": v}
            else:
                s.entities[cid] = {**c, "details": {**c["details"], "credits": v}}
        elif a in ("retire", "detach"):
            eid = it["target"]["entity_id"]
            if eid not in s.entities:
                continue
            scope = detach_scope(s, cid, eid)
            for k in scope["keys"]:
                s.relationships.pop(k, None)
            if scope["remove_entity"]:
                s.entities.pop(eid, None)
            s.invalidate()
        elif a == "replace":
            eid = it["target"]["entity_id"]
            e = s.entities[eid]
            p = it["proposed"]
            det = {
                **e["details"],
                **{k: v for k, v in p["details"].items() if v is not None or k == "statement_verbatim"},
            }
            if e["type"] == "course_outcome":
                det["label"] = p["details"].get("normalized_label") or det.get("label")
            s.entities[eid] = {**e, "title": p["title"], "details": det, "revision_id": f"proj:{it['id']}"}
        elif a == "add":
            nid = new_id(import_id, it["id"])
            p = it["proposed"]
            det = dict(p["details"])
            if p["type"] == "course_outcome":
                det = {
                    "scope": "course",
                    "statement_verbatim": det.get("statement_verbatim"),
                    "label": det.get("normalized_label"),
                }
            s.entities[nid] = {
                "id": nid,
                "type": p["type"],
                "key": it.get("key_preview") or nid,
                "revision_id": f"proj:{it['id']}",
                "revision_number": 1,
                "title": p["title"],
                "description": None,
                "details": det,
                "origin": "ai_inferred",
                "is_synthetic": False,
                "field_evidence": {"statement" if p["type"] == "course_outcome" else "title": 1},
            }
            k = new_id(import_id, it["id"] + ":contains")
            s.relationships[k] = _rel(k, cid, nid, CONTAINMENT_FOR[p["type"]])
        elif a == "link":
            L = it["link"]
            src, tgt = end_id(L["source"], import_id, add_of), end_id(L["target"], import_id, add_of)
            if not src or not tgt or src not in s.entities or tgt not in s.entities:
                continue
            ed = (edits or {}).get(it["id"]) or {}
            stated = L["basis"] == "stated"
            k = new_id(import_id, it["id"])
            s.relationships[k] = _rel(
                k,
                src,
                tgt,
                L["rel_type"],
                basis="explicit_statement" if stated else "interpretation",
                level=ed.get("level", L.get("level")),
                strength=None if stated else ed.get("strength", L.get("strength")),
                strength_source=None if stated else ("reviewer" if "strength" in ed else "ai_estimate"),
            )
        s.invalidate()
    return s


def preview(
    snap: Snapshot,
    plan: dict[str, Any],
    selected: set[str],
    import_id: str,
    edits: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Missing pieces after applying `selected`, and what that closes and introduces."""
    cid = plan["course"]["id"]
    proj = project_plan(snap, plan, selected, import_id, edits)
    after = course_missing(proj, cid)
    before_ids = {g["id"] for g in plan["missing_before"]}
    after_ids = {g["id"] for g in after["items"]}
    prog_before = {g["id"] for g in plan["program_missing"]}
    prog_after = {g["id"] for g in after["program"]}
    chosen = [i for i in plan["items"] if i["id"] in selected and not i["noop"]]
    return {
        "selected": len(chosen),
        "changes": {a: sum(1 for i in chosen if i["action"] == a) for a in ORDER},
        "missing_before": len(before_ids),
        "missing_after": len(after_ids),
        "closes": sorted(before_ids - after_ids),
        "introduces": [g for g in after["items"] if g["id"] not in before_ids],
        "program_before": len(prog_before),
        "program_after": len(prog_after),
        "program_closes": sorted(prog_before - prog_after),
    }
