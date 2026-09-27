"""Alignment strength model (ADR 0003): formula, determinism, program-outcome threads, gaps, course pairs."""

import json
import uuid

import pytest

from cfs.analysis.alignment import alignment_index, edge_strength, label_for, pair_allowed, scorecard_delta
from cfs.graph.snapshot import Snapshot, load_snapshot


def _ent(eid, etype, key, **details):
    return {
        "id": eid,
        "type": etype,
        "key": key,
        "revision_id": None,
        "revision_number": 1,
        "title": key,
        "description": None,
        "details": details,
        "origin": "extracted",
        "is_synthetic": True,
        "field_evidence": {},
    }


def _rel(key, s, t, rtype, *, basis="explicit_statement", review="accepted", level=None, strength=None, origin="x"):
    return {
        "key": key,
        "revision_id": None,
        "source": s,
        "target": t,
        "type": rtype,
        "origin": origin,
        "basis": basis,
        "review_state": review,
        "level": level,
        "rule_id": None,
        "rationale": None,
        "strength": strength,
        "strength_source": "ai_estimate" if strength is not None else None,
        "scope": None,
        "is_synthetic": True,
        "evidence": {"supporting": 0, "contradicting": 0},
    }


def mini(*, assessed: bool, taught: bool, level: str, **kw) -> Snapshot:
    ents = {
        "c": _ent("c", "course", "C 100"),
        "o": _ent("o", "course_outcome", "C100-CO1"),
        "p": _ent("p", "program_outcome", "PLO1"),
        "a": _ent("a", "assessment", "C100-A1", weight_percent=40),
        "t": _ent("t", "topic", "topic"),
    }
    rels = {
        "h": _rel("h", "c", "o", "has_outcome"),
        "ha": _rel("ha", "c", "a", "has_assessment"),
        "ct": _rel("ct", "c", "t", "covers_topic"),
        "k": _rel("k", "o", "p", "contributes_to", level=level, **kw),
    }
    if assessed:
        rels["as"] = _rel("as", "a", "o", "assesses")
    if taught:
        rels["su"] = _rel("su", "t", "o", "supports")
    pl = {"c": [{"id": "pl", "year": 1, "term": "fall", "classification": "required", "pathway": None}]}
    return Snapshot({"id": "v"}, ents, pl, rels, {}, {}, {})


@pytest.mark.parametrize(
    "assessed,taught,level,kw,expected,label",
    [
        (True, True, "reinforce", {}, 0.8, "strong"),  # documented, taught and assessed
        (False, False, "introduce", {}, 0.42, "weak"),  # documented but unsupported introduction
        (True, False, "assess", {}, 0.9, "strong"),
        # AI proposal with a high asserted strength still ranks below a documented link
        (True, True, "reinforce", {"review": "proposed", "basis": "interpretation", "strength": 0.9}, 0.54, "moderate"),
        # accepted interpretation
        (True, True, "reinforce", {"basis": "interpretation"}, 0.68, "moderate"),
    ],
)
def test_edge_strength_formula(assessed, taught, level, kw, expected, label):
    snap = mini(assessed=assessed, taught=taught, level=level, **kw)
    st = edge_strength(snap, snap.relationships["k"])
    assert st["effective"] == expected and st["label"] == label
    assert set(st["components"]) == {"trust", "intensity", "support"}


def test_structural_links_carry_no_strength_and_labels():
    snap = mini(assessed=True, taught=True, level="assess")
    assert edge_strength(snap, snap.relationships["h"]) is None
    assert (label_for(0.7), label_for(0.45), label_for(0.1), label_for(0.0)) == (
        "strong",
        "moderate",
        "weak",
        "missing",
    )


def test_assessment_weight_sets_intensity():
    snap = mini(assessed=True, taught=False, level="assess")
    assert edge_strength(snap, snap.relationships["as"])["effective"] == 1.0  # 40% weight → full intensity
    snap.entities["a"]["details"]["weight_percent"] = 0
    snap.invalidate()
    assert edge_strength(snap, snap.relationships["as"])["effective"] == 0.55


def test_type_pairs():
    snap = mini(assessed=True, taught=True, level="assess")
    assert pair_allowed(snap, "assesses", "a", "o") and pair_allowed(snap, "supports", "t", "o")
    assert not pair_allowed(snap, "assesses", "o", "a") and not pair_allowed(snap, "contributes_to", "a", "p")


def test_alignment_index_is_deterministic(db, v1):
    a = alignment_index(load_snapshot(db, uuid.UUID(v1)))
    b = alignment_index(load_snapshot(db, uuid.UUID(v1)))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_fixture_threads_gaps_and_pairs(db, v1):
    snap = load_snapshot(db, uuid.UUID(v1))
    idx = alignment_index(snap)
    threads = {t["plo_key"]: t for t in idx["threads"]}
    collab = threads["PLO7"]
    assert collab["status"] == "never_assessed" and collab["missing"] == ["reinforce", "assess"]
    assert collab["label"] == "weak" and [s["course_key"] for s in collab["stages"]] == ["SYN 340"]
    assert threads["PLO10"]["status"] == "complete"
    assert threads["PLO10"]["firsts"] == {"introduce": "SYN 110", "reinforce": "SYN 310", "assess": "SYN 407"}

    kinds = {(g["kind"], snap.entities[g["entity_ids"][0]]["key"]) for g in idx["gaps"]}
    assert ("clo_no_plo", "SYN202-CO2") in kinds
    assert ("assessment_no_clo", "SYN220-A1") in kinds
    assert ("topic_no_clo", "bioinformatics") in kinds
    assert ("plo_never_assessed", "PLO7") in kinds

    pairs = {(p["source_key"], p["target_key"]): p for p in idx["course_pairs"]}
    assert pairs[("SYN 110", "SYN 210")]["label"] == "strong"
    assert pairs[("SYN 102", "SYN 201")]["label"] == "missing"  # a prerequisite with no outcome-level bridge
    assert pairs[("SYN 340", "SYN 401")]["label"] == "weak"  # proposed, low asserted strength

    sc = idx["scorecard"]
    assert sc["threads_complete"]["of"] == 10 and sc["links"]["proposed"] >= 5
    assert sc["orphans"] == {"clo": 1, "assessment": 1, "topic": 1}


def test_scorecard_delta():
    d = scorecard_delta({"a": 1, "n": {"x": 2}, "s": None}, {"a": 3, "n": {"x": 1}, "s": None})
    assert d == {"a": 2, "n": {"x": -1}, "s": None}
