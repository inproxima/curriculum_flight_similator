"""Typed traversals over a snapshot: expected prior learning, current contribution, downstream use.

Propagation is typed, never "everything reachable":
  course     → formal_prerequisite / inferred_preparation targets; its own outcomes and topics (provides)
  outcome    → prepares_for targets; assessments that assess it; owning course of a dependent outcome
  topic      → prepares_for targets
  overlap, corequisite, and program-membership links never propagate dependency.

Language: exposure is "expected to have encountered", never "knows".
"""

from __future__ import annotations

from collections import deque
from typing import Any

from cfs.curriculum.rules import (
    Availability,
    EvalContext,
    Sat,
    evaluate,
    iter_course_refs,
    parse_expr,
    render,
)
from cfs.graph.snapshot import Snapshot, time_index

EXPOSURE_FOR_CLASS = {
    "required": "required_path",
    "pathway_required": "pathway_dependent",
    "elective": "elective",
    "optional": "elective",
    "unknown": "unknown",
}


def exposure(snap: Snapshot, course_id: str, pathway: str | None = None) -> str:
    pls = snap.placements.get(course_id) or []
    if not pls:
        return "unknown"
    classes = []
    for p in pls:
        c = p["classification"]
        if c == "pathway_required" and pathway and p.get("pathway") == pathway:
            c = "required"
        classes.append(EXPOSURE_FOR_CLASS.get(c, "unknown"))
    order = ["required_path", "pathway_dependent", "elective", "unknown"]
    return min(classes, key=order.index)


def timing(snap: Snapshot, earlier: str, later: str) -> str:
    a, b = time_index(snap.primary_placement(earlier)), time_index(snap.primary_placement(later))
    if a is None or b is None:
        return "unknown"
    return "earlier" if a < b else "same_term" if a == b else "later"


def owner_courses(snap: Snapshot, node_id: str) -> list[str]:
    """Courses that contain an outcome/topic/assessment. Index cached on the snapshot (invalidate via meta)."""
    idx = snap.meta.get("owners")
    if idx is None:
        idx = {}
        for r in snap.relationships.values():
            if r["type"] in ("has_outcome", "covers_topic", "has_assessment") and _active(r):
                idx.setdefault(r["target"], set()).add(r["source"])
        idx = {k: sorted(v) for k, v in idx.items()}
        snap.meta["owners"] = idx
    return idx.get(node_id, [])


def _active(r: dict[str, Any]) -> bool:
    return r["review_state"] not in ("rejected", "superseded")


def _edge_view(snap: Snapshot, r: dict[str, Any], *, via: str | None = None) -> dict[str, Any]:
    return {
        "key": r["key"],
        "type": r["type"],
        "source": r["source"],
        "target": r["target"],
        "basis": r["basis"],
        "origin": r["origin"],
        "review_state": r["review_state"],
        "has_evidence": r["evidence"]["supporting"] > 0,
        "via": via,
    }


def _entity_view(snap: Snapshot, eid: str) -> dict[str, Any]:
    e = snap.entities[eid]
    v = {"id": eid, "type": e["type"], "key": e["key"], "title": e["title"]}
    if e["type"] == "course":
        pl = snap.primary_placement(eid)
        v["placement"] = pl
    return v


def downstream_steps(snap: Snapshot, node: str) -> list[tuple[str, dict[str, Any]]]:
    e = snap.entities[node]
    out: list[tuple[str, dict[str, Any]]] = []
    for r in sorted(snap.rels(source=node), key=lambda r: (r["type"], r["target"])):
        if not _active(r):
            continue
        if e["type"] == "course" and r["type"] in (
            "formal_prerequisite",
            "inferred_preparation",
            "has_outcome",
            "covers_topic",
        ):
            out.append((r["target"], _edge_view(snap, r)))
        elif e["type"] in ("course_outcome", "topic", "program_outcome") and r["type"] == "prepares_for":
            out.append((r["target"], _edge_view(snap, r)))
    if e["type"] == "course_outcome":
        for r in sorted(snap.rels("assesses", target=node), key=lambda r: r["source"]):
            if _active(r):
                out.append((r["source"], {**_edge_view(snap, r), "reverse": True}))
    return out


def upstream_steps(snap: Snapshot, node: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for r in sorted(
        snap.rels("formal_prerequisite", "inferred_preparation", "prepares_for", target=node),
        key=lambda r: (r["type"], r["source"]),
    ):
        if _active(r):
            out.append((r["source"], _edge_view(snap, r)))
    return out


def _documented_only(step):
    def inner(snap: Snapshot, node: str):
        return [
            (n, e)
            for n, e in step(snap, node)
            if e["type"] in ("has_outcome", "covers_topic", "has_assessment", "assesses")
            or (e["basis"] == "explicit_statement" and e["review_state"] == "accepted")
        ]

    return inner


def _bfs(
    snap: Snapshot, start: str | list[str], step, max_depth: int
) -> dict[str, tuple[str | None, dict | None, int]]:
    starts = [start] if isinstance(start, str) else start
    seen: dict[str, tuple[str | None, dict | None, int]] = {s: (None, None, 0) for s in starts}
    q = deque(starts)
    while q:
        n = q.popleft()
        d = seen[n][2]
        if d >= max_depth:
            continue
        for nxt, edge in step(snap, n):
            if nxt not in seen and nxt in snap.entities:
                seen[nxt] = (n, edge, d + 1)
                q.append(nxt)
    return seen


def _path(seen: dict, node: str) -> tuple[list[str], list[dict]]:
    nodes, edges = [node], []
    while seen[node][0] is not None:
        prev, edge, _ = seen[node]
        edges.append(edge)
        nodes.append(prev)
        node = prev
    return list(reversed(nodes)), list(reversed(edges))


def _path_basis(edges: list[dict]) -> str:
    dep = [e for e in edges if e["type"] not in ("has_outcome", "covers_topic", "has_assessment", "assesses")]
    if any(e["basis"] != "explicit_statement" or e["review_state"] != "accepted" for e in dep):
        return "inferred"
    return "documented"


def availability_for(snap: Snapshot, target_course: str, pathway: str | None = None) -> EvalContext:
    avail: dict[str, Availability] = {}
    credits: dict[str, float] = {}
    t_target = time_index(snap.primary_placement(target_course))
    for c in snap.courses():
        code = c["key"]
        if c["details"].get("credits") is not None:
            credits[code] = float(c["details"]["credits"])
        pl = snap.primary_placement(c["id"])
        if pl is None:
            avail[code] = Availability.ABSENT
            continue
        t = time_index(pl)
        if t is None or t_target is None:
            avail[code] = Availability.UNKNOWN_TIMING
        elif t > t_target:
            avail[code] = Availability.AFTER
        elif t == t_target:
            avail[code] = Availability.CONCURRENT
        else:
            ex = exposure(snap, c["id"], pathway)
            avail[code] = Availability.REQUIRED_BEFORE if ex == "required_path" else Availability.ELECTIVE_BEFORE
    return EvalContext(avail, credits)


SAT_LABEL = {Sat.ALL: "satisfied_for_all", Sat.SOME: "conditional", Sat.UNKNOWN: "unknown", Sat.NONE: "unsatisfied"}


def evaluate_rules(snap: Snapshot, course_id: str, pathway: str | None = None) -> list[dict[str, Any]]:
    out = []
    ctx = availability_for(snap, course_id, pathway)
    for rule in sorted(snap.rules_for(course_id), key=lambda r: r["kind"]):
        if rule["kind"] == "antirequisite":
            continue
        expr = parse_expr(rule["expression"])
        res = evaluate(expr, ctx)
        out.append(
            {
                "rule_id": rule["id"],
                "kind": rule["kind"],
                "rendered": render(expr),
                "source_text": rule["source_text"],
                "basis": rule["basis"],
                "review_state": rule["review_state"],
                "evidence_span_id": rule["evidence_span_id"],
                "status": SAT_LABEL[res.sat],
                "reasons": res.reasons,
                "course_codes": sorted({r.code for r in iter_course_refs(expr)}),
            }
        )
    return out


def prior_learning(snap: Snapshot, entity_id: str, *, pathway: str | None = None, depth: int = 4) -> dict[str, Any]:
    is_course = snap.entities[entity_id]["type"] == "course"
    own = [
        r["target"]
        for r in snap.relationships.values()
        if is_course and _active(r) and r["source"] == entity_id and r["type"] in ("has_outcome", "covers_topic")
    ]
    starts = [entity_id, *sorted(set(own))]
    seen = _bfs(snap, starts, upstream_steps, depth)
    documented = _bfs(snap, starts, _documented_only(upstream_steps), depth)
    target_course = entity_id if is_course else (owner_courses(snap, entity_id) or [None])[0]

    def make_item(nid: str, nodes: list[str], edges: list[dict], d: int) -> dict[str, Any]:
        e = snap.entities[nid]
        kinds = {x["type"] for x in edges}
        relation = (
            "formal_requirement"
            if kinds <= {"formal_prerequisite"}
            else "inferred_preparation"
            if "inferred_preparation" in kinds
            else "topic_or_outcome_preparation"
        )
        item: dict[str, Any] = {
            "entity": _entity_view(snap, nid),
            "depth": d,
            "relation": relation,
            "basis": "documented" if nid in documented and _path_basis(edges) == "documented" else _path_basis(edges),
            "path": nodes,
            "edges": edges,
        }
        if e["type"] == "course":
            item["exposure"] = exposure(snap, nid, pathway)
            if target_course:
                item["timing"] = timing(snap, nid, target_course)
        else:
            opps = []
            for c in owner_courses(snap, nid):
                if c == target_course:
                    continue
                opps.append(
                    {
                        "course": _entity_view(snap, c),
                        "exposure": exposure(snap, c, pathway),
                        "timing": timing(snap, c, target_course) if target_course else "unknown",
                    }
                )
            item["opportunities"] = opps
            if not any(o["timing"] == "earlier" for o in opps):
                item["gap"] = "No earlier documented learning opportunity found for this preparation."
        return item

    items = []
    for nid, (_prev, _edge, d) in seen.items():
        if nid in starts:
            continue
        nodes, edges = _path(seen, nid)
        items.append(make_item(nid, list(reversed(nodes)), list(reversed(edges)), d))
    # preparation between the course's own topics/outcomes (e.g. a topic it covers that also prepares its outcome)
    for r in snap.relationships.values():
        if (
            _active(r)
            and r["type"] == "prepares_for"
            and r["source"] in starts
            and r["target"] in starts
            and r["source"] != entity_id
        ):
            items.append(make_item(r["source"], [r["source"], r["target"]], [_edge_view(snap, r)], 1))
    items.sort(key=lambda i: (i["depth"], i["entity"]["type"] != "course", i["entity"]["key"]))
    rules = evaluate_rules(snap, entity_id, pathway) if is_course else []
    return {
        "entity": _entity_view(snap, entity_id),
        "requirement_rules": rules,
        "items": items,
        "assumptions": {
            "pathway": pathway or "all students on the required path",
            "language": "Students are expected to have encountered these; this does not establish mastery.",
            "elective_note": "Elective or pathway-specific preparation is not treated as universal.",
        },
    }


def downstream(snap: Snapshot, entity_id: str, *, depth: int = 6) -> dict[str, Any]:
    seen = _bfs(snap, entity_id, downstream_steps, depth)
    documented = _bfs(snap, entity_id, _documented_only(downstream_steps), depth)
    items = []
    for nid, (_p, _e, d) in seen.items():
        if nid == entity_id:
            continue
        e = snap.entities[nid]
        nodes, edges = _path(seen, nid)
        if e["type"] in ("course_outcome", "topic") and all(
            x["type"] in ("has_outcome", "covers_topic") for x in edges
        ):
            continue  # own contribution
        basis = "documented" if nid in documented else _path_basis(edges)
        if basis == "documented" and nid in documented:
            nodes, edges = _path(documented, nid)
        item = {"entity": _entity_view(snap, nid), "depth": d, "basis": basis, "path": nodes, "edges": edges}
        if e["type"] == "course_outcome":
            item["owner_courses"] = [_entity_view(snap, c) for c in owner_courses(snap, nid)]
        items.append(item)

    # Dependent courses reached through outcomes (course B's outcome depends on this course's learning)
    dependent_courses: dict[str, dict] = {}
    for it in items:
        cands = (
            [it["entity"]["id"]] if it["entity"]["type"] == "course" else [c["id"] for c in it.get("owner_courses", [])]
        )
        for c in cands:
            if c == entity_id:
                continue
            prev = dependent_courses.get(c)
            if prev is None or (prev["basis"] != "documented" and it["basis"] == "documented"):
                dependent_courses[c] = {"course": _entity_view(snap, c), "via": it["path"], "basis": it["basis"]}

    # Alternatives: for dependent courses whose rule lists this course inside an OR
    alternatives = []
    if snap.entities[entity_id]["type"] == "course":
        code = snap.entities[entity_id]["key"]
        for c in dependent_courses:
            for rule in snap.rules_for(c, "prerequisite"):
                alts = _or_alternatives(parse_expr(rule["expression"]), code)
                if alts:
                    alternatives.append(
                        {
                            "course": _entity_view(snap, c),
                            "alternatives": alts,
                            "rule": render(parse_expr(rule["expression"])),
                        }
                    )
    items.sort(key=lambda i: (i["depth"], i["entity"]["type"], i["entity"]["key"]))
    return {
        "entity": _entity_view(snap, entity_id),
        "items": items,
        "dependent_courses": sorted(dependent_courses.values(), key=lambda x: x["course"]["key"]),
        "alternatives": alternatives,
    }


def _or_alternatives(expr, code: str) -> list[str]:
    from cfs.curriculum.rules import AndExpr, CourseRef, OrExpr

    if isinstance(expr, OrExpr):
        codes = [x.code for x in expr.items if isinstance(x, CourseRef)]
        if code in codes:
            return [c for c in codes if c != code]
    if isinstance(expr, (AndExpr, OrExpr)):
        for it in expr.items:
            r = _or_alternatives(it, code)
            if r:
                return r
    return []


def contribution(snap: Snapshot, course_id: str) -> dict[str, Any]:
    outcomes = []
    for r in snap.rels("has_outcome", source=course_id):
        oid = r["target"]
        plos = [
            {
                "plo": _entity_view(snap, c["target"]),
                "level": c["level"],
                "basis": c["basis"],
                "review_state": c["review_state"],
                "has_evidence": c["evidence"]["supporting"] > 0,
            }
            for c in snap.rels("contributes_to", source=oid)
        ]
        assessed_by = [_entity_view(snap, a["source"]) for a in snap.rels("assesses", target=oid)]
        outcomes.append(
            {
                "outcome": {
                    **_entity_view(snap, oid),
                    "statement_verbatim": snap.entities[oid]["details"].get("statement_verbatim"),
                    "field_evidence": snap.entities[oid]["field_evidence"],
                },
                "program_outcomes": plos,
                "assessed_by": assessed_by,
                "basis": r["basis"],
                "has_evidence": r["evidence"]["supporting"] > 0,
            }
        )
    topics = [
        {"topic": _entity_view(snap, r["target"]), "basis": r["basis"], "has_evidence": r["evidence"]["supporting"] > 0}
        for r in snap.rels("covers_topic", source=course_id)
    ]
    assessments = []
    for r in snap.rels("has_assessment", source=course_id):
        a = snap.entities[r["target"]]
        assessments.append(
            {
                "assessment": _entity_view(snap, a["id"]),
                "details": a["details"],
                "field_evidence": a["field_evidence"],
                "assesses": [_entity_view(snap, x["target"]) for x in snap.rels("assesses", source=a["id"])],
            }
        )
    for lst, k in ((outcomes, "outcome"), (topics, "topic"), (assessments, "assessment")):
        lst.sort(key=lambda x: x[k]["key"])
    return {"outcomes": outcomes, "topics": topics, "assessments": assessments}
