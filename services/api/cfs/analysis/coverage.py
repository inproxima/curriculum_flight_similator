"""Outcome coverage and assessment alignment (spec §13). Deterministic; no model involvement.

Cell states for course × program-outcome:
  documented          explicit, accepted contribution in an assigned source
  inferred            only interpretation-based or unreviewed contributions
  none_found          the course has documented outcomes, but none map to this program outcome
  insufficient        the course has no documented outcomes, so absence cannot be judged
  explicitly_absent   a source explicitly states no contribution (only when such a statement exists)

Introduce/reinforce/assess levels are shown only where a contribution states them.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from cfs.graph.queries import exposure, owner_courses
from cfs.graph.snapshot import Snapshot, time_index

LEVEL_ORDER = {"introduce": 0, "reinforce": 1, "assess": 2}


def _documented(r: dict[str, Any]) -> bool:
    return r["basis"] == "explicit_statement" and r["review_state"] == "accepted"


def _active(r: dict[str, Any]) -> bool:
    return r["review_state"] not in ("rejected", "superseded")


def _course_sort(snap: Snapshot):
    def key(c: dict[str, Any]):
        t = time_index(snap.primary_placement(c["id"]))
        return (t if t is not None else 999, c["key"])

    return key


def outcome_matrix(snap: Snapshot, pathway: str | None = None) -> dict[str, Any]:
    plos = sorted((e for e in snap.entities.values() if e["type"] == "program_outcome"), key=lambda e: e["key"])
    courses = sorted(snap.courses(), key=_course_sort(snap))
    outcomes_of: dict[str, list[str]] = defaultdict(list)
    for r in snap.rels("has_outcome"):
        if _active(r):
            outcomes_of[r["source"]].append(r["target"])
    assessed: set[str] = {r["target"] for r in snap.rels("assesses") if _documented(r)}

    rows = []
    col: dict[str, dict[str, Any]] = {
        p["id"]: {
            "documented_courses": [],
            "inferred_courses": [],
            "levels": set(),
            "assessed": False,
            "required_path": False,
        }
        for p in plos
    }
    for c in courses:
        cos = outcomes_of.get(c["id"], [])
        ex = exposure(snap, c["id"], pathway)
        cells = {}
        for p in plos:
            contribs = [r for o in cos for r in snap.rels("contributes_to", source=o, target=p["id"]) if _active(r)]
            # course-level alignment (used when a source documents no course outcomes)
            contribs += [r for r in snap.rels("contributes_to", source=c["id"], target=p["id"]) if _active(r)]
            doc = [r for r in contribs if _documented(r)]
            if doc:
                state = "documented"
            elif contribs:
                state = "inferred"
            elif cos:
                state = "none_found"
            else:
                state = "insufficient"
            levels = sorted({r["level"] for r in (doc or contribs) if r["level"]}, key=LEVEL_ORDER.get)
            is_assessed = any(r["source"] in assessed for r in doc)
            cells[p["id"]] = {
                "state": state,
                "levels": levels,
                "assessed": is_assessed,
                "relationship_keys": [r["key"] for r in contribs],
            }
            if state == "documented":
                col[p["id"]]["documented_courses"].append(c["key"])
                col[p["id"]]["levels"].update(levels)
                col[p["id"]]["assessed"] |= is_assessed
                col[p["id"]]["required_path"] |= ex == "required_path"
            elif state == "inferred":
                col[p["id"]]["inferred_courses"].append(c["key"])
        pl = snap.primary_placement(c["id"])
        rows.append(
            {
                "course": {"id": c["id"], "key": c["key"], "title": c["title"]},
                "year": pl["year"] if pl else None,
                "term": pl["term"] if pl else None,
                "exposure": ex,
                "has_documented_outcomes": bool(cos),
                "cells": cells,
            }
        )

    columns = []
    for p in plos:
        cc = col[p["id"]]
        columns.append(
            {
                "plo": {
                    "id": p["id"],
                    "key": p["key"],
                    "title": p["title"],
                    "statement": p["details"].get("statement_verbatim"),
                },
                "documented_courses": cc["documented_courses"],
                "inferred_courses": cc["inferred_courses"],
                "levels": sorted(cc["levels"], key=LEVEL_ORDER.get),
                "assessed": cc["assessed"],
                "required_path": cc["required_path"],
            }
        )
    covered = [c for c in columns if c["required_path"]]
    with_outcomes = sum(1 for r in rows if r["has_documented_outcomes"])
    return {
        "curriculum_version": snap.version,
        "pathway_assumption": pathway or "all students on the required path",
        "columns": columns,
        "rows": rows,
        "coverage": {
            "value": (len(covered) / len(columns)) if columns else None,
            "numerator": len(covered),
            "denominator": len(columns),
            "definition": "Program outcomes with at least one documented contribution from a course on the required "
            "path (under the pathway assumption), divided by all program outcomes.",
            "assessed_value": (sum(1 for c in columns if c["assessed"]) / len(columns)) if columns else None,
            "assessed_definition": "Program outcomes with a documented assessment aligned to a contributing course "
            "outcome, divided by all program outcomes.",
        },
        "documentation": {
            "courses_with_outcomes": with_outcomes,
            "courses_total": len(rows),
            "unknowns_treatment": "Courses without documented outcomes are 'insufficient documentation', not "
            "'no coverage'. They are excluded from neither numerator nor denominator "
            "and may understate coverage.",
        },
    }


def assessment_alignment(snap: Snapshot) -> dict[str, Any]:
    rows = []
    for c in sorted(snap.courses(), key=_course_sort(snap)):
        for r in snap.rels("has_assessment", source=c["id"]):
            a = snap.entities.get(r["target"])
            if not a:
                continue
            outs = []
            for ar in snap.rels("assesses", source=a["id"]):
                if not _active(ar) or ar["target"] not in snap.entities:
                    continue
                o = snap.entities[ar["target"]]
                plos = [
                    {"key": snap.entities[x["target"]]["key"], "level": x["level"], "documented": _documented(x)}
                    for x in snap.rels("contributes_to", source=o["id"])
                    if _active(x)
                ]
                outs.append(
                    {
                        "outcome": {"id": o["id"], "key": o["key"], "title": o["title"]},
                        "documented": _documented(ar),
                        "program_outcomes": plos,
                    }
                )
            pl = snap.primary_placement(c["id"])
            rows.append(
                {
                    "assessment": {"id": a["id"], "key": a["key"], "title": a["title"], **a["details"]},
                    "course": {"id": c["id"], "key": c["key"]},
                    "year": pl["year"] if pl else None,
                    "term": pl["term"] if pl else None,
                    "outcomes": outs,
                }
            )
    return {"curriculum_version": snap.version, "rows": rows}


# ────────────────────────────── issue detection ──────────────────────────────


def detect_issues(snap: Snapshot, pathway: str | None = None) -> list[dict[str, Any]]:
    """Potential issues for faculty review. Each carries rule_id, affected ids, paths, and assumptions."""
    from cfs.graph.queries import evaluate_rules

    issues: list[dict[str, Any]] = []
    matrix = outcome_matrix(snap, pathway)

    for col in matrix["columns"]:
        p = col["plo"]
        if not col["documented_courses"]:
            issues.append(
                _issue(
                    "plo_no_documented_coverage",
                    "medium",
                    "interpretation",
                    f"{p['key']} has no documented contributing course",
                    "No assigned source documents a course outcome contributing to this program outcome."
                    + (" Inferred contributions exist." if col["inferred_courses"] else ""),
                    [p["id"]],
                )
            )
            continue
        if not col["assessed"]:
            issues.append(
                _issue(
                    "plo_no_documented_assessment",
                    "medium",
                    "explicit_statement",
                    f"{p['key']} ({p['title'].split(': ', 1)[-1]}) has no documented assessment",
                    "Courses contribute to this outcome, but no imported assessment is documented as "
                    "assessing a contributing course outcome. Assessment documents may be missing.",
                    [p["id"]],
                    assumptions=["Only imported assessment documents were considered."],
                )
            )
        if len(col["documented_courses"]) == 1:
            issues.append(
                _issue(
                    "plo_single_opportunity",
                    "low",
                    "explicit_statement",
                    f"{p['key']} relies on a single learning opportunity ({col['documented_courses'][0]})",
                    "Only one course documents a contribution. Changes to that course directly affect "
                    "this program outcome.",
                    [p["id"]],
                )
            )
        if not col["required_path"]:
            issues.append(
                _issue(
                    "plo_elective_only",
                    "medium",
                    "explicit_statement",
                    f"{p['key']} is supported only by elective or pathway-specific courses",
                    "Not all students are guaranteed exposure under the selected pathway assumption.",
                    [p["id"]],
                )
            )

    for row in matrix["rows"]:
        if not row["has_documented_outcomes"]:
            issues.append(
                _issue(
                    "course_outcomes_undocumented",
                    "info",
                    "explicit_statement",
                    f"{row['course']['key']} has no documented learning outcomes",
                    "Its contribution to program outcomes cannot be established from the documents.",
                    [row["course"]["id"]],
                    consequence="missing_evidence",
                )
            )

    # Requisite satisfaction and sequencing
    for c in snap.courses():
        for ev in evaluate_rules(snap, c["id"], pathway):
            if ev["status"] == "unsatisfied":
                issues.append(
                    _issue(
                        "requisite_unsatisfiable",
                        "high",
                        ev["basis"],
                        f"{c['key']} {ev['kind']} cannot be satisfied: {ev['rendered']}",
                        "; ".join(ev["reasons"]),
                        [c["id"]],
                        rule_ids=[ev["rule_id"]],
                    )
                )
            elif ev["status"] == "conditional" and exposure(snap, c["id"], pathway) == "required_path":
                issues.append(
                    _issue(
                        "requisite_depends_on_elective",
                        "medium",
                        ev["basis"],
                        f"Required course {c['key']} depends on elective preparation: {ev['rendered']}",
                        "; ".join(ev["reasons"]) + ". Students must choose particular electives to be eligible.",
                        [c["id"]],
                        rule_ids=[ev["rule_id"]],
                    )
                )
            elif ev["status"] == "unknown":
                issues.append(
                    _issue(
                        "requisite_unknown",
                        "info",
                        ev["basis"],
                        f"{c['key']} {ev['kind']} cannot be evaluated: {ev['rendered']}",
                        "; ".join(ev["reasons"]),
                        [c["id"]],
                        rule_ids=[ev["rule_id"]],
                        consequence="missing_evidence",
                    )
                )

    # Advanced assessment before documented preparation
    for a in (e for e in snap.entities.values() if e["type"] == "assessment"):
        a_course = (owner_courses(snap, a["id"]) or [None])[0]
        if not a_course:
            continue
        ta = time_index(snap.primary_placement(a_course))
        for ar in snap.rels("assesses", source=a["id"]):
            for prep in snap.rels("prepares_for", target=ar["target"]):
                if not _active(prep) or prep["source"] not in snap.entities:
                    continue
                src = snap.entities[prep["source"]]
                providers = [c for c in owner_courses(snap, src["id"]) if c != a_course]
                earlier = [
                    c
                    for c in providers
                    if (t := time_index(snap.primary_placement(c))) is not None and ta is not None and t < ta
                ]
                own = a_course in owner_courses(snap, src["id"])
                if earlier or own:
                    continue
                later = [snap.entities[c]["key"] for c in providers]
                issues.append(
                    _issue(
                        "assessment_before_preparation",
                        "medium",
                        "explicit_statement" if prep["basis"] == "explicit_statement" else "interpretation",
                        f"{a['title']} ({snap.entities[a_course]['key']}) assesses learning prepared by "
                        f"'{src['title']}' before students encounter it",
                        f"'{src['title']}' is covered in {', '.join(later) or 'no course'}"
                        f"{' (not earlier than this course)' if later else ''}. "
                        f"Week {a['details'].get('timing_week') or '?'} assessment.",
                        [a["id"], ar["target"], src["id"], a_course],
                        paths=[[src["id"], ar["target"], a["id"]]],
                        edge_keys=[prep["key"], ar["key"]],
                        consequence="direct_documented"
                        if prep["basis"] == "explicit_statement" and prep["review_state"] == "accepted"
                        else "uncertain_inferred",
                    )
                )

    # Topic depending on elective exposure
    for prep in snap.rels("prepares_for"):
        if not _active(prep) or prep["source"] not in snap.entities or prep["target"] not in snap.entities:
            continue
        tgt_courses = (
            owner_courses(snap, prep["target"])
            if snap.entities[prep["target"]]["type"] != "course"
            else [prep["target"]]
        )
        for tc in tgt_courses:
            if exposure(snap, tc, pathway) != "required_path":
                continue
            providers = [c for c in owner_courses(snap, prep["source"]) if c != tc and timing_ok(snap, c, tc)]
            if providers and all(exposure(snap, c, pathway) != "required_path" for c in providers):
                issues.append(
                    _issue(
                        "topic_depends_on_elective",
                        "medium",
                        prep["basis"],
                        f"{snap.entities[tc]['key']} relies on '{snap.entities[prep['source']]['title']}', "
                        f"covered earlier only in electives",
                        f"Earlier exposure only via {', '.join(snap.entities[c]['key'] for c in providers)}. Elective "
                        "exposure is not universal preparation.",
                        [tc, prep["source"], *providers],
                        edge_keys=[prep["key"]],
                    )
                )

    # Repeated introductory material without documented progression (flag for review, not waste)
    for t in (e for e in snap.entities.values() if e["type"] == "topic"):
        cs = owner_courses(snap, t["id"])
        years = {snap.primary_placement(c)["year"] for c in cs if snap.primary_placement(c)}
        has_prog = any(
            r
            for r in snap.relationships.values()
            if _active(r) and r["type"] == "prepares_for" and t["id"] in (r["source"], r["target"])
        )
        if t["details"].get("level") == "introductory" and len(years) >= 2 and not has_prog:
            issues.append(
                _issue(
                    "repeated_introductory_material",
                    "info",
                    "interpretation",
                    f"Introductory topic '{t['title']}' recurs across years without documented progression",
                    f"Appears in {len(cs)} courses: "
                    + ", ".join(sorted(snap.entities[c]["key"] for c in cs))
                    + ". This may be deliberate "
                    "revisiting (spiral curriculum). Review whether later occurrences build on earlier ones.",
                    [t["id"], *cs],
                    consequence="judgment_needed",
                )
            )

    # Prerequisite cycles (co-requisites are a separate relation and are not cycles)
    import networkx as nx

    g = nx.DiGraph()
    for r in snap.rels("formal_prerequisite"):
        if _active(r):
            g.add_edge(r["source"], r["target"], key=r["key"])
    for cyc in sorted((sorted(c) for c in nx.simple_cycles(g)), key=lambda c: c)[:20]:
        issues.append(
            _issue(
                "prerequisite_cycle",
                "high",
                "explicit_statement",
                "Prerequisite cycle: " + " → ".join(snap.entities[c]["key"] for c in cyc),
                "These courses require each other. If intended as concurrent enrolment, record them as co-requisites.",
                list(cyc),
                paths=[cyc + [cyc[0]]],
            )
        )
    issues.sort(key=lambda i: (-SEV_RANK[i["severity"]], i["rule_id"], i["title"]))
    return issues


def timing_ok(snap: Snapshot, earlier: str, later: str) -> bool:
    a, b = time_index(snap.primary_placement(earlier)), time_index(snap.primary_placement(later))
    return a is not None and b is not None and a < b


SEV_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}


def _issue(
    rule_id: str,
    severity: str,
    basis: str,
    title: str,
    explanation: str,
    affected: list[str],
    *,
    assumptions: list[str] | None = None,
    paths: list[list[str]] | None = None,
    edge_keys: list[str] | None = None,
    rule_ids: list[str] | None = None,
    consequence: str | None = None,
) -> dict[str, Any]:
    if consequence is None:
        consequence = "direct_documented" if basis == "explicit_statement" else "uncertain_inferred"
    return {
        "rule_id": rule_id,
        "category": rule_id.split("_")[0],
        "severity": severity,
        "evidence_basis": basis
        if basis in ("explicit_statement", "interpretation", "assumption")
        else "interpretation",
        "consequence_class": consequence,
        "title": title,
        "explanation": explanation,
        "affected_entity_ids": list(dict.fromkeys(affected)),
        "assumptions": assumptions or [],
        "paths": paths or [],
        "edge_keys": edge_keys or [],
        "rule_ids": rule_ids or [],
        "suggested_actions": [],
    }
