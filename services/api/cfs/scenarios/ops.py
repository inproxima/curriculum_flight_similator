"""Typed scenario operations applied to an in-memory snapshot projection.

The baseline is never mutated: `project()` deep-copies the base snapshot and replays the applied
change stack in order. New entities get deterministic ids (uuid5 of scenario id + change seq) so replays,
comparisons, and saved views are stable.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from cfs.curriculum.rules import dump_expr, leaf_edges, parse_requisite_text
from cfs.graph.snapshot import Snapshot

TermT = Literal["fall", "winter", "spring", "summer", "full_year", "unknown"]
LevelT = Literal["introduce", "reinforce", "assess"]


class OpBase(BaseModel):
    assumptions: list[str] = Field(default_factory=list)
    note: str | None = None


class AddCourse(OpBase):
    op: Literal["add_course"] = "add_course"
    code: str
    title: str
    year: int | None = None
    term: TermT = "unknown"
    classification: Literal["required", "elective", "pathway_required", "optional", "unknown"] = "required"
    credits: float | None = None
    description: str | None = None
    prerequisite_text: str | None = None


class RemoveCourse(OpBase):
    op: Literal["remove_course"] = "remove_course"
    entity_id: str


class MoveCourse(OpBase):
    op: Literal["move_course"] = "move_course"
    entity_id: str
    year: int | None
    term: TermT


class SetClassification(OpBase):
    op: Literal["set_classification"] = "set_classification"
    entity_id: str
    classification: Literal["required", "elective", "pathway_required", "optional", "unknown"]


class AddOutcome(OpBase):
    op: Literal["add_outcome"] = "add_outcome"
    course_id: str
    statement: str
    contributes: list[tuple[str, LevelT]] = Field(default_factory=list)  # (program outcome id, level)


class RemoveOutcome(OpBase):
    op: Literal["remove_outcome"] = "remove_outcome"
    entity_id: str


class ModifyOutcome(OpBase):
    op: Literal["modify_outcome"] = "modify_outcome"
    entity_id: str
    statement: str | None = None
    contributes: list[tuple[str, LevelT]] | None = None


class AddTopic(OpBase):
    op: Literal["add_topic"] = "add_topic"
    course_id: str
    title: str
    existing_topic_id: str | None = None  # attach an existing topic instead of creating one
    area: str | None = None
    level: Literal["introductory", "intermediate", "advanced"] | None = None
    prepares: list[str] = Field(default_factory=list)  # entity ids this topic prepares for


class RemoveTopic(OpBase):
    op: Literal["remove_topic"] = "remove_topic"
    entity_id: str
    course_id: str | None = None  # remove only from this course; None = remove the topic entirely


class ChangeAssessment(OpBase):
    op: Literal["change_assessment"] = "change_assessment"
    entity_id: str
    timing_week: int | None = None
    weight_percent: float | None = None
    format: str | None = None
    shift_weeks: int | None = None  # relative move ("two weeks later" = 2)


class AddAssessment(OpBase):
    op: Literal["add_assessment"] = "add_assessment"
    course_id: str
    title: str
    format: str | None = None
    weight_percent: float | None = None
    timing_week: int | None = None
    assesses: list[str] = Field(default_factory=list)


class RemoveAssessment(OpBase):
    op: Literal["remove_assessment"] = "remove_assessment"
    entity_id: str


class ChangeDelivery(OpBase):
    op: Literal["change_delivery"] = "change_delivery"
    course_id: str
    delivery_format: str


class SetWorkload(OpBase):
    op: Literal["set_workload"] = "set_workload"
    entity_id: str  # course or assessment
    component: Literal[
        "contact", "reading", "practice", "assignment_prep", "assessment", "faculty_prep", "marking", "development"
    ]
    low: float
    typical: float
    high: float
    unit: Literal["hours_per_term"] = "hours_per_term"


class ModifyRequirement(OpBase):
    op: Literal["modify_requirement"] = "modify_requirement"
    course_id: str
    rule_kind: Literal["prerequisite", "corequisite", "antirequisite"] = "prerequisite"
    text: str | None = None  # None/"" removes the rule


class AddRelationship(OpBase):
    op: Literal["add_relationship"] = "add_relationship"
    source: str
    target: str
    rel_type: Literal[
        "inferred_preparation", "prepares_for", "possible_overlap", "contributes_to", "assesses", "covers_topic"
    ]
    level: LevelT | None = None
    rationale: str | None = None


class RemoveRelationship(OpBase):
    op: Literal["remove_relationship"] = "remove_relationship"
    key: str


Op = Annotated[
    Union[
        AddCourse,
        RemoveCourse,
        MoveCourse,
        SetClassification,
        AddOutcome,
        RemoveOutcome,
        ModifyOutcome,
        AddTopic,
        RemoveTopic,
        ChangeAssessment,
        AddAssessment,
        RemoveAssessment,
        ChangeDelivery,
        SetWorkload,
        ModifyRequirement,
        AddRelationship,
        RemoveRelationship,
    ],
    Field(discriminator="op"),
]
OpAdapter: TypeAdapter[Op] = TypeAdapter(Op)


class OpError(Exception):
    """Operation cannot be applied to this projection (nonexistent target, wrong type, conflict)."""


def parse_op(data: dict[str, Any]) -> Op:
    return OpAdapter.validate_python(data)


def _new_id(scenario_id: str, seq: int, suffix: str) -> str:
    return str(uuid.uuid5(uuid.UUID(scenario_id), f"{seq}:{suffix}"))


def _need(snap: Snapshot, eid: str, *types: str) -> dict[str, Any]:
    e = snap.entities.get(eid)
    if e is None:
        raise OpError(f"entity {eid} does not exist in this projection")
    if types and e["type"] not in types:
        raise OpError(f"entity {e['key']} is a {e['type']}, expected {'/'.join(types)}")
    return e


def _rel(
    snap: Snapshot,
    key: str,
    source: str,
    target: str,
    rtype: str,
    *,
    basis: str = "assumption",
    level: str | None = None,
    rationale: str | None = None,
    rule_id: str | None = None,
) -> None:
    if rtype == "possible_overlap" and source > target:
        source, target = target, source
    snap.relationships[key] = {
        "key": key,
        "revision_id": None,
        "source": source,
        "target": target,
        "type": rtype,
        "origin": "manual",
        "basis": basis,
        "review_state": "accepted",
        "level": level,
        "rule_id": rule_id,
        "rationale": rationale,
        "scope": None,
        "is_synthetic": False,
        "evidence": {"supporting": 0, "contradicting": 0},
        "scenario": True,
    }


def _entity(
    snap: Snapshot,
    eid: str,
    etype: str,
    key: str,
    title: str,
    details: dict | None = None,
    description: str | None = None,
) -> None:
    snap.entities[eid] = {
        "id": eid,
        "type": etype,
        "key": key,
        "revision_id": None,
        "revision_number": 0,
        "title": title,
        "description": description,
        "details": details or {},
        "origin": "manual",
        "is_synthetic": False,
        "field_evidence": {},
        "scenario": True,
    }


def _drop_entity(snap: Snapshot, eid: str) -> list[str]:
    """Remove entity, its placements, rules targeting it, and all relationships touching it."""
    removed = [eid]
    snap.entities.pop(eid, None)
    snap.placements.pop(eid, None)
    for k in [k for k, r in snap.relationships.items() if eid in (r["source"], r["target"])]:
        del snap.relationships[k]
    for rid in [rid for rid, r in snap.rules.items() if r["target"] == eid]:
        del snap.rules[rid]
    for g in snap.groups.values():
        if eid in g["members"]:
            g["members"] = [m for m in g["members"] if m != eid]
    return removed


def _key(snap: Snapshot, eid: str) -> str:
    return snap.entities.get(eid, {}).get("key", eid[:8])


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "item"


def _install_rule(snap: Snapshot, scenario_id: str, seq: int, course_id: str, kind: str, text: str) -> None:
    for rid in [rid for rid, r in snap.rules.items() if r["target"] == course_id and r["kind"] == kind]:
        del snap.rules[rid]
    rtype = "formal_prerequisite" if kind == "prerequisite" else "corequisite"
    for k in [k for k, r in snap.relationships.items() if r["target"] == course_id and r["type"] == rtype]:
        del snap.relationships[k]
    if not text:
        return
    expr = parse_requisite_text(text)
    rid = _new_id(scenario_id, seq, f"rule:{course_id}:{kind}")
    snap.rules[rid] = {
        "id": rid,
        "target": course_id,
        "kind": kind,
        "expression": dump_expr(expr),
        "source_text": text,
        "origin": "manual",
        "basis": "assumption",
        "review_state": "accepted",
        "evidence_span_id": None,
    }
    if kind in ("prerequisite", "corequisite"):
        by_code = {e["key"]: e["id"] for e in snap.entities.values() if e["type"] == "course"}
        for ref, _p, alt in leaf_edges(expr):
            if ref.code in by_code:
                _rel(
                    snap,
                    _new_id(scenario_id, seq, f"req:{ref.code}"),
                    by_code[ref.code],
                    course_id,
                    rtype,
                    rule_id=rid,
                    rationale="One of several alternatives" if alt else "Scenario requirement",
                )


def apply_op(snap: Snapshot, op: Op, scenario_id: str, seq: int) -> dict[str, Any]:
    """Apply in place (snap must already be a copy). Returns {before, after, summary, target}."""
    snap.invalidate()
    try:
        return _apply(snap, op, scenario_id, seq)
    finally:
        snap.invalidate()  # cached indexes must never outlive a mutation


def _apply(snap: Snapshot, op: Op, scenario_id: str, seq: int) -> dict[str, Any]:
    o = op
    if isinstance(o, AddCourse):
        code = re.sub(r"\s+", " ", o.code.strip().upper())
        if any(e["type"] == "course" and e["key"] == code for e in snap.entities.values()):
            raise OpError(f"course {code} already exists in this projection")
        eid = _new_id(scenario_id, seq, f"course:{code}")
        _entity(snap, eid, "course", code, o.title, {"course_code": code, "credits": o.credits}, o.description)
        snap.placements[eid] = [
            {
                "id": _new_id(scenario_id, seq, "placement"),
                "year": o.year,
                "term": o.term,
                "classification": o.classification,
                "pathway": None,
                "offering_conditions": None,
            }
        ]
        if o.prerequisite_text:
            _install_rule(snap, scenario_id, seq, eid, "prerequisite", o.prerequisite_text)
        return {
            "target": eid,
            "before": None,
            "after": {"code": code, "year": o.year, "term": o.term},
            "summary": f"Add course {code} ({o.title}) in year {o.year or '?'} {o.term}",
        }
    if isinstance(o, RemoveCourse):
        e = _need(snap, o.entity_id, "course")
        contained = [
            r["target"]
            for r in snap.relationships.values()
            if r["source"] == e["id"] and r["type"] in ("has_outcome", "has_assessment")
        ]
        before = {"key": e["key"], "placements": snap.placements.get(e["id"]), "contained": contained}
        for c in contained:
            _drop_entity(snap, c)
        _drop_entity(snap, e["id"])
        return {
            "target": e["id"],
            "before": before,
            "after": None,
            "summary": f"Remove course {e['key']} and its {len(contained)} outcomes/assessments",
        }
    if isinstance(o, MoveCourse):
        e = _need(snap, o.entity_id, "course")
        pls = snap.placements.get(e["id"]) or []
        if not pls:
            raise OpError(f"{e['key']} has no placement to move")
        before = {"year": pls[0]["year"], "term": pls[0]["term"]}
        for p in pls:
            p["year"], p["term"] = o.year, o.term
        return {
            "target": e["id"],
            "before": before,
            "after": {"year": o.year, "term": o.term},
            "summary": f"Move {e['key']} from Y{before['year']} {before['term']} to Y{o.year} {o.term}",
        }
    if isinstance(o, SetClassification):
        e = _need(snap, o.entity_id, "course")
        pls = snap.placements.get(e["id"]) or []
        if not pls:
            raise OpError(f"{e['key']} has no placement")
        before = {"classification": pls[0]["classification"]}
        for p in pls:
            p["classification"] = o.classification
        return {
            "target": e["id"],
            "before": before,
            "after": {"classification": o.classification},
            "summary": f"Make {e['key']} {o.classification.replace('_', ' ')}",
        }
    if isinstance(o, AddOutcome):
        c = _need(snap, o.course_id, "course")
        n = 1 + sum(1 for r in snap.relationships.values() if r["source"] == c["id"] and r["type"] == "has_outcome")
        eid = _new_id(scenario_id, seq, "outcome")
        key = f"{c['key'].replace(' ', '')}-CO{n}-new"
        _entity(
            snap,
            eid,
            "course_outcome",
            key,
            o.statement,
            {"scope": "course", "statement_verbatim": None, "label": o.statement[:300]},
        )
        _rel(snap, _new_id(scenario_id, seq, "has"), c["id"], eid, "has_outcome")
        for plo, lvl in o.contributes:
            _need(snap, plo, "program_outcome")
            _rel(snap, _new_id(scenario_id, seq, f"plo:{plo}"), eid, plo, "contributes_to", level=lvl)
        return {
            "target": eid,
            "before": None,
            "after": {"statement": o.statement, "course": c["key"]},
            "summary": f"Add outcome to {c['key']}: {o.statement[:80]}",
        }
    if isinstance(o, RemoveOutcome):
        e = _need(snap, o.entity_id, "course_outcome")
        before = {
            "key": e["key"],
            "statement": e["title"],
            "relationships": sorted(
                r["key"] for r in snap.relationships.values() if e["id"] in (r["source"], r["target"])
            ),
        }
        _drop_entity(snap, e["id"])
        return {"target": e["id"], "before": before, "after": None, "summary": f"Remove outcome {e['key']}"}
    if isinstance(o, ModifyOutcome):
        e = _need(snap, o.entity_id, "course_outcome")
        before = {
            "statement": e["title"],
            "contributes": sorted(
                [r["target"], r["level"]]
                for r in snap.relationships.values()
                if r["source"] == e["id"] and r["type"] == "contributes_to"
            ),
        }
        if o.statement:
            e["title"] = o.statement
            e["details"] = {**e["details"], "label": o.statement[:300], "statement_modified_in_scenario": True}
            e["modified"] = True
        if o.contributes is not None:
            for k in [
                k for k, r in snap.relationships.items() if r["source"] == e["id"] and r["type"] == "contributes_to"
            ]:
                del snap.relationships[k]
            for plo, lvl in o.contributes:
                _need(snap, plo, "program_outcome")
                _rel(snap, _new_id(scenario_id, seq, f"plo:{plo}"), e["id"], plo, "contributes_to", level=lvl)
        return {
            "target": e["id"],
            "before": before,
            "after": {"statement": e["title"], "contributes": o.contributes},
            "summary": f"Modify outcome {e['key']}",
        }
    if isinstance(o, AddTopic):
        c = _need(snap, o.course_id, "course")
        if o.existing_topic_id:
            t = _need(snap, o.existing_topic_id, "topic")
            eid = t["id"]
        else:
            eid = _new_id(scenario_id, seq, "topic")
            _entity(snap, eid, "topic", _slug(o.title) + "-new", o.title, {"area": o.area, "level": o.level})
        _rel(snap, _new_id(scenario_id, seq, "covers"), c["id"], eid, "covers_topic")
        for tgt in o.prepares:
            _need(snap, tgt)
            _rel(
                snap,
                _new_id(scenario_id, seq, f"prep:{tgt}"),
                eid,
                tgt,
                "prepares_for",
                rationale="Scenario assumption",
            )
        return {
            "target": eid,
            "before": None,
            "after": {"title": o.title, "course": c["key"]},
            "summary": f"Add topic '{o.title}' to {c['key']}",
        }
    if isinstance(o, RemoveTopic):
        t = _need(snap, o.entity_id, "topic")
        if o.course_id:
            c = _need(snap, o.course_id, "course")
            ks = [
                k
                for k, r in snap.relationships.items()
                if r["type"] == "covers_topic" and r["source"] == c["id"] and r["target"] == t["id"]
            ]
            if not ks:
                raise OpError(f"{c['key']} does not cover topic {t['title']}")
            for k in ks:
                del snap.relationships[k]
            return {
                "target": t["id"],
                "before": {"course": c["key"], "topic": t["title"]},
                "after": None,
                "summary": f"Remove topic '{t['title']}' from {c['key']}",
            }
        before = {"topic": t["title"]}
        _drop_entity(snap, t["id"])
        return {"target": t["id"], "before": before, "after": None, "summary": f"Remove topic '{t['title']}' entirely"}
    if isinstance(o, ChangeAssessment):
        a = _need(snap, o.entity_id, "assessment")
        d = a["details"]
        before = {k: d.get(k) for k in ("timing_week", "weight_percent", "format")}
        if o.shift_weeks is not None:
            if d.get("timing_week") is None:
                raise OpError(f"{a['title']} has no documented week, so a relative move is ambiguous; set timing_week")
            d["timing_week"] = d["timing_week"] + o.shift_weeks
        if o.timing_week is not None:
            d["timing_week"] = o.timing_week
        if o.weight_percent is not None:
            d["weight_percent"] = o.weight_percent
        if o.format is not None:
            d["format"] = o.format
        a["modified"] = True
        after = {k: d.get(k) for k in ("timing_week", "weight_percent", "format")}
        return {
            "target": a["id"],
            "before": before,
            "after": after,
            "summary": f"Change assessment {a['title']}: "
            + ", ".join(f"{k} {before[k]}→{after[k]}" for k in after if before[k] != after[k]),
        }
    if isinstance(o, AddAssessment):
        c = _need(snap, o.course_id, "course")
        eid = _new_id(scenario_id, seq, "assessment")
        _entity(
            snap,
            eid,
            "assessment",
            f"{c['key'].replace(' ', '')}-A-new{seq}",
            o.title,
            {"format": o.format, "weight_percent": o.weight_percent, "timing_week": o.timing_week},
        )
        _rel(snap, _new_id(scenario_id, seq, "has"), c["id"], eid, "has_assessment")
        for out in o.assesses:
            _need(snap, out, "course_outcome")
            _rel(snap, _new_id(scenario_id, seq, f"assesses:{out}"), eid, out, "assesses")
        return {
            "target": eid,
            "before": None,
            "after": {"title": o.title},
            "summary": f"Add assessment {o.title} to {c['key']}",
        }
    if isinstance(o, RemoveAssessment):
        a = _need(snap, o.entity_id, "assessment")
        _drop_entity(snap, a["id"])
        return {
            "target": a["id"],
            "before": {"title": a["title"]},
            "after": None,
            "summary": f"Remove assessment {a['title']}",
        }
    if isinstance(o, ChangeDelivery):
        c = _need(snap, o.course_id, "course")
        before = {"delivery_format": c["details"].get("delivery_format")}
        c["details"]["delivery_format"] = o.delivery_format
        c["modified"] = True
        return {
            "target": c["id"],
            "before": before,
            "after": {"delivery_format": o.delivery_format},
            "summary": f"Deliver {c['key']} as {o.delivery_format}",
        }
    if isinstance(o, SetWorkload):
        e = _need(snap, o.entity_id, "course", "assessment")
        wl = e["details"].setdefault("workload_assumptions", {})
        before = wl.get(o.component)
        wl[o.component] = {"low": o.low, "typical": o.typical, "high": o.high, "unit": o.unit, "basis": "assumption"}
        return {
            "target": e["id"],
            "before": {"component": o.component, "value": before},
            "after": {"component": o.component, "value": wl[o.component]},
            "summary": f"Workload assumption for {e['key']} ({o.component}): {o.low}–{o.high} h (typical {o.typical})",
        }
    if isinstance(o, ModifyRequirement):
        c = _need(snap, o.course_id, "course")
        before = [r["source_text"] for r in snap.rules.values() if r["target"] == c["id"] and r["kind"] == o.rule_kind]
        _install_rule(snap, scenario_id, seq, c["id"], o.rule_kind, (o.text or "").strip())
        return {
            "target": c["id"],
            "before": {"text": before[0] if before else None},
            "after": {"text": o.text},
            "summary": f"{'Set' if o.text else 'Remove'} {o.rule_kind} for {c['key']}"
            + (f": {o.text}" if o.text else ""),
        }
    if isinstance(o, AddRelationship):
        _need(snap, o.source)
        _need(snap, o.target)
        key = _new_id(scenario_id, seq, "rel")
        _rel(
            snap,
            key,
            o.source,
            o.target,
            o.rel_type,
            level=o.level,
            rationale=o.rationale,
            basis="assumption" if o.rel_type != "inferred_preparation" else "interpretation",
        )
        return {
            "target": o.target,
            "before": None,
            "after": {"key": key, "type": o.rel_type},
            "summary": f"Add {o.rel_type.replace('_', ' ')}: {_key(snap, o.source)} → {_key(snap, o.target)}",
        }
    if isinstance(o, RemoveRelationship):
        r = snap.relationships.get(o.key)
        if r is None:
            raise OpError("relationship does not exist in this projection")
        del snap.relationships[o.key]
        return {
            "target": r["target"],
            "before": {"type": r["type"], "source": r["source"], "target": r["target"]},
            "after": None,
            "summary": f"Remove {r['type'].replace('_', ' ')}: {_key(snap, r['source'])} → {_key(snap, r['target'])}",
        }
    raise OpError(f"unsupported operation {type(o).__name__}")


def project(base: Snapshot, changes: list[tuple[int, dict[str, Any]]], scenario_id: str) -> tuple[Snapshot, list[dict]]:
    """Replay applied changes over a copy of the base snapshot. Returns (projection, per-change results)."""
    snap = base.copy()
    results = []
    for seq, payload in changes:
        try:
            res = apply_op(snap, parse_op(payload), scenario_id, seq)
            results.append({"seq": seq, "ok": True, **res})
        except OpError as e:
            results.append({"seq": seq, "ok": False, "error": str(e)})
    snap.invalidate()
    return snap, results


def diff(base: Snapshot, proj: Snapshot) -> dict[str, dict[str, str]]:
    ents: dict[str, str] = {}
    for eid in base.entities.keys() - proj.entities.keys():
        ents[eid] = "removed"
    for eid in proj.entities.keys() - base.entities.keys():
        ents[eid] = "added"
    for eid in proj.entities.keys() & base.entities.keys():
        b, p = base.entities[eid], proj.entities[eid]
        if (b["title"], b["details"]) != (p["title"], p["details"]) or base.placements.get(eid) != proj.placements.get(
            eid
        ):
            ents[eid] = "modified"
    rels: dict[str, str] = {}
    for k in base.relationships.keys() - proj.relationships.keys():
        rels[k] = "removed"
    for k in proj.relationships.keys() - base.relationships.keys():
        rels[k] = "added"
    rules = {
        "removed": sorted(base.rules.keys() - proj.rules.keys()),
        "added": sorted(proj.rules.keys() - base.rules.keys()),
    }
    return {"entities": ents, "relationships": rels, "rules": rules}
