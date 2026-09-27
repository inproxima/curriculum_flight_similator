"""Deterministic scenario impact analysis (spec §14–15).

Pipeline:
  1. Diff baseline vs projection.
  2. Seeds = changed/removed/added elements.
  3. Candidate impact via typed propagation (dependency, alignment, assessment edges only;
     overlap/co-requisite/membership never propagate).
  4. Evaluate actual consequences: requisite satisfaction (AND/OR/credits), alternative preparation,
     program-outcome coverage and assessment, sequencing, cycles, assessment timing.
  5. Classify each finding: direct_documented / indirect_potential / uncertain_inferred /
     judgment_needed / missing_evidence. A reachable node is not automatically broken.

Same inputs → same input hash → identical ordered findings.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from cfs.analysis import alignment as al
from cfs.analysis.coverage import detect_issues, outcome_matrix
from cfs.graph.queries import evaluate_rules, exposure, owner_courses
from cfs.graph.snapshot import Snapshot, time_index

# Bump on ANY change to engine or cfs.analysis.coverage rules: cached runs are keyed on this version.
ENGINE_VERSION = "engine-1.4.0"
SEV = {"info": 0, "low": 1, "medium": 2, "high": 3}
RULE_RANK = {"satisfied_for_all": 3, "conditional": 2, "unknown": 1, "unsatisfied": 0}

# Typed propagation: which relationship types carry impact from a changed element, and in which direction.
PROPAGATE_FORWARD = {"formal_prerequisite", "inferred_preparation", "prepares_for", "contributes_to", "supports"}
PROPAGATE_REVERSE = {"assesses"}  # an assessment depends on the outcome it assesses
EDGE_PRIORITY = {
    "formal_prerequisite": 0,
    "inferred_preparation": 0,
    "prepares_for": 0,
    "assesses": 1,
    "contributes_to": 2,
    "supports": 3,
}


def input_hash(base: Snapshot, changes: list[tuple[int, dict]], pathway: str | None) -> str:
    payload = {"engine": ENGINE_VERSION, "base": base.content_hash(), "changes": changes, "pathway": pathway}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _label(snap: Snapshot, other: Snapshot | None, eid: str) -> str:
    e = snap.entities.get(eid) or (other.entities.get(eid) if other else None)
    return e["key"] if e else eid[:8]


def _finding(
    rule_id: str,
    severity: str,
    consequence: str,
    basis: str,
    title: str,
    explanation: str,
    affected: list[str],
    **kw: Any,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "category": kw.pop("category", rule_id.split("_")[0]),
        "severity": severity,
        "consequence_class": consequence,
        "evidence_basis": basis,
        "title": title,
        "explanation": explanation,
        "affected_entity_ids": list(dict.fromkeys(affected)),
        "assumptions": kw.pop("assumptions", []),
        "suggested_actions": kw.pop("suggested_actions", []),
        "paths": kw.pop("paths", []),
        "edge_keys": kw.pop("edge_keys", []),
        "rule_ids": kw.pop("rule_ids", []),
    }


def _propagate(base: Snapshot, seeds: set[str], max_depth: int = 6) -> dict[str, dict[str, Any]]:
    """Candidate impact set over the *baseline* graph (where removed elements still exist).

    Returns node → {depth, path, edges, inferred} using typed edges only.
    """
    out_edges: dict[str, list[dict]] = defaultdict(list)
    # dependency links first, so a documented preparation path wins over a parallel alignment path
    for r in sorted(base.relationships.values(), key=lambda r: (EDGE_PRIORITY.get(r["type"], 9), r["key"])):
        if r["review_state"] in ("rejected", "superseded"):
            continue
        if r["type"] in PROPAGATE_FORWARD:
            out_edges[r["source"]].append({**r, "_to": r["target"]})
        elif r["type"] in PROPAGATE_REVERSE:
            out_edges[r["target"]].append({**r, "_to": r["source"]})
        elif r["type"] in ("has_outcome", "covers_topic", "has_assessment"):
            # removing a course removes what it contains; contained items then propagate
            out_edges[r["source"]].append({**r, "_to": r["target"], "_containment": True})
    reached: dict[str, dict[str, Any]] = {s: {"depth": 0, "path": [s], "edges": [], "inferred": False} for s in seeds}
    frontier = sorted(seeds)
    for depth in range(1, max_depth + 1):
        nxt = []
        for n in frontier:
            for e in out_edges.get(n, []):
                to = e["_to"]
                if to in reached:
                    continue
                inferred = reached[n]["inferred"] or (
                    not e.get("_containment")
                    and (e["basis"] != "explicit_statement" or e["review_state"] != "accepted")
                )
                reached[to] = {
                    "depth": depth,
                    "path": reached[n]["path"] + [to],
                    "edges": reached[n]["edges"]
                    + [{"key": e["key"], "type": e["type"], "basis": e["basis"], "review_state": e["review_state"]}],
                    "inferred": inferred,
                }
                nxt.append(to)
        frontier = sorted(nxt)
        if not frontier:
            break
    return reached


def analyze(
    base: Snapshot, proj: Snapshot, d: dict[str, Any], change_results: list[dict], pathway: str | None = None
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    ents = d["entities"]
    removed = {e for e, s in ents.items() if s == "removed"}
    added = {e for e, s in ents.items() if s == "added"}
    modified = {e for e, s in ents.items() if s == "modified"}
    removed_rels = {k for k, s in d["relationships"].items() if s == "removed"}

    for r in change_results:
        if not r["ok"]:
            findings.append(
                _finding(
                    "change_not_applicable",
                    "high",
                    "direct_documented",
                    "explicit_statement",
                    f"Change #{r['seq']} could not be applied",
                    r["error"],
                    [],
                    category="scenario",
                )
            )

    # ── 1. Requisite satisfaction for every course whose rule status changed ──
    for c in sorted(proj.courses(), key=lambda c: c["key"]):
        new = {e["kind"]: e for e in evaluate_rules(proj, c["id"], pathway)}
        old = {e["kind"]: e for e in evaluate_rules(base, c["id"], pathway)} if c["id"] in base.entities else {}
        for kind, ev in new.items():
            before = old.get(kind)
            refs_removed = [
                code
                for code in ev["course_codes"]
                if any(base.entities[x]["key"] == code for x in removed if x in base.entities)
            ]
            unchanged = (
                before
                and RULE_RANK[ev["status"]] >= RULE_RANK[before["status"]]
                and before["rendered"] == ev["rendered"]
            )
            if (unchanged and not refs_removed) or (before is None and ev["status"] == "satisfied_for_all"):
                continue
            if ev["status"] == "unsatisfied":
                findings.append(
                    _finding(
                        "requisite_broken",
                        "high",
                        "direct_documented" if ev["basis"] == "explicit_statement" else "uncertain_inferred",
                        ev["basis"],
                        f"{c['key']} {kind} can no longer be satisfied",
                        f"Rule {ev['rendered']}: " + "; ".join(ev["reasons"]),
                        [c["id"]],
                        rule_ids=[ev["rule_id"]],
                        suggested_actions=[
                            "Revise the requisite, provide an alternative preparation course, or keep the "
                            "prerequisite course."
                        ],
                        category="sequencing",
                    )
                )
            elif ev["status"] == "conditional" and exposure(proj, c["id"], pathway) == "required_path":
                findings.append(
                    _finding(
                        "requisite_now_elective_dependent",
                        "medium",
                        "direct_documented",
                        ev["basis"],
                        f"{c['key']} {kind} now depends on elective or pathway courses",
                        f"Rule {ev['rendered']}: " + "; ".join(ev["reasons"]),
                        [c["id"]],
                        rule_ids=[ev["rule_id"]],
                        category="sequencing",
                    )
                )
            elif ev["status"] == "unknown":
                findings.append(
                    _finding(
                        "requisite_undeterminable",
                        "low",
                        "missing_evidence",
                        ev["basis"],
                        f"{c['key']} {kind} cannot be evaluated after this change",
                        "; ".join(ev["reasons"]),
                        [c["id"]],
                        rule_ids=[ev["rule_id"]],
                        category="evidence",
                    )
                )
            elif refs_removed and ev["status"] == "satisfied_for_all":
                findings.append(
                    _finding(
                        "alternative_path_remains",
                        "info",
                        "direct_documented",
                        ev["basis"],
                        f"{c['key']} remains satisfiable through an alternative",
                        f"{', '.join(refs_removed)} was removed, but the rule {ev['rendered']} is still met: "
                        + "; ".join(ev["reasons"]),
                        [c["id"]],
                        rule_ids=[ev["rule_id"]],
                        category="sequencing",
                    )
                )

    # ── 2. Typed propagation from removed/modified elements ──
    seeds = removed | {e for e in modified if base.entities.get(e, {}).get("type") != "course"}
    moved = {
        e
        for e in modified
        if base.entities.get(e, {}).get("type") == "course" and base.placements.get(e) != proj.placements.get(e)
    }
    for k in removed_rels:
        r = base.relationships[k]
        if r["type"] in ("prepares_for", "inferred_preparation", "formal_prerequisite"):
            seeds.add(r["source"])
        elif r["type"] in ("covers_topic", "has_outcome") and r["target"] in proj.entities:
            seeds.add(r["target"])  # the topic/outcome still exists but lost a learning opportunity
    reached = _propagate(base, seeds)
    impacted_courses: dict[str, dict[str, Any]] = {}
    for nid, info in sorted(reached.items(), key=lambda kv: (kv[1]["depth"], kv[0])):
        if info["depth"] == 0 or nid in removed:
            continue
        e = base.entities.get(nid)
        if not e:
            continue
        via_types = {x["type"] for x in info["edges"]}
        if not via_types & {
            "prepares_for",
            "inferred_preparation",
            "formal_prerequisite",
            "assesses",
            "contributes_to",
        }:
            continue  # reached only through containment of a removed item; handled as removal itself
        if e["type"] == "course":
            impacted_courses.setdefault(nid, info)
        elif e["type"] == "assessment" and "assesses" in via_types:
            still = [r for r in proj.rels("assesses", source=nid)]
            if not still and nid in proj.entities:
                findings.append(
                    _finding(
                        "assessment_loses_target",
                        "medium",
                        "direct_documented",
                        "explicit_statement",
                        f"Assessment '{e['title']}' no longer assesses any documented outcome",
                        "The outcome(s) it assessed were removed or changed in this scenario.",
                        [nid, *info["path"]],
                        paths=[info["path"]],
                        edge_keys=[x["key"] for x in info["edges"]],
                        suggested_actions=["Re-align the assessment to a remaining outcome, or revise it."],
                        category="assessment",
                    )
                )
        elif e["type"] in ("course_outcome", "topic") and "prepares_for" in via_types:
            owners = owner_courses(base, nid)
            alt = _alternative_exposure(proj, info["path"][0], owners, pathway)
            cls = (
                "uncertain_inferred"
                if info["inferred"]
                else ("indirect_potential" if not alt else "indirect_potential")
            )
            findings.append(
                _finding(
                    "preparation_weakened",
                    "low" if alt else ("medium" if not info["inferred"] else "low"),
                    cls,
                    "interpretation" if info["inferred"] else "explicit_statement",
                    f"Preparation for '{e['title']}' "
                    f"({', '.join(sorted(base.entities[o]['key'] for o in owners)) or 'no course'}) "
                    f"may be affected",
                    (
                        "Alternative earlier exposure remains: " + ", ".join(alt) + "."
                        if alt
                        else "No alternative earlier exposure to the changed preparation was found."
                    )
                    + (" This path relies on inferred relationships." if info["inferred"] else ""),
                    [nid, *owners, *info["path"]],
                    paths=[info["path"]],
                    edge_keys=[x["key"] for x in info["edges"]],
                    suggested_actions=[]
                    if alt
                    else ["Identify where the removed preparation could be provided instead."],
                    category="preparation",
                )
            )

    for cid, info in sorted(impacted_courses.items(), key=lambda kv: base.entities[kv[0]]["key"]):
        if cid not in proj.entities:
            continue
        rule_types = {x["type"] for x in info["edges"]}
        if rule_types <= {"formal_prerequisite"}:
            continue  # handled by requisite evaluation above (reachable ≠ broken)
        findings.append(
            _finding(
                "downstream_course_affected",
                "low",
                "uncertain_inferred" if info["inferred"] else "indirect_potential",
                "interpretation" if info["inferred"] else "explicit_statement",
                f"{base.entities[cid]['key']} is downstream of the change",
                "Reached through " + " → ".join(sorted(rule_types)) + ". Faculty review is needed to judge whether its "
                "expected preparation is still adequate.",
                [cid, *info["path"]],
                paths=[info["path"]],
                edge_keys=[x["key"] for x in info["edges"]],
                category="preparation",
            )
        )

    # ── 3. Program-outcome coverage and assessment (baseline vs scenario) ──
    mb, mp = outcome_matrix(base, pathway), outcome_matrix(proj, pathway)
    cols_b = {c["plo"]["id"]: c for c in mb["columns"]}
    for c in mp["columns"]:
        b = cols_b.get(c["plo"]["id"])
        if not b:
            continue
        pid, key = c["plo"]["id"], c["plo"]["key"]
        lost = sorted(set(b["documented_courses"]) - set(c["documented_courses"]))
        gained = sorted(set(c["documented_courses"]) - set(b["documented_courses"]))
        if b["documented_courses"] and not c["documented_courses"]:
            findings.append(
                _finding(
                    "plo_coverage_lost",
                    "high",
                    "direct_documented",
                    "explicit_statement",
                    f"{key} loses all documented course contributions",
                    f"Previously supported by {', '.join(b['documented_courses'])}.",
                    [pid],
                    category="coverage",
                    suggested_actions=["Add or re-map an outcome that contributes to this program outcome."],
                )
            )
        elif lost:
            findings.append(
                _finding(
                    "plo_coverage_reduced",
                    "medium" if len(c["documented_courses"]) == 1 else "low",
                    "direct_documented",
                    "explicit_statement",
                    f"{key} loses documented contributions from {', '.join(lost)}",
                    f"Remaining: {', '.join(c['documented_courses'])}."
                    + (" It now relies on a single learning opportunity." if len(c["documented_courses"]) == 1 else ""),
                    [pid],
                    category="coverage",
                )
            )
        if b["assessed"] and not c["assessed"]:
            findings.append(
                _finding(
                    "plo_assessment_lost",
                    "high",
                    "direct_documented",
                    "explicit_statement",
                    f"{key} no longer has a documented assessment",
                    "No remaining assessment is documented as assessing an outcome that contributes "
                    "to this program outcome.",
                    [pid],
                    category="assessment",
                )
            )
        if b["required_path"] and not c["required_path"] and c["documented_courses"]:
            findings.append(
                _finding(
                    "plo_now_elective_only",
                    "medium",
                    "direct_documented",
                    "explicit_statement",
                    f"{key} is now supported only by elective or pathway courses",
                    "Not all students are guaranteed exposure.",
                    [pid],
                    category="coverage",
                )
            )
        if gained:
            findings.append(
                _finding(
                    "plo_coverage_added",
                    "info",
                    "judgment_needed",
                    "assumption",
                    f"{key} gains contributions from {', '.join(gained)}",
                    "Added by scenario assumptions; not yet documented in a source.",
                    [pid],
                    category="coverage",
                )
            )

    # ── 4. Sequencing / assessment timing / cycles: issues new or resolved relative to baseline ──
    ib = {(i["rule_id"], i["title"]) for i in detect_issues(base, pathway)}
    ip = detect_issues(proj, pathway)
    ipk = {(i["rule_id"], i["title"]) for i in ip}
    skip = {
        "requisite_unsatisfiable",
        "requisite_depends_on_elective",
        "requisite_unknown",
        "plo_no_documented_assessment",
        "plo_elective_only",
        "plo_no_documented_coverage",
    }  # covered by dedicated comparisons above
    for i in ip:
        if (i["rule_id"], i["title"]) not in ib and i["rule_id"] not in skip:
            findings.append({**i, "title": "New: " + i["title"], "category": i["category"]})
    for rule_id, title in sorted(ib - ipk):
        if rule_id not in skip:
            findings.append(
                _finding(
                    f"resolved_{rule_id}",
                    "info",
                    "judgment_needed",
                    "interpretation",
                    "Resolved: " + title,
                    "This baseline issue no longer appears in the scenario.",
                    [],
                    category="resolved",
                )
            )

    # Same-term prerequisite placements introduced by moves
    for cid in sorted(moved):
        for r in proj.rels("formal_prerequisite"):
            if cid not in (r["source"], r["target"]):
                continue
            ts, tt = time_index(proj.primary_placement(r["source"])), time_index(proj.primary_placement(r["target"]))
            if ts is not None and tt is not None and ts >= tt:
                findings.append(
                    _finding(
                        "sequence_violation",
                        "high",
                        "direct_documented",
                        r["basis"],
                        f"{proj.entities[r['source']]['key']} is now scheduled "
                        f"{'in the same term as' if ts == tt else 'after'} "
                        f"{proj.entities[r['target']]['key']}, which requires it",
                        "Formal prerequisite order is violated by the move.",
                        [r["source"], r["target"]],
                        paths=[[r["source"], r["target"]]],
                        edge_keys=[r["key"]],
                        category="sequencing",
                    )
                )
        if not any(
            cid in (r["source"], r["target"])
            for r in proj.rels("formal_prerequisite", "inferred_preparation", "prepares_for")
        ):
            findings.append(
                _finding(
                    "move_no_documented_dependencies",
                    "info",
                    "missing_evidence",
                    "explicit_statement",
                    f"Moving {proj.entities[cid]['key']} has no documented sequencing effect",
                    "No documented or inferred dependencies connect this course to others, so the "
                    "sequencing impact cannot be established from the documents.",
                    [cid],
                    category="evidence",
                )
            )

    # ── 5. Missing evidence where precise impact cannot be established ──
    for eid in sorted(seeds | added | moved):
        e = proj.entities.get(eid) or base.entities.get(eid)
        if not e:
            continue
        if e["type"] == "course":
            c_out = (
                proj.rels("has_outcome", source=eid) if eid in proj.entities else base.rels("has_outcome", source=eid)
            )
            if not c_out:
                findings.append(
                    _finding(
                        "impact_unquantifiable_no_outcomes",
                        "info",
                        "missing_evidence",
                        "explicit_statement",
                        f"{e['key']}: impact on program outcomes cannot be established",
                        "The course has no documented learning outcomes.",
                        [eid],
                        category="evidence",
                    )
                )
        if e["type"] in ("course_outcome", "topic") and eid in removed:
            deps = [r for r in base.relationships.values() if r["source"] == eid and r["type"] == "prepares_for"]
            if not deps:
                findings.append(
                    _finding(
                        "impact_unknown_no_dependencies",
                        "info",
                        "missing_evidence",
                        "explicit_statement",
                        f"No documented later learning depends on '{e['title']}'",
                        "Absence of documented dependencies does not prove none exist. Course outlines "
                        "for later courses may be needed to confirm.",
                        [eid],
                        category="evidence",
                    )
                )

    # ── 6. Workload (known / estimated / unquantified) and assessment congestion ──
    workload = workload_summary(base, proj)
    if workload["delta"]["estimated_typical"] or workload["scenario"]["assumption_count"]:
        findings.append(
            _finding(
                "workload_estimate",
                "info",
                "judgment_needed",
                "assumption",
                "Workload estimate changes under stated assumptions",
                f"Estimated typical hours change by {workload['delta']['estimated_typical']:+g} "
                f"(range {workload['delta']['estimated_low']:+g} to {workload['delta']['estimated_high']:+g}). "
                f"{workload['scenario']['unquantified']} components remain unquantified.",
                [],
                assumptions=["Hours come from user-entered assumptions, not documents."],
                category="workload",
            )
        )
    cong = congestion(proj, pathway)
    cong_b = {(c["year"], c["term"], c["week"]) for c in congestion(base, pathway)["weeks"]}
    for w in cong["weeks"]:
        if (w["year"], w["term"], w["week"]) not in cong_b:
            findings.append(
                _finding(
                    "assessment_congestion",
                    "medium",
                    "direct_documented",
                    "explicit_statement",
                    f"Assessment congestion: {len(w['assessments'])} major assessments in Y{w['year']} "
                    f"{w['term']} week {w['week']}",
                    ", ".join(a["title"] for a in w["assessments"]),
                    [a["id"] for a in w["assessments"]],
                    category="workload",
                    assumptions=[cong["rule"]],
                )
            )

    # ── 7. Alignment: strength changes, program-outcome threads, horizontal chains, ripple ──
    align_findings, ripple, scorecard = alignment_impact(base, proj, d, pathway)
    findings.extend(align_findings)

    # ── ordering & summary ──
    order = {
        "direct_documented": 0,
        "indirect_potential": 1,
        "uncertain_inferred": 2,
        "judgment_needed": 3,
        "missing_evidence": 4,
    }
    findings.sort(key=lambda f: (order[f["consequence_class"]], -SEV[f["severity"]], f["rule_id"], f["title"]))
    # de-duplicate identical titles
    seen, unique = set(), []
    for f in findings:
        k = (f["rule_id"], f["title"])
        if k not in seen:
            seen.add(k)
            unique.append(f)
    counts: dict[str, int] = defaultdict(int)
    for f in unique:
        counts[f["consequence_class"]] += 1
    return {
        "findings": unique,
        "summary": {
            "engine_version": ENGINE_VERSION,
            "changes": {
                "entities": {s: sum(1 for v in ents.values() if v == s) for s in ("added", "removed", "modified")},
                "relationships": {
                    s: sum(1 for v in d["relationships"].values() if v == s) for s in ("added", "removed", "modified")
                },
            },
            "ripple": ripple,
            "scorecard": scorecard,
            "by_class": dict(sorted(counts.items())),
            "coverage": {"baseline": mb["coverage"], "scenario": mp["coverage"]},
            "workload": workload,
            "congestion": {"calculable": cong["calculable"], "note": cong["note"]},
            "pathway_assumption": pathway or "required path",
        },
    }


def _alternative_exposure(proj: Snapshot, changed: str, owners: list[str], pathway: str | None) -> list[str]:
    """Other earlier required-path courses that still cover the changed topic/outcome, if it still exists."""
    if changed not in proj.entities or proj.entities[changed]["type"] != "topic":
        return []
    out = []
    target_t = min((time_index(proj.primary_placement(o)) or 999) for o in owners) if owners else 999
    for c in owner_courses(proj, changed):
        t = time_index(proj.primary_placement(c))
        if t is not None and t < target_t and exposure(proj, c, pathway) == "required_path":
            out.append(proj.entities[c]["key"])
    return sorted(out)


WORK_COMPONENTS = [
    "contact",
    "reading",
    "practice",
    "assignment_prep",
    "assessment",
    "faculty_prep",
    "marking",
    "development",
]


def _workload(snap: Snapshot) -> dict[str, Any]:
    known = 0.0
    est = {"low": 0.0, "typical": 0.0, "high": 0.0}
    unq = 0
    assumptions = 0
    for e in snap.entities.values():
        if e["type"] not in ("course", "assessment"):
            continue
        wl = e["details"].get("workload_assumptions") or {}
        if e["type"] == "assessment" and e["details"].get("documented_hours") is not None:
            known += float(e["details"]["documented_hours"])
        for comp in WORK_COMPONENTS if e["type"] == "course" else ["assessment"]:
            if comp in wl:
                assumptions += 1
                for k in est:
                    est[k] += float(wl[comp][k])
            elif not (e["type"] == "assessment" and e["details"].get("documented_hours") is not None):
                unq += 1
    return {"known_hours": known, "estimated": est, "unquantified": unq, "assumption_count": assumptions}


def workload_summary(base: Snapshot, proj: Snapshot) -> dict[str, Any]:
    b, p = _workload(base), _workload(proj)
    return {
        "baseline": b,
        "scenario": p,
        "delta": {
            "known_hours": p["known_hours"] - b["known_hours"],
            "estimated_low": p["estimated"]["low"] - b["estimated"]["low"],
            "estimated_typical": p["estimated"]["typical"] - b["estimated"]["typical"],
            "estimated_high": p["estimated"]["high"] - b["estimated"]["high"],
        },
        "note": "Known = documented hours; estimated = user assumptions (low/typical/high); unquantified = "
        "components with neither. Overlapping topics are not summed as extra workload.",
    }


def congestion(
    snap: Snapshot, pathway: str | None = None, min_weight: float = 25.0, threshold: int = 2
) -> dict[str, Any]:
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    missing = 0
    for a in snap.entities.values():
        if a["type"] != "assessment":
            continue
        c = (owner_courses(snap, a["id"]) or [None])[0]
        pl = snap.primary_placement(c) if c else None
        w = a["details"].get("timing_week")
        if not pl or pl.get("year") is None or w is None:
            missing += 1
            continue
        if exposure(snap, c, pathway) != "required_path":
            continue
        if (a["details"].get("weight_percent") or 0) >= min_weight:
            buckets[(pl["year"], pl["term"], w)].append(
                {"id": a["id"], "title": a["title"], "course": snap.entities[c]["key"]}
            )
    weeks = [
        {"year": y, "term": t, "week": w, "assessments": sorted(v, key=lambda x: x["course"])}
        for (y, t, w), v in sorted(buckets.items())
        if len(v) >= threshold
    ]
    return {
        "weeks": weeks,
        "calculable": missing == 0,
        "rule": f"{threshold}+ required-course assessments weighted ≥{min_weight:g}% in the same week of the same term",
        "note": (
            "All assessments have documented weeks."
            if missing == 0
            else f"{missing} assessments lack documented weeks or placement; congestion for them cannot be calculated."
        ),
    }


# ─────────────────────────── alignment impact and ripple ───────────────────────────

# How a change travels outward, hop by hop (the "butterfly effect"). Typed, never "everything reachable":
#   assessment → the outcome it assesses · topic → the outcome it supports · outcome → program outcome
#   outcome / assessment → later outcome it prepares for · course → what it contains and courses that depend on it
#   a changed outcome also reaches the assessments that assess it (first hop only)
RIPPLE_FORWARD = {
    "assesses",
    "supports",
    "contributes_to",
    "prepares_for",
    "formal_prerequisite",
    "inferred_preparation",
    "has_outcome",
    "has_assessment",
    "covers_topic",
}
STATUS_ORDER = ["removed", "added", "broken", "weakened", "strengthened", "modified", "affected"]
IMPACT_RANK = {"broken": 0, "removed": 1, "weakened": 2, "added": 3, "strengthened": 4, "modified": 5, "affected": 6}
THREAD_RANK = {
    "complete": 0,
    "out_of_order": 1,
    "levels_unstated": 1,
    "incomplete": 2,
    "never_assessed": 3,
    "uncovered": 4,
}
IMPACT_SEVERITY = {
    "broken": "high",
    "removed": "medium",
    "weakened": "medium",
    "added": "info",
    "strengthened": "info",
    "modified": "low",
    "affected": "info",
}


def _union(base: Snapshot, proj: Snapshot) -> tuple[dict[str, dict], dict[str, dict]]:
    ents = {**base.entities, **proj.entities}
    rels = {**base.relationships, **proj.relationships}
    return ents, rels


def ripple_walk(base: Snapshot, proj: Snapshot, seeds: set[str], max_depth: int = 6) -> dict[str, dict[str, Any]]:
    """BFS over the union of baseline and scenario links, so both removals and additions travel."""
    _ents, rels = _union(base, proj)
    fwd: dict[str, list[dict]] = defaultdict(list)
    rev: dict[str, list[dict]] = defaultdict(list)
    for r in sorted(rels.values(), key=lambda r: (EDGE_PRIORITY.get(r["type"], 9), r["key"])):
        if r["review_state"] in ("rejected", "superseded"):
            continue
        if r["type"] in RIPPLE_FORWARD:
            fwd[r["source"]].append(r)
        if r["type"] == "assesses":
            rev[r["target"]].append(r)
    reached = {s: {"hop": 0, "path": [s], "edges": []} for s in seeds}
    frontier = sorted(seeds)
    for hop in range(1, max_depth + 1):
        nxt = []
        for n in frontier:
            steps = [(r, r["target"]) for r in fwd.get(n, [])]
            if hop == 1:
                steps += [(r, r["source"]) for r in rev.get(n, [])]
            for r, to in steps:
                if to in reached:
                    continue
                reached[to] = {"hop": hop, "path": reached[n]["path"] + [to], "edges": reached[n]["edges"] + [r["key"]]}
                nxt.append(to)
        frontier = sorted(nxt)
        if not frontier:
            break
    return reached


def alignment_impact(
    base: Snapshot, proj: Snapshot, d: dict[str, Any], pathway: str | None
) -> tuple[list[dict], dict[str, Any], dict[str, Any]]:
    bi, pi = al.alignment_index(base, pathway), al.alignment_index(proj, pathway)
    ents, rels = _union(base, proj)
    findings: list[dict[str, Any]] = []
    ent_st, rel_st = d["entities"], dict(d["relationships"])
    for k, st in al.compare_edges(bi, pi).items():
        if rel_st.get(k) in (None, "modified"):
            rel_st[k] = st

    def key(eid: str) -> str:
        return (ents.get(eid) or {}).get("key", eid[:8])

    # seeds are the edits themselves: changed elements, plus the source of each edited link whose endpoints were not
    # themselves added or removed. Links that merely got stronger or weaker are consequences, not causes.
    seeds = set(ent_st)
    for k in d["relationships"]:
        r = rels.get(k)
        if (
            r is None
            or ent_st.get(r["source"]) in ("added", "removed")
            or ent_st.get(r["target"]) in ("added", "removed")
        ):
            continue
        seeds.add(r["source"])
    reached = ripple_walk(base, proj, seeds)

    # node statuses
    b_threads = {t["plo_id"]: t for t in bi["threads"]}
    p_threads = {t["plo_id"]: t for t in pi["threads"]}
    impacts: dict[str, dict[str, Any]] = {}
    for eid in sorted(set(reached) | set(ent_st) | set(bi["nodes"]) & set(pi["nodes"])):
        e = ents.get(eid)
        if not e:
            continue
        bm, pm = bi["nodes"].get(eid, {}), pi["nodes"].get(eid, {})
        b, a = bm.get("score"), pm.get("score")
        status = ent_st.get(eid)
        if status is None and bm and pm:
            if bm.get("label") not in (None, "missing") and pm.get("label") == "missing":
                status = "broken"
            elif (
                e["type"] == "program_outcome"
                and eid in b_threads
                and eid in p_threads
                and (
                    THREAD_RANK[p_threads[eid]["status"]] > THREAD_RANK[b_threads[eid]["status"]]
                    and p_threads[eid]["status"] in ("never_assessed", "uncovered")
                )
            ):
                status = "broken"
            elif b is not None and a is not None and abs(a - b) >= al.DELTA:
                status = "strengthened" if a > b else "weakened"
        if status is None:
            if eid not in reached or reached[eid]["hop"] == 0:
                continue
            status = "affected"
        info = reached.get(eid, {"hop": 1, "path": [eid], "edges": []})
        impacts[eid] = {
            "entity_id": eid,
            "key": e["key"],
            "title": e["title"],
            "type": e["type"],
            "course_key": key(owner_courses(proj, eid)[0])
            if e["type"] not in ("course", "program_outcome") and owner_courses(proj, eid)
            else (
                key(owner_courses(base, eid)[0])
                if e["type"] not in ("course", "program_outcome") and owner_courses(base, eid)
                else None
            ),
            "status": status,
            "hop": info["hop"],
            "path": info["path"],
            "edge_keys": info["edges"],
            "strength_before": b,
            "strength_after": a,
        }

    # explanations and ranking
    for imp in impacts.values():
        via = " → ".join(key(x) for x in imp["path"])
        delta = (
            f" ({imp['strength_before']:.2f} → {imp['strength_after']:.2f})"
            if imp["strength_before"] is not None
            and imp["strength_after"] is not None
            and imp["strength_before"] != imp["strength_after"]
            else ""
        )
        noun = {
            "program_outcome": "Program outcome",
            "course_outcome": "Outcome",
            "assessment": "Assessment",
            "topic": "Topic",
            "course": "Course",
        }.get(imp["type"], imp["type"])
        txt = f"{noun} {imp['key']} {imp['status']}{delta}"
        if imp["hop"] > 0 and len(imp["path"]) > 1:
            txt += f", reached in {imp['hop']} step{'s' if imp['hop'] > 1 else ''} via {via}"
        if imp["type"] == "program_outcome" and imp["entity_id"] in p_threads and imp["entity_id"] in b_threads:
            bt, pt = b_threads[imp["entity_id"]]["status"], p_threads[imp["entity_id"]]["status"]
            if bt != pt:
                txt += f"; thread {bt.replace('_', ' ')} → {pt.replace('_', ' ')}"
        imp["explanation"] = txt + "."
        imp["severity"] = IMPACT_SEVERITY[imp["status"]]
    ordered = sorted(
        impacts.values(),
        key=lambda i: (
            IMPACT_RANK[i["status"]],
            i["type"] != "program_outcome",
            i["hop"],
            -abs((i["strength_after"] or 0) - (i["strength_before"] or 0)),
            i["key"],
        ),
    )
    for n, imp in enumerate(ordered, start=1):
        imp["rank"] = n

    # edges by hop (an edge lights up when the later of its endpoints is reached)
    edge_hop: dict[str, int] = {}
    for k in rel_st:
        r = rels.get(k)
        if r is None:
            continue
        hs = [reached[x]["hop"] for x in (r["source"], r["target"]) if x in reached]
        edge_hop[k] = max(hs) if hs else 0
    for imp in impacts.values():
        for i, k in enumerate(imp["edge_keys"]):
            edge_hop.setdefault(k, i + 1)
    max_hop = max([i["hop"] for i in impacts.values()] + list(edge_hop.values()) + [0])
    steps = [
        {
            "hop": h,
            "node_ids": sorted(i["entity_id"] for i in impacts.values() if i["hop"] == h),
            "edge_keys": sorted(k for k, eh in edge_hop.items() if eh == h),
        }
        for h in range(0, max_hop + 1)
    ]
    ripple = {
        "max_hop": max_hop,
        "steps": steps,
        "impacts": ordered,
        "edge_status": dict(sorted(rel_st.items())),
    }

    # ── findings ──
    def path_for(eid: str) -> list[list[str]]:
        return [reached[eid]["path"]] if eid in reached and len(reached[eid]["path"]) > 1 else []

    for eid in sorted(ent_st):
        if ent_st[eid] == "removed" and base.entities.get(eid, {}).get("type") == "program_outcome":
            e = base.entities[eid]
            findings.append(
                _finding(
                    "plo_removed",
                    "high",
                    "direct_documented",
                    "explicit_statement",
                    f"Program outcome {e['title']} is removed",
                    "Every course outcome that contributed to it loses that alignment.",
                    [eid],
                    category="alignment",
                )
            )
    for plo_id, bt in b_threads.items():
        pt = p_threads.get(plo_id)
        if not pt or pt["status"] == bt["status"]:
            continue
        worse = THREAD_RANK[pt["status"]] > THREAD_RANK[bt["status"]]
        findings.append(
            _finding(
                "plo_thread_broken" if worse else "plo_thread_improved",
                ("high" if pt["status"] in ("uncovered", "never_assessed") else "medium") if worse else "info",
                "direct_documented" if worse else "indirect_potential",
                "explicit_statement",
                f"{bt['plo_key']} thread {'weakens' if worse else 'improves'}: {bt['status'].replace('_', ' ')} → "
                f"{pt['status'].replace('_', ' ')}",
                (f"Missing stages now: {', '.join(pt['missing']) or 'none'}. " if pt["missing"] else "")
                + f"Thread strength {bt['strength'] or 0:.2f} → {pt['strength'] or 0:.2f}.",
                [plo_id],
                paths=path_for(plo_id),
                category="alignment",
                suggested_actions=["Add or re-align a course outcome and an assessment at the missing stage."]
                if worse
                else [],
            )
        )
    b_pairs = {(p["source_course"], p["target_course"]): p for p in bi["course_pairs"]}
    for p in pi["course_pairs"]:
        bp = b_pairs.get((p["source_course"], p["target_course"]))
        if not bp or bp["label"] == p["label"]:
            continue
        rank = {"missing": 0, "weak": 1, "moderate": 2, "strong": 3}
        if rank[p["label"]] < rank[bp["label"]]:
            broken = p["label"] == "missing"
            findings.append(
                _finding(
                    "horizontal_chain_broken" if broken else "horizontal_chain_weakened",
                    "medium" if broken else "low",
                    "direct_documented" if broken else "indirect_potential",
                    "explicit_statement" if broken else "interpretation",
                    f"{p['source_key']} → {p['target_key']}: bridge {'lost' if broken else 'weakened'} "
                    f"({bp['label']} → {p['label']})",
                    "No remaining learning outcome or assessment in the earlier course is linked to the later one."
                    if broken
                    else f"Best outcome-level bridge {bp['strength'] or 0:.2f} → {p['strength'] or 0:.2f}.",
                    [p["source_course"], p["target_course"]],
                    edge_keys=sorted(set(bp["edge_keys"]) - set(p["edge_keys"])),
                    category="alignment",
                )
            )
    orphan_kinds = {"clo_no_plo", "assessment_no_clo", "topic_no_clo"}
    before_gaps = {g["id"] for g in bi["gaps"]}
    for g in pi["gaps"]:
        if g["kind"] == "clo_unassessed" and g["id"] not in before_gaps and g["entity_ids"][0] in base.entities:
            eid = g["entity_ids"][0]
            findings.append(
                _finding(
                    "outcome_no_longer_assessed",
                    "medium",
                    "direct_documented",
                    "explicit_statement",
                    f"{key(eid)} is no longer assessed",
                    "No remaining assessment is linked to this course outcome, so the program outcomes it contributes "
                    "to lose evidence from this course.",
                    [eid],
                    paths=path_for(eid),
                    category="alignment",
                    suggested_actions=["Link another assessment to this outcome, or add one."],
                )
            )
    for g in pi["gaps"]:
        if g["kind"] in orphan_kinds and g["id"] not in before_gaps:
            eid = g["entity_ids"][0]
            findings.append(
                _finding(
                    "orphan_created",
                    "medium",
                    "direct_documented",
                    "explicit_statement",
                    f"{g['message']} (new in this scenario)",
                    "Something this element was linked to was removed or changed.",
                    [eid],
                    paths=path_for(eid),
                    category="alignment",
                )
            )
    for k, st in sorted(rel_st.items()):
        if st not in ("strengthened", "weakened"):
            continue
        r = rels[k]
        if r["type"] not in ("contributes_to", "assesses"):
            continue
        b, a = bi["edges"].get(k), pi["edges"].get(k)
        if not b or not a or b["label"] == a["label"]:
            continue
        findings.append(
            _finding(
                f"alignment_{st}",
                "low" if st == "weakened" else "info",
                "indirect_potential",
                "interpretation",
                f"{key(r['source'])} → {key(r['target'])} {st}: {b['label']} → {a['label']}",
                f"Effective strength {b['effective']:.2f} → {a['effective']:.2f} (trust × intensity × support).",
                [r["source"], r["target"]],
                edge_keys=[k],
                category="alignment",
            )
        )

    scorecard = {
        "baseline": bi["scorecard"],
        "scenario": pi["scorecard"],
        "delta": al.scorecard_delta(bi["scorecard"], pi["scorecard"]),
    }
    return findings, ripple, scorecard
