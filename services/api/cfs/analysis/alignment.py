"""Alignment strength: how well the curriculum's pieces connect (ADR 0003). Deterministic; no model involvement.

Vertical alignment    topic → course outcome (supports), assessment → course outcome (assesses),
                      course outcome → program outcome (contributes_to)
Horizontal alignment  earlier course outcome / assessment → later course outcome (prepares_for), summarised per
                      course pair (formal prerequisite or pedagogical preparation)

Per-edge strength:  effective = trust × intensity × support

  trust      documented (explicit, accepted) 1.00 · accepted interpretation 0.85 · manual/scenario 0.80 · proposed 0.60
  intensity  the asserted strength when one exists (AI estimate, reviewer, fixture, scenario); otherwise
             contributes_to by level (assess 1.0, reinforce 0.8, introduce 0.6, unstated 0.7),
             assesses by the assessment's weight (0.55 + 0.45·min(w, 40)/40; unknown 0.7), others 0.7
  support    contributes_to from a course outcome: 0.7, +0.2 if the outcome is assessed, +0.1 if a topic supports it;
             everything else 1.0

Labels are computed, never stored: strong ≥ 0.70 · moderate ≥ 0.45 · weak > 0 · missing = an expected link is absent.
A chain is as strong as its weakest link (minimum); a course pair is as strong as its best bridge (maximum).
Documented links outrank AI proposals by construction (trust 1.0 vs 0.6).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from cfs.analysis.coverage import outcome_matrix
from cfs.graph.queries import owner_courses
from cfs.graph.snapshot import Snapshot, time_index

ALIGNMENT_VERSION = "align-1.0.0"
STRONG, MODERATE = 0.70, 0.45
DELTA = 0.05  # |Δ| at or above this (or a label change) counts as strengthened / weakened

VERTICAL = {"contributes_to", "assesses", "supports"}
HORIZONTAL = {"prepares_for", "inferred_preparation", "formal_prerequisite", "corequisite"}
CONTAINMENT = {"has_outcome", "covers_topic", "has_assessment", "has_activity"}
ALIGNMENT_LINKS = {"contributes_to", "assesses", "supports", "prepares_for"}  # links that carry a strength

# Allowed (source type, target type) per relationship type. Every write path (scenario ops, AI proposals,
# inline decisions) validates against this table; the database itself does not constrain type pairs.
TYPE_PAIRS: dict[str, set[tuple[str, str]]] = {
    "has_outcome": {("course", "course_outcome")},
    "covers_topic": {("course", "topic")},
    "has_assessment": {("course", "assessment")},
    "has_activity": {("course", "activity")},
    "contributes_to": {("course_outcome", "program_outcome"), ("course", "program_outcome")},
    "assesses": {("assessment", "course_outcome"), ("assessment", "program_outcome")},
    "supports": {("topic", "course_outcome"), ("activity", "course_outcome"), ("activity", "topic")},
    "prepares_for": {
        ("course_outcome", "course_outcome"),
        ("assessment", "course_outcome"),
        ("topic", "topic"),
        ("topic", "course_outcome"),
        ("topic", "assessment"),
        ("course_outcome", "assessment"),
    },
    "formal_prerequisite": {("course", "course")},
    "corequisite": {("course", "course")},
    "inferred_preparation": {("course", "course")},
    "possible_overlap": {("course", "course"), ("topic", "topic")},
}

LEVEL_INTENSITY = {"assess": 1.0, "reinforce": 0.8, "introduce": 0.6}
STAGES = ("introduce", "reinforce", "assess")


# How directly a link connects two elements, shown by colour on every view (with a glyph, never colour alone).
#   direct   the documents state it (explicit + accepted), or the AI judged the source to address the target directly
#   strong   clearly implied
#   partial  plausible, indirect or only part of the target: a surmise
# The AI's categorical judgment is stored as an asserted strength so it stays inspectable and editable.
CONNECTION_LEVELS = ("direct", "strong", "partial")
LEVEL_STRENGTH = {"direct": 0.95, "strong": 0.8, "partial": 0.5}


def connection_level(r: dict[str, Any], strength: dict[str, Any] | None = None) -> str | None:
    """direct | strong | partial for an active alignment link; None for structure or inactive links."""
    if r["type"] not in ALIGNMENT_LINKS or not _active(r):
        return None
    if r["basis"] == "explicit_statement" and r["review_state"] == "accepted":
        return "direct"
    a = r.get("strength")
    if a is not None:
        a = float(a)
        return "direct" if a >= 0.9 else "strong" if a >= 0.7 else "partial"
    label = (strength or {}).get("label")
    return "strong" if label == "strong" else "partial"


def pair_allowed(snap: Snapshot, rel_type: str, source: str, target: str) -> bool:
    s, t = snap.entities.get(source), snap.entities.get(target)
    return bool(s and t and (s["type"], t["type"]) in TYPE_PAIRS.get(rel_type, set()))


def _active(r: dict[str, Any]) -> bool:
    return r["review_state"] not in ("rejected", "superseded")


def label_for(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= STRONG:
        return "strong"
    if score >= MODERATE:
        return "moderate"
    return "weak" if score > 0 else "missing"


def category(rel_type: str) -> str:
    if rel_type in VERTICAL:
        return "vertical"
    if rel_type in HORIZONTAL:
        return "horizontal"
    if rel_type in CONTAINMENT:
        return "containment"
    return "overlap"


def provenance(r: dict[str, Any]) -> str:
    if r.get("scenario"):
        return "scenario"
    if r["review_state"] == "proposed":
        return "proposed"
    if r["basis"] == "explicit_statement" and r["review_state"] == "accepted":
        return "documented"
    return "accepted_inference"


def _trust(r: dict[str, Any]) -> float:
    if r["review_state"] == "proposed":
        return 0.6
    if r["basis"] == "explicit_statement" and r["review_state"] == "accepted":
        return 1.0
    if r["origin"] == "manual" or r.get("scenario"):
        return 0.8
    return 0.85


def _intensity(snap: Snapshot, r: dict[str, Any]) -> float:
    if r.get("strength") is not None:
        return float(r["strength"])
    if r["type"] == "contributes_to":
        return LEVEL_INTENSITY.get(r.get("level") or "", 0.7)
    if r["type"] == "assesses":
        w = (snap.entities.get(r["source"]) or {}).get("details", {}).get("weight_percent")
        return 0.7 if w is None else 0.55 + 0.45 * min(float(w), 40.0) / 40.0
    return 0.7


class _Idx:
    """Per-snapshot lookups shared by every calculation below."""

    def __init__(self, snap: Snapshot):
        self.assessed: dict[str, list[str]] = defaultdict(list)  # outcome → assessments
        self.supported: dict[str, list[str]] = defaultdict(list)  # outcome → topics
        for r in snap.relationships.values():
            if not _active(r):
                continue
            if r["type"] == "assesses":
                self.assessed[r["target"]].append(r["source"])
            elif r["type"] == "supports":
                self.supported[r["target"]].append(r["source"])


def _support(snap: Snapshot, r: dict[str, Any], idx: _Idx) -> float:
    if r["type"] != "contributes_to":
        return 1.0
    src = snap.entities.get(r["source"])
    if not src or src["type"] != "course_outcome":
        return 0.7
    assessed = 0.2 if idx.assessed.get(src["id"]) else 0.0
    taught = 0.1 if idx.supported.get(src["id"]) else 0.0
    return min(1.0, 0.7 + assessed + taught)


def edge_strength(snap: Snapshot, r: dict[str, Any], idx: _Idx | None = None) -> dict[str, Any] | None:
    """Strength of one alignment link; None for structural links (containment, requisites, overlap)."""
    if r["type"] not in ALIGNMENT_LINKS:
        return None
    idx = idx or _Idx(snap)
    trust, intensity, support = _trust(r), _intensity(snap, r), _support(snap, r, idx)
    eff = round(trust * intensity * support, 3) if _active(r) else 0.0
    return {
        "effective": eff,
        "label": label_for(eff) if _active(r) else "missing",
        "components": {"trust": trust, "intensity": round(intensity, 3), "support": round(support, 3)},
        "asserted": r.get("strength"),
        "asserted_source": r.get("strength_source"),
    }


def _time(snap: Snapshot, course_id: str | None) -> float | None:
    return time_index(snap.primary_placement(course_id)) if course_id else None


def _owner(snap: Snapshot, eid: str) -> str | None:
    e = snap.entities.get(eid)
    if not e:
        return None
    if e["type"] == "course":
        return eid
    owners = owner_courses(snap, eid)
    return owners[0] if owners else None


def plo_threads(snap: Snapshot, strengths: dict[str, dict[str, Any]], idx: _Idx) -> list[dict[str, Any]]:
    """For each program outcome: where it is introduced, reinforced, and assessed across the program, in time order."""
    out = []
    for plo in sorted((e for e in snap.entities.values() if e["type"] == "program_outcome"), key=lambda e: e["key"]):
        stages = []
        for r in snap.rels("contributes_to", target=plo["id"]):
            if not _active(r) or r["source"] not in snap.entities:
                continue
            src = snap.entities[r["source"]]
            course = _owner(snap, src["id"])
            pl = snap.primary_placement(course) if course else None
            st = strengths.get(r["key"]) or {}
            assessments = sorted(idx.assessed.get(src["id"], [])) if src["type"] == "course_outcome" else []
            stages.append(
                {
                    "course_id": course,
                    "course_key": snap.entities[course]["key"] if course else None,
                    "year": pl["year"] if pl else None,
                    "term": pl["term"] if pl else None,
                    "clo_id": src["id"] if src["type"] == "course_outcome" else None,
                    "clo_key": src["key"] if src["type"] == "course_outcome" else None,
                    "relationship_key": r["key"],
                    "level": r.get("level"),
                    "effective": st.get("effective"),
                    "label": st.get("label"),
                    "provenance": provenance(r),
                    "assessed": bool(assessments),
                    "assessment_ids": assessments,
                    "_t": _time(snap, course),
                }
            )
        stages.sort(key=lambda s: (s["_t"] if s["_t"] is not None else 999, s["course_key"] or "", s["clo_key"] or ""))
        levels = {s["level"] for s in stages if s["level"]}
        missing = [lv for lv in STAGES if lv not in levels] if levels else []
        first = {lv: next((s for s in stages if s["level"] == lv), None) for lv in STAGES}
        if not stages:
            status = "uncovered"
        elif not levels:
            status = "levels_unstated"
        elif "assess" not in levels:
            status = "never_assessed"
        elif missing:
            status = "incomplete"
        else:
            ti, tr, ta = (first[lv]["_t"] for lv in STAGES)  # type: ignore[index]
            ordered = all(x is not None for x in (ti, tr, ta))
            status = "out_of_order" if ordered and not (ti <= tr <= ta) else "complete"
        if not stages:
            score = None
        elif levels:
            best = {lv: max((s["effective"] or 0) for s in stages if s["level"] == lv) for lv in levels}
            score = round(sum(best.get(lv, 0.0) for lv in STAGES) / len(STAGES), 3)
        else:
            score = max((s["effective"] or 0) for s in stages)
        for s in stages:
            s.pop("_t")
        out.append(
            {
                "plo_id": plo["id"],
                "plo_key": plo["key"],
                "status": status,
                "missing": missing,
                "strength": score,
                "label": label_for(score) if score is not None else "missing",
                "firsts": {lv: (first[lv]["course_key"] if first[lv] else None) for lv in STAGES},
                "stages": stages,
            }
        )
    return out


def course_pairs(snap: Snapshot, strengths: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Course → later course relations and the outcome-level links that bridge them."""
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for r in sorted(snap.rels("formal_prerequisite", "inferred_preparation"), key=lambda r: (r["type"], r["key"])):
        if not _active(r) or r["source"] not in snap.entities or r["target"] not in snap.entities:
            continue
        k = (r["source"], r["target"])
        if k not in pairs:
            pairs[k] = {"via": r["type"], "course_edge_keys": []}
        pairs[k]["course_edge_keys"].append(r["key"])
    bridges: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in snap.rels("prepares_for"):
        # topics can belong to several courses, so only outcome / assessment links identify a course bridge
        if not _active(r) or any(snap.entities.get(x, {}).get("type") == "topic" for x in (r["source"], r["target"])):
            continue
        a, b = _owner(snap, r["source"]), _owner(snap, r["target"])
        if a and b and a != b:
            bridges[(a, b)].append(r["key"])
            if (a, b) not in pairs:
                pairs[(a, b)] = {"via": "outcome_link", "course_edge_keys": []}
    out = []
    for (a, b), p in pairs.items():
        keys = sorted(bridges.get((a, b), []), key=lambda k: -(strengths.get(k, {}).get("effective") or 0))
        best = max((strengths[k]["effective"] for k in keys if k in strengths), default=None)
        out.append(
            {
                "source_course": a,
                "target_course": b,
                "source_key": snap.entities[a]["key"],
                "target_key": snap.entities[b]["key"],
                "via": p["via"],
                "course_edge_keys": p["course_edge_keys"],
                "edge_keys": keys,
                "strength": best,
                "label": label_for(best) if best is not None else "missing",
            }
        )
    out.sort(
        key=lambda p: (
            _time(snap, p["source_course"]) if _time(snap, p["source_course"]) is not None else 999,
            p["source_key"],
            p["target_key"],
        )
    )
    return out


def _gap(kind: str, severity: str, message: str, ids: list[str], source=None, target=None) -> dict[str, Any]:
    return {
        "id": f"gap:{kind}:{'|'.join(ids)}",
        "kind": kind,
        "severity": severity,
        "message": message,
        "entity_ids": ids,
        "source": source,
        "target": target,
    }


def gaps(snap: Snapshot, threads: list[dict[str, Any]], pairs: list[dict[str, Any]], idx: _Idx) -> list[dict]:
    out: list[dict[str, Any]] = []
    out_types: dict[str, set[str]] = defaultdict(set)
    for r in snap.relationships.values():
        if _active(r):
            out_types[r["source"]].add(r["type"])
    for e in sorted(snap.entities.values(), key=lambda e: (e["type"], e["key"])):
        eid, t = e["id"], e["type"]
        if t == "course_outcome":
            if "contributes_to" not in out_types[eid]:
                out.append(_gap("clo_no_plo", "medium", f"{e['key']} is not linked to any program outcome.", [eid]))
            if not idx.assessed.get(eid):
                out.append(_gap("clo_unassessed", "low", f"No assessment is linked to {e['key']}.", [eid]))
        elif t == "assessment":
            if "assesses" not in out_types[eid]:
                out.append(
                    _gap("assessment_no_clo", "medium", f"{e['title']} is not linked to any learning outcome.", [eid])
                )
        elif t == "topic":
            if owner_courses(snap, eid) and not ({"supports", "prepares_for"} & out_types[eid]):
                out.append(_gap("topic_no_clo", "low", f"Topic '{e['title']}' supports no learning outcome.", [eid]))
    for th in threads:
        if th["status"] == "uncovered":
            msg = f"{th['plo_key']} has no contributing course outcome."
            out.append(_gap("plo_no_clo", "high", msg, [th["plo_id"]]))
        elif th["status"] == "never_assessed":
            where = ", ".join(sorted({s["course_key"] for s in th["stages"] if s["course_key"]}))
            out.append(
                _gap(
                    "plo_never_assessed",
                    "medium",
                    f"{th['plo_key']} is {', '.join(sorted({s['level'] for s in th['stages'] if s['level']}))}d "
                    f"({where}) but never reaches the 'assess' level.",
                    [th["plo_id"]],
                )
            )
    for p in pairs:
        if p["label"] == "missing" and p["via"] == "formal_prerequisite":
            out.append(
                _gap(
                    "course_pair_no_link",
                    "low",
                    f"{p['source_key']} is a prerequisite for {p['target_key']}, but no learning outcome or "
                    "assessment is linked across them.",
                    [p["source_course"], p["target_course"]],
                    source=p["source_course"],
                    target=p["target_course"],
                )
            )
    return out


def alignment_index(snap: Snapshot, pathway: str | None = None) -> dict[str, Any]:
    """Everything the Web view and the engine need. Memoised on the snapshot (cleared on any mutation)."""
    ck = ("alignment", pathway)
    if ck in snap.meta:
        return snap.meta[ck]
    idx = _Idx(snap)
    strengths = {}
    for k, r in snap.relationships.items():
        st = edge_strength(snap, r, idx)
        if st is not None:
            strengths[k] = st
    threads = plo_threads(snap, strengths, idx)
    pairs = course_pairs(snap, strengths)
    gs = gaps(snap, threads, pairs, idx)
    nodes = node_metrics(snap, strengths, threads, gs, idx)
    res = {
        "version": ALIGNMENT_VERSION,
        "edges": strengths,
        "nodes": nodes,
        "threads": threads,
        "course_pairs": pairs,
        "gaps": gs,
        "scorecard": scorecard(snap, strengths, threads, pairs, gs, pathway),
    }
    snap.meta[ck] = res
    return res


def node_metrics(
    snap: Snapshot, strengths: dict[str, dict], threads: list[dict], gs: list[dict], idx: _Idx
) -> dict[str, dict[str, Any]]:
    orphan_kinds = {"clo_no_plo", "assessment_no_clo", "topic_no_clo", "plo_no_clo"}
    orphans = {i for g in gs if g["kind"] in orphan_kinds for i in g["entity_ids"]}
    thread_by = {t["plo_id"]: t for t in threads}
    links_in: dict[str, int] = defaultdict(int)
    links_out: dict[str, int] = defaultdict(int)
    proposed: dict[str, int] = defaultdict(int)
    best_out: dict[str, float] = defaultdict(float)
    for k, st in strengths.items():
        r = snap.relationships[k]
        if not _active(r):
            continue
        links_out[r["source"]] += 1
        links_in[r["target"]] += 1
        if r["review_state"] == "proposed":
            proposed[r["source"]] += 1
            proposed[r["target"]] += 1
        if r["type"] in VERTICAL:
            best_out[r["source"]] = max(best_out[r["source"]], st["effective"])
        elif r["type"] == "prepares_for" and snap.entities.get(r["source"], {}).get("type") == "topic":
            best_out[r["source"]] = max(best_out[r["source"]], st["effective"])
    out: dict[str, dict[str, Any]] = {}
    for eid, e in snap.entities.items():
        t = e["type"]
        m: dict[str, Any] = {
            "links_in": links_in[eid],
            "links_out": links_out[eid],
            "proposed_links": proposed[eid],
            "orphan": eid in orphans,
        }
        if t in ("course_outcome", "assessment", "topic"):
            score = best_out.get(eid, 0.0)
            m.update(score=round(score, 3), label=label_for(score) if score else "missing")
            if t == "course_outcome":
                m.update(assessed=bool(idx.assessed.get(eid)), topic_support=len(idx.supported.get(eid, [])))
        elif t == "program_outcome":
            th = thread_by.get(eid)
            m.update(
                score=th["strength"] if th else None,
                label=th["label"] if th else "missing",
                thread_status=th["status"] if th else "uncovered",
                stages=th["firsts"] if th else {lv: None for lv in STAGES},
            )
        out[eid] = m
    for c in snap.courses():
        clos = [r["target"] for r in snap.rels("has_outcome", source=c["id"]) if _active(r) and r["target"] in out]
        scores = [out[x].get("score") or 0.0 for x in clos]
        score = round(sum(scores) / len(scores), 3) if scores else None
        out[c["id"]].update(score=score, label=label_for(score) if score is not None else "missing")
    return out


def scorecard(
    snap: Snapshot,
    strengths: dict[str, dict],
    threads: list[dict],
    pairs: list[dict],
    gs: list[dict],
    pathway: str | None = None,
) -> dict[str, Any]:
    cov = outcome_matrix(snap, pathway)["coverage"]
    active = [(snap.relationships[k], st) for k, st in strengths.items() if _active(snap.relationships[k])]
    links = {"strong": 0, "moderate": 0, "weak": 0, "proposed": 0}
    for r, st in active:
        if st["label"] in links:
            links[st["label"]] += 1
        if r["review_state"] == "proposed":
            links["proposed"] += 1
    gap_counts: dict[str, int] = defaultdict(int)
    for g in gs:
        gap_counts[g["kind"]] += 1
    return {
        "plo_documented_coverage": cov["value"],
        "plo_assessed_coverage": cov["assessed_value"],
        "threads_complete": {"n": sum(1 for t in threads if t["status"] == "complete"), "of": len(threads)},
        "avg_alignment_strength": round(sum(st["effective"] for _, st in active) / len(active), 3) if active else None,
        "links": links,
        "gaps": dict(sorted(gap_counts.items())),
        "broken_chains": sum(1 for p in pairs if p["label"] == "missing" and p["via"] == "formal_prerequisite"),
        "weak_chains": sum(1 for p in pairs if p["label"] == "weak"),
        "orphans": {
            "clo": gap_counts.get("clo_no_plo", 0),
            "assessment": gap_counts.get("assessment_no_clo", 0),
            "topic": gap_counts.get("topic_no_clo", 0),
        },
    }


def scorecard_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Numeric differences (after − before), recursively for nested counts."""

    def d(a: Any, b: Any) -> Any:
        if isinstance(a, dict) and isinstance(b, dict):
            return {k: d(a.get(k), b.get(k)) for k in sorted(set(a) | set(b))}
        if isinstance(a, (int, float)) or isinstance(b, (int, float)):
            if a is None or b is None:
                return None
            return round((b or 0) - (a or 0), 3)
        return None

    return d(before, after)


def compare_edges(base: dict[str, Any], proj: dict[str, Any]) -> dict[str, str]:
    """Relationship key → strengthened / weakened for links present in both, by effective strength."""
    out = {}
    for k, b in base["edges"].items():
        a = proj["edges"].get(k)
        if a is None:
            continue
        diff = a["effective"] - b["effective"]
        if abs(diff) >= DELTA or a["label"] != b["label"]:
            if diff > 0 or (diff == 0 and _rank(a["label"]) > _rank(b["label"])):
                out[k] = "strengthened"
            elif diff < 0 or _rank(a["label"]) < _rank(b["label"]):
                out[k] = "weakened"
    return out


def _rank(label: str | None) -> int:
    return {"missing": 0, "weak": 1, "moderate": 2, "strong": 3}.get(label or "missing", 0)
