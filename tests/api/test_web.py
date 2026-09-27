"""Alignment Web endpoint: shape, focus neighbourhoods, overview bundling, scenario comparison."""

import time
import uuid

from conftest import add_change, new_scenario

from cfs.graph.snapshot import Snapshot, load_snapshot
from cfs.graph.web import build_web


def replicated(snap: Snapshot, n: int) -> Snapshot:
    """A large program made of n disjoint copies of the synthetic one (ids suffixed), sharing program outcomes."""
    shared = {eid for eid, e in snap.entities.items() if e["type"] == "program_outcome"}

    def m(x: str, i: int) -> str:
        return x if x in shared else f"{x}~{i}"

    ents, pls, rels = {}, {}, {}
    for i in range(n):
        for eid, e in snap.entities.items():
            if eid in shared and i:
                continue
            ents[m(eid, i)] = {**e, "id": m(eid, i), "key": e["key"] if eid in shared else f"{e['key']}~{i}"}
        for eid, p in snap.placements.items():
            pls[m(eid, i)] = p
        for k, r in snap.relationships.items():
            rels[f"{k}~{i}"] = {**r, "key": f"{k}~{i}", "source": m(r["source"], i), "target": m(r["target"], i)}
    return Snapshot(snap.version, ents, pls, rels, {}, {}, snap.pathways)


def test_web_shape_and_every_element_is_a_node(client, v1):
    w = client.get(f"/api/v1/versions/{v1}/web").json()
    assert w["detail"] == "full" and w["focus"] is None and w["scenario"] is None
    types = w["stats"]["by_type"]
    assert types["program_outcome"] == 10 and types["course"] == 25 and types["assessment"] > 20
    assert {"vertical", "horizontal", "containment"} <= {e["category"] for e in w["edges"]}
    for e in w["edges"]:
        if e["type"] in ("contributes_to", "assesses", "supports", "prepares_for"):
            assert e["strength"]["label"] in ("strong", "moderate", "weak", "missing")
        else:
            assert e["strength"] is None
    assert {e["provenance"] for e in w["edges"]} >= {"documented", "proposed", "accepted_inference"}
    assert w["columns"][0]["band"] == "Year 1 · Fall"
    assert w["scorecard"]["threads_complete"]["of"] == 10
    clo = next(n for n in w["nodes"] if n["key"] == "SYN340-CO1")
    assert clo["course_id"] and clo["metrics"]["assessed"] is True and clo["metrics"]["label"] == "moderate"


def test_plo_focus_is_the_collaboration_neighbourhood(client, v1, ent):
    w = client.get(f"/api/v1/versions/{v1}/web", params={"focus": ent("PLO7")}).json()
    keys = {n["key"] for n in w["nodes"] if n["id"] in set(w["focus"]["node_ids"])}
    assert keys == {"PLO7", "SYN340-CO1", "SYN 340", "SYN340-A1", "teamwork"}
    assert w["focus"]["kind"] == "program_outcome" and w["focus"]["edge_ids"]


def test_course_focus_includes_horizontal_neighbours(client, v1, ent):
    w = client.get(f"/api/v1/versions/{v1}/web", params={"focus": ent("SYN 210")}).json()
    keys = {n["key"] for n in w["nodes"] if n["id"] in set(w["focus"]["node_ids"])}
    assert {"SYN210-CO1", "SYN210-CO2", "SYN210-A1", "PLO3", "PLO2"} <= keys
    assert {"SYN110-CO1", "SYN 110", "SYN330-CO1", "SYN230-CO1"} <= keys  # prepared by / prepares for


def test_overview_bundles_on_large_programs(db, v1):
    snap = replicated(load_snapshot(db, uuid.UUID(v1)), 8)
    t0 = time.perf_counter()
    w = build_web(snap)
    elapsed = time.perf_counter() - t0
    assert w["detail"] == "overview" and w["truncated"]
    assert {n["type"] for n in w["nodes"]} == {"course", "program_outcome"}
    bundles = [e for e in w["edges"] if e["bundle"]]
    assert bundles and all(b["bundle"]["count"] >= 1 for b in bundles)
    assert elapsed < 3.0, elapsed  # generous CI budget; see docs/performance.md for measured timings
    course = next(n for n in w["nodes"] if n["type"] == "course")
    focused = build_web(snap, focus=course["id"])
    assert any(n["type"] == "course_outcome" for n in focused["nodes"])


def test_scenario_web_marks_changes_and_baseline_scorecard(client, v1, ent):
    s = new_scenario(client, v1, "web diff")
    s = add_change(client, s, {"op": "remove_assessment", "entity_id": ent("SYN340-A1")})
    w = client.get(f"/api/v1/scenarios/{s['id']}/web").json()
    assert w["scenario"]["id"] == s["id"] and w["scorecard_baseline"] is not None
    by_key = {n["key"]: n for n in w["nodes"]}
    assert by_key["SYN340-A1"]["status"] == "removed"
    assert by_key["SYN340-CO1"]["status"] == "weakened"  # no longer assessed → contribution loses support
    statuses = {e["status"] for e in w["edges"]}
    assert "removed" in statuses and "weakened" in statuses
    base = client.get(f"/api/v1/versions/{v1}/web").json()
    assert w["scorecard_baseline"] == base["scorecard"]


def test_unknown_focus_is_harmless(client, v1):
    w = client.get(f"/api/v1/versions/{v1}/web", params={"focus": str(uuid.uuid4())}).json()
    assert w["focus"]["node_ids"] == []
