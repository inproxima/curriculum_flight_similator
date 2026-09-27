"""Graph view projection for the map: layers, progressive disclosure, collapse, and visible-node budget.

Hidden nodes (outcomes, topics, assessments) collapse onto their owning course when that course is
visible, producing clearly marked *derived* edges (`derived: true`, `via: [...]`) rather than
silently inventing course-to-course links. Coordinates are never part of this response.
"""

from __future__ import annotations

from typing import Any

from cfs.graph.queries import owner_courses
from cfs.graph.snapshot import Snapshot

LAYER_TYPES: dict[str, set[str]] = {
    "prerequisites": {"formal_prerequisite", "corequisite"},
    "preparation": {"inferred_preparation", "prepares_for"},
    "plo_alignment": {"contributes_to"},
    "topics": {"covers_topic", "prepares_for", "possible_overlap"},
    "assessment": {"assesses"},
}
DEFAULT_LAYERS = ["prerequisites", "preparation"]
CONTAINMENT = {"has_outcome", "covers_topic", "has_assessment"}
COURSE_EVIDENCE_FIELDS = ["title", "description", "credits", "year", "term", "classification"]


def evidence_completeness(e: dict[str, Any]) -> dict[str, Any]:
    fields = (
        COURSE_EVIDENCE_FIELDS
        if e["type"] == "course"
        else (
            ["statement"]
            if e["type"] == "course_outcome"
            else ["format", "weight_percent", "timing_week"]
            if e["type"] == "assessment"
            else []
        )
    )
    have = [f for f in fields if e["field_evidence"].get(f)]
    return {"fields": fields, "with_evidence": have, "ratio": (len(have) / len(fields)) if fields else None}


def build_view(
    snap: Snapshot,
    *,
    layers: list[str] | None = None,
    expand: list[str] | None = None,
    focus: str | None = None,
    focus_depth: int = 1,
    include_rejected: bool = False,
    node_budget: int = 300,
    diff: dict[str, Any] | None = None,
    removed_snapshot: Snapshot | None = None,
) -> dict[str, Any]:
    layers = [ly for ly in (layers or DEFAULT_LAYERS) if ly in LAYER_TYPES]
    types: set[str] = set().union(*(LAYER_TYPES[ly] for ly in layers)) if layers else set()
    expand_set = set(expand or [])

    # combine current snapshot with removed baseline items (scenario comparison overlay)
    entities = dict(snap.entities)
    relationships = dict(snap.relationships)
    placements = dict(snap.placements)
    if removed_snapshot is not None and diff:
        for eid, st in diff.get("entities", {}).items():
            if st == "removed" and eid in removed_snapshot.entities:
                entities[eid] = removed_snapshot.entities[eid]
                if eid in removed_snapshot.placements:
                    placements[eid] = removed_snapshot.placements[eid]
        for k, st in diff.get("relationships", {}).items():
            if st == "removed" and k in removed_snapshot.relationships:
                relationships[k] = removed_snapshot.relationships[k]

    def active(r: dict) -> bool:
        return include_rejected or r["review_state"] not in ("rejected", "superseded")

    # visible nodes
    visible: set[str] = {eid for eid, e in entities.items() if e["type"] == "course"}
    if "plo_alignment" in layers:
        visible |= {eid for eid, e in entities.items() if e["type"] == "program_outcome"}
    if "topics" in layers:
        visible |= {eid for eid, e in entities.items() if e["type"] == "topic"}
    for r in relationships.values():
        if r["type"] in CONTAINMENT and r["source"] in expand_set and active(r):
            visible.add(r["target"])
    if focus and focus in entities:
        # neighborhood: courses within focus_depth hops over selected layer types (plus expansion of focus)
        keep = {focus}
        frontier = {focus}
        for _ in range(max(1, focus_depth)):
            nxt = set()
            for r in relationships.values():
                if r["type"] in types and active(r):
                    if r["source"] in frontier:
                        nxt.add(r["target"])
                    if r["target"] in frontier:
                        nxt.add(r["source"])
            nxt = {rep for n in nxt for rep in ([n] if n in visible else owner_courses(snap, n)[:1])}
            keep |= nxt
            frontier = nxt
        visible = {
            n
            for n in visible
            if n in keep
            or (
                n in entities
                and any(
                    r["source"] in keep and r["target"] == n and r["type"] in CONTAINMENT
                    for r in relationships.values()
                )
            )
        }

    truncated = None
    if len(visible) > node_budget:
        ordered = sorted(visible, key=lambda n: (entities[n]["type"] != "course", entities[n]["key"]))
        truncated = {
            "total": len(visible),
            "shown": node_budget,
            "message": "Visible-node budget exceeded; use filters, focus, or the table view.",
        }
        visible = set(ordered[:node_budget])

    def rep(n: str) -> str | None:
        if n in visible:
            return n
        for c in owner_courses(snap, n):
            if c in visible:
                return c
        return None

    ent_status = (diff or {}).get("entities", {})
    rel_status = (diff or {}).get("relationships", {})

    edges: dict[str, dict[str, Any]] = {}
    for r in sorted(relationships.values(), key=lambda r: r["key"]):
        show_containment = r["type"] in CONTAINMENT and r["source"] in expand_set
        if not active(r) or (r["type"] not in types and not show_containment):
            continue
        s, t = rep(r["source"]), rep(r["target"])
        if s is None or t is None or s == t:
            continue
        derived = (s != r["source"]) or (t != r["target"])
        status = rel_status.get(r["key"], "unchanged")
        ek = r["key"] if not derived else f"derived:{r['type']}:{s}:{t}:{status}"
        if ek in edges:
            agg = edges[ek]
            agg["via"].append(r["key"])
            agg["basis"] = agg["basis"] if agg["basis"] == r["basis"] else "mixed"
            if r["review_state"] != "accepted":
                agg["review_state"] = r["review_state"]
            agg["has_evidence"] = agg["has_evidence"] and r["evidence"]["supporting"] > 0
            continue
        edges[ek] = {
            "id": ek,
            "key": r["key"] if not derived else None,
            "revision_id": None if derived else r["revision_id"],
            "source": s,
            "target": t,
            "type": r["type"],
            "basis": r["basis"],
            "origin": r["origin"],
            "review_state": r["review_state"],
            "level": r["level"],
            "rule_id": r["rule_id"],
            "has_evidence": r["evidence"]["supporting"] > 0,
            "contradicted": r["evidence"]["contradicting"] > 0,
            "derived": derived,
            "via": [r["key"]] if derived else [],
            "status": status,
            "rationale": r["rationale"],
            "is_synthetic": r["is_synthetic"],
        }

    # OR-alternative markers on formal prerequisite edges
    from cfs.curriculum.rules import leaf_edges, parse_expr

    alt_by_target: dict[str, dict[str, str]] = {}
    for rule in snap.rules.values():
        if rule["kind"] != "prerequisite":
            continue
        for ref, path, alt in leaf_edges(parse_expr(rule["expression"])):
            if alt:
                alt_by_target.setdefault(rule["target"], {})[ref.code] = path.rsplit(".or", 1)[0]
    for e in edges.values():
        if e["type"] == "formal_prerequisite" and not e["derived"]:
            code = entities[e["source"]]["key"]
            grp = alt_by_target.get(e["target"], {}).get(code)
            e["alternative_group"] = f"{e['target']}:{grp}" if grp else None

    nodes = []
    for n in sorted(visible, key=lambda x: (entities[x]["type"], entities[x]["key"])):
        e = entities[n]
        pls = placements.get(n) or []
        pl = pls[0] if pls else None
        nodes.append(
            {
                "id": n,
                "type": e["type"],
                "key": e["key"],
                "title": e["title"],
                "description": e["description"],
                "year": pl["year"] if pl else None,
                "term": pl["term"] if pl else None,
                "classification": pl["classification"] if pl else None,
                "pathway": pl["pathway"] if pl else None,
                "placements": pls,
                "details": e["details"],
                "origin": e["origin"],
                "is_synthetic": e["is_synthetic"],
                "evidence": evidence_completeness(e),
                "status": ent_status.get(n, "unchanged"),
                "owner_course": (owner_courses(snap, n)[:1] or [None])[0] if e["type"] != "course" else None,
                "expanded": n in expand_set,
            }
        )
    return {
        "curriculum_version": snap.version,
        "layers": layers,
        "available_layers": list(LAYER_TYPES),
        "nodes": nodes,
        "edges": list(edges.values()),
        "truncated": truncated,
        "stats": {"nodes": len(nodes), "edges": len(edges), "courses": sum(1 for x in nodes if x["type"] == "course")},
    }
