"""Alignment Web projection: every curriculum element and how strongly it connects to everything else.

Unlike the course-centric map (graph/view.py), nothing is collapsed onto courses: program outcomes, course
outcomes, assessments, and topics are all nodes. Edges carry a category (vertical / horizontal / containment /
overlap), a provenance (documented / accepted_inference / proposed / scenario), and a computed strength
(cfs.analysis.alignment). Coordinates are never part of this response; the client lays out deterministically.

Large programs are served as an overview (courses + program outcomes with bundled edges) unless a focus is given.
"""

from __future__ import annotations

from typing import Any

from cfs.analysis import alignment as al
from cfs.graph.queries import owner_courses
from cfs.graph.snapshot import Snapshot, time_index
from cfs.graph.view import evidence_completeness

WEB_TYPES = ("course", "program_outcome", "course_outcome", "assessment", "topic")
FULL_NODE_LIMIT = 600
TERM_LABEL = {
    "fall": "Fall",
    "winter": "Winter",
    "spring": "Spring",
    "summer": "Summer",
    "full_year": "Full year",
    "unknown": "Term not stated",
}


def _band(pl: dict[str, Any] | None) -> str:
    if not pl or pl.get("year") is None:
        return "Timing unknown"
    return f"Year {pl['year']} · {TERM_LABEL.get(pl.get('term') or 'unknown', 'Term not stated')}"


def _focus_set(snap: Snapshot, rels: dict[str, dict], focus: str, active) -> set[str]:
    """Nodes in the neighbourhood of a program outcome, course, or single element."""
    e = snap.entities.get(focus)
    if e is None:
        return set()
    keep = {focus}
    by_src: dict[str, list[dict]] = {}
    by_tgt: dict[str, list[dict]] = {}
    for r in rels.values():
        if active(r):
            by_src.setdefault(r["source"], []).append(r)
            by_tgt.setdefault(r["target"], []).append(r)

    def owners(n: str) -> list[str]:
        return owner_courses(snap, n)

    def vertical_below(clos: set[str]) -> set[str]:
        out = set()
        for c in clos:
            for r in by_tgt.get(c, []):
                if r["type"] in ("assesses", "supports"):
                    out.add(r["source"])
        return out

    t = e["type"]
    if t == "program_outcome":
        clos = {r["source"] for r in by_tgt.get(focus, []) if r["type"] == "contributes_to"}
        keep |= clos | vertical_below(clos)
        for x in clos:
            keep.update(owners(x) or ([x] if snap.entities.get(x, {}).get("type") == "course" else []))
    elif t == "course":
        members = {r["target"] for r in by_src.get(focus, []) if r["type"] in al.CONTAINMENT}
        keep |= members
        for m in list(members):
            for r in by_src.get(m, []):
                if r["type"] in ("contributes_to", "prepares_for"):
                    keep.add(r["target"])
                    keep.update(owners(r["target"]))
            for r in by_tgt.get(m, []):
                if r["type"] in ("prepares_for", "supports", "assesses"):
                    keep.add(r["source"])
                    if r["type"] == "prepares_for":
                        keep.update(owners(r["source"]))
        for r in by_src.get(focus, []) + by_tgt.get(focus, []):
            if r["type"] in ("formal_prerequisite", "inferred_preparation", "contributes_to"):
                keep |= {r["source"], r["target"]}
    else:
        for r in by_src.get(focus, []) + by_tgt.get(focus, []):
            if r["type"] in al.ALIGNMENT_LINKS:
                keep |= {r["source"], r["target"]}
        keep.update(owners(focus))
        for n in list(keep):
            if n != focus and snap.entities.get(n, {}).get("type") == "course_outcome":
                keep.update(owners(n))
    return {n for n in keep if n in snap.entities or n in rels}


def build_web(
    snap: Snapshot,
    *,
    detail: str = "auto",
    focus: str | None = None,
    pathway: str | None = None,
    include_rejected: bool = False,
    diff: dict[str, Any] | None = None,
    base: Snapshot | None = None,
) -> dict[str, Any]:
    idx = al.alignment_index(snap, pathway)
    base_idx = al.alignment_index(base, pathway) if base is not None else None

    entities = {k: v for k, v in snap.entities.items() if v["type"] in WEB_TYPES}
    rels = dict(snap.relationships)
    placements = dict(snap.placements)
    ent_status = dict((diff or {}).get("entities", {}))
    rel_status = dict((diff or {}).get("relationships", {}))
    if base is not None and diff:
        for eid, st in diff.get("entities", {}).items():
            if st == "removed" and eid in base.entities and base.entities[eid]["type"] in WEB_TYPES:
                entities[eid] = base.entities[eid]
                if eid in base.placements:
                    placements[eid] = base.placements[eid]
        for k, st in diff.get("relationships", {}).items():
            if st == "removed" and k in base.relationships:
                rels[k] = base.relationships[k]
    if base_idx is not None:
        for k, st in al.compare_edges(base_idx, idx).items():
            rel_status.setdefault(k, st)
            if rel_status[k] == "modified":
                rel_status[k] = st  # a strength-bearing modification reads better as its direction

    def active(r: dict) -> bool:
        return include_rejected or r["review_state"] not in ("rejected", "superseded") or rel_status.get(r["key"])

    total = len(entities)
    mode = detail if detail in ("full", "overview") else ("full" if total <= FULL_NODE_LIMIT else "overview")
    focus_snap = snap if focus in snap.entities else (base or snap)
    focus_nodes = _focus_set(focus_snap, rels, focus, active) if focus else set()

    if mode == "full":
        visible = set(entities)
    else:
        visible = {k for k, e in entities.items() if e["type"] in ("course", "program_outcome")} | focus_nodes
    visible &= set(entities)

    def owner_of(n: str) -> list[str]:
        own = owner_courses(snap, n)
        if not own and base is not None:
            own = owner_courses(base, n)
        return own

    # ── nodes ──
    nodes = []
    for n in sorted(visible, key=lambda x: (WEB_TYPES.index(entities[x]["type"]), entities[x]["key"])):
        e = entities[n]
        pls = placements.get(n) or []
        pl = pls[0] if pls else None
        owners = owner_of(n) if e["type"] != "course" else []
        metrics = idx["nodes"].get(n) or (base_idx["nodes"].get(n) if base_idx else None) or {}
        status = ent_status.get(n, "unchanged")
        if status == "unchanged" and base_idx is not None and n in base_idx["nodes"] and n in idx["nodes"]:
            b, a = base_idx["nodes"][n].get("score"), idx["nodes"][n].get("score")
            if b is not None and a is not None and abs(a - b) >= al.DELTA:
                status = "strengthened" if a > b else "weakened"
        node = {
            "id": n,
            "type": e["type"],
            "key": e["key"],
            "title": e["title"],
            "description": e["description"],
            "course_id": owners[0] if owners else None,
            "course_ids": owners,
            "year": pl["year"] if pl else None,
            "term": pl["term"] if pl else None,
            "time_index": time_index(pl),
            "classification": pl["classification"] if pl else None,
            "details": e["details"],
            "origin": e["origin"],
            "is_synthetic": e["is_synthetic"],
            "evidence": evidence_completeness(e),
            "status": status,
            "metrics": metrics,
            "metrics_before": (base_idx["nodes"].get(n) if base_idx else None),
            "aggregate": None,
        }
        if e["type"] == "course":
            members: dict[str, int] = {"course_outcome": 0, "assessment": 0, "topic": 0}
            for r in snap.rels(source=n):
                if r["type"] in al.CONTAINMENT and r["target"] in entities and active(r):
                    members[entities[r["target"]]["type"]] = members.get(entities[r["target"]]["type"], 0) + 1
            node["aggregate"] = {
                "clos": members["course_outcome"],
                "assessments": members["assessment"],
                "topics": members["topic"],
            }
        nodes.append(node)

    # ── edges ──
    edges = []
    for r in sorted(rels.values(), key=lambda r: r["key"]):
        if not active(r) or r["source"] not in visible or r["target"] not in visible:
            continue
        st = idx["edges"].get(r["key"]) or (base_idx["edges"].get(r["key"]) if base_idx else None)
        before = base_idx["edges"].get(r["key"]) if base_idx else None
        edges.append(
            {
                "id": r["key"],
                "key": r["key"],
                "revision_id": r.get("revision_id"),
                "source": r["source"],
                "target": r["target"],
                "type": r["type"],
                "category": al.category(r["type"]),
                "level": r.get("level"),
                "origin": r["origin"],
                "basis": r["basis"],
                "review_state": r["review_state"],
                "provenance": al.provenance(r),
                "strength": st,
                "strength_before": before["effective"] if before else None,
                "rationale": r.get("rationale"),
                "has_evidence": r["evidence"]["supporting"] > 0,
                "contradicted": r["evidence"]["contradicting"] > 0,
                "status": rel_status.get(r["key"], "unchanged"),
                "bundle": None,
            }
        )

    if mode == "overview":
        edges += _bundles(snap, idx, visible, focus_nodes, entities)

    columns = []
    for c in sorted(
        (e for e in entities.values() if e["type"] == "course"),
        key=lambda c: (
            time_index((placements.get(c["id"]) or [None])[0])
            if time_index((placements.get(c["id"]) or [None])[0]) is not None
            else 999,
            c["key"],
        ),
    ):
        pl = (placements.get(c["id"]) or [None])[0]
        columns.append(
            {
                "course_id": c["id"],
                "key": c["key"],
                "title": c["title"],
                "year": pl["year"] if pl else None,
                "term": pl["term"] if pl else None,
                "time_index": time_index(pl),
                "band": _band(pl),
            }
        )

    by_type: dict[str, int] = {}
    for n in nodes:
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1
    focus_edges = [e["id"] for e in edges if e["source"] in focus_nodes and e["target"] in focus_nodes]
    return {
        "curriculum_version": snap.version,
        "scenario": None,
        "detail": mode,
        "strength_model": {
            "version": al.ALIGNMENT_VERSION,
            "thresholds": {"strong": al.STRONG, "moderate": al.MODERATE},
            "formula": "effective = trust × intensity × support",
        },
        "columns": columns,
        "nodes": nodes,
        "edges": edges,
        "gaps": idx["gaps"],
        "gaps_before": base_idx["gaps"] if base_idx else None,
        "plo_threads": idx["threads"],
        "course_pairs": idx["course_pairs"],
        "scorecard": idx["scorecard"],
        "scorecard_baseline": base_idx["scorecard"] if base_idx else None,
        "focus": (
            {
                "id": focus,
                "kind": (snap.entities.get(focus) or (base.entities.get(focus) if base else None) or {}).get("type"),
                "node_ids": sorted(focus_nodes),
                "edge_ids": focus_edges,
            }
            if focus
            else None
        ),
        "stats": {"nodes": len(nodes), "edges": len(edges), "by_type": by_type, "total_nodes": total},
        "truncated": (
            {
                "total": total,
                "shown": len(nodes),
                "message": "Overview: courses and program outcomes only. Select a course or program outcome to see "
                "its outcomes, assessments, and topics.",
            }
            if mode == "overview"
            else None
        ),
    }


def _bundles(
    snap: Snapshot, idx: dict[str, Any], visible: set[str], focus_nodes: set[str], entities: dict[str, dict]
) -> list[dict[str, Any]]:
    """Overview edges: course → program outcome (via its outcomes) and course → course (via bridges)."""
    groups: dict[tuple[str, str, str], list[str]] = {}
    for k in idx["edges"]:
        r = snap.relationships[k]
        if r["type"] != "contributes_to" or r["review_state"] in ("rejected", "superseded"):
            continue
        if r["source"] in visible and r["source"] in focus_nodes:
            continue  # already drawn in full
        for c in owner_courses(snap, r["source"]) or []:
            if c in visible and r["target"] in visible:
                groups.setdefault(("contributes_to", c, r["target"]), []).append(k)
    for p in idx["course_pairs"]:
        if p["source_course"] in visible and p["target_course"] in visible and p["edge_keys"]:
            groups.setdefault(("prepares_for", p["source_course"], p["target_course"]), []).extend(p["edge_keys"])
    out = []
    for (t, s, tg), keys in sorted(groups.items()):
        vals = [idx["edges"][k]["effective"] for k in keys if k in idx["edges"]]
        best = max(vals) if vals else None
        out.append(
            {
                "id": f"bundle:{t}:{s}:{tg}",
                "key": None,
                "revision_id": None,
                "source": s,
                "target": tg,
                "type": t,
                "category": "vertical" if t == "contributes_to" else "horizontal",
                "level": None,
                "origin": "derived",
                "basis": "mixed",
                "review_state": "accepted",
                "provenance": "documented"
                if all(al.provenance(snap.relationships[k]) == "documented" for k in keys)
                else "accepted_inference",
                "strength": {
                    "effective": best,
                    "label": al.label_for(best),
                    "components": None,
                    "asserted": None,
                    "asserted_source": None,
                },
                "strength_before": None,
                "rationale": None,
                "has_evidence": False,
                "contradicted": False,
                "status": "unchanged",
                "bundle": {"count": len(keys), "keys": sorted(keys), "min": min(vals) if vals else None, "max": best},
            }
        )
    return out
