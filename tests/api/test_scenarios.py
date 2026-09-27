"""Scenario analysis: baseline immutability, determinism, alternatives, staleness, workload, rebase, publish."""

from conftest import add_change, new_scenario

from cfs.graph.snapshot import load_snapshot
from cfs.scenarios.engine import analyze
from cfs.scenarios.ops import diff, project


def run(client, s):
    r = client.post(f"/api/v1/scenarios/{s['id']}/analyses")
    assert r.status_code == 201, r.text
    return r.json()


def test_baseline_is_immutable_under_scenarios(client, db, v1, ent):
    import uuid

    before = load_snapshot(db, uuid.UUID(v1)).content_hash()
    s = new_scenario(client, v1, "remove 310")
    s = add_change(client, s, {"op": "remove_course", "entity_id": ent("SYN 310")})
    run(client, s)
    db.expire_all()
    assert load_snapshot(db, uuid.UUID(v1)).content_hash() == before
    g = client.get(f"/api/v1/versions/{v1}/graph").json()
    assert "SYN 310" in {n["key"] for n in g["nodes"]}


def test_removing_prerequisite_breaks_documented_rules(client, v1, ent):
    s = new_scenario(client, v1, "remove 310 b")
    s = add_change(client, s, {"op": "remove_course", "entity_id": ent("SYN 310")})
    out = run(client, s)
    broken = {f["title"] for f in out["findings"] if f["rule_id"] == "requisite_broken"}
    assert "SYN 407 prerequisite can no longer be satisfied" in broken
    f = [f for f in out["findings"] if f["title"] == "SYN 407 prerequisite can no longer be satisfied"][0]
    assert f["consequence_class"] == "direct_documented" and any(e["evidence_span_id"] for e in f["evidence"])


def test_alternative_preparation_path(client, v1, ent):
    s = new_scenario(client, v1, "remove 201")
    s = add_change(client, s, {"op": "remove_course", "entity_id": ent("SYN 201")})
    out = run(client, s)
    titles = {f["title"]: f for f in out["findings"]}
    assert "SYN 220 remains satisfiable through an alternative" in titles  # SYN 201 OR SYN 202
    assert "SYN 220 prerequisite can no longer be satisfied" not in titles
    assert "SYN 311 prerequisite can no longer be satisfied" in titles  # AND with SYN 201


def test_deterministic_reproduction(client, db, v1, ent):
    import uuid

    s = new_scenario(client, v1, "determinism")
    s = add_change(client, s, {"op": "move_course", "entity_id": ent("SYN 210"), "year": 3, "term": "winter"})
    a = run(client, s)
    b = run(client, s)
    assert a["input_hash"] == b["input_hash"] and b["cached"] is True
    # recompute directly, without cache: identical ordered findings
    base = load_snapshot(db, uuid.UUID(v1))
    changes = [
        (
            1,
            {
                "op": "move_course",
                "entity_id": ent("SYN 210"),
                "year": 3,
                "term": "winter",
                "assumptions": [],
                "note": None,
            },
        )
    ]
    p1, r1 = project(base, changes, s["id"])
    p2, r2 = project(base, changes, s["id"])
    f1 = analyze(base, p1, diff(base, p1), r1)["findings"]
    f2 = analyze(base, p2, diff(base, p2), r2)["findings"]
    assert f1 == f2
    assert [f["title"] for f in f1] == [f["title"] for f in a["findings"]]


def test_stale_analysis_after_edit_and_undo_redo(client, v1, ent):
    s = new_scenario(client, v1, "staleness")
    s = add_change(client, s, {"op": "remove_outcome", "entity_id": ent("SYN310-CO1")})
    r1 = run(client, s)
    assert r1["scenario_revision"] == s["revision"]
    s = add_change(client, s, {"op": "remove_outcome", "entity_id": ent("SYN310-CO2")})
    runs = client.get(f"/api/v1/scenarios/{s['id']}/analyses").json()
    assert runs[0]["id"] == r1["id"] and runs[0]["stale"] is True
    u = client.post(f"/api/v1/scenarios/{s['id']}/undo", headers={"If-Match": str(s["revision"])})
    assert u.status_code == 200 and u.json()["head"] == 1
    r2 = run(client, u.json())
    assert (
        r2["input_hash"] == r1["input_hash"]
        and r2["cached"] is True
        and r2["scenario_revision"] == u.json()["revision"]
    )
    rd = client.post(f"/api/v1/scenarios/{s['id']}/redo", headers={"If-Match": str(u.json()["revision"])})
    assert rd.json()["head"] == 2


def test_optimistic_concurrency(client, v1, ent):
    s = new_scenario(client, v1, "concurrency")
    add_change(client, s, {"op": "remove_outcome", "entity_id": ent("SYN310-CO1")})
    r = client.post(
        f"/api/v1/scenarios/{s['id']}/changes",
        json={"op": "remove_outcome", "entity_id": ent("SYN310-CO2")},
        headers={"If-Match": str(s["revision"])},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_revision"
    r = client.post(
        f"/api/v1/scenarios/{s['id']}/changes", json={"op": "remove_outcome", "entity_id": ent("SYN310-CO2")}
    )
    assert r.status_code == 428


def test_invalid_ids_and_types_rejected(client, v1, ent):
    s = new_scenario(client, v1, "invalid")
    add_change(client, s, {"op": "remove_course", "entity_id": "00000000-0000-0000-0000-000000000000"}, expect=422)
    add_change(client, s, {"op": "move_course", "entity_id": ent("PLO1"), "year": 1, "term": "fall"}, expect=422)
    add_change(client, s, {"op": "teleport_course", "entity_id": ent("SYN 101")}, expect=422)


def test_topic_overlap_does_not_propagate(client, v1, ent):
    s = new_scenario(client, v1, "remove 212")
    s = add_change(client, s, {"op": "remove_course", "entity_id": ent("SYN 212")})
    out = run(client, s)
    affected = {a["key"] for f in out["findings"] for a in f["affected"]}
    assert "SYN 211" not in affected  # linked only by possible_overlap


def test_unknown_workload_and_congestion_reported_honestly(client, v1, ent):
    s = new_scenario(client, v1, "workload")
    s = add_change(
        client,
        s,
        {
            "op": "set_workload",
            "entity_id": ent("SYN 407"),
            "component": "reading",
            "low": 10,
            "typical": 15,
            "high": 25,
        },
    )
    out = run(client, s)
    wl = out["summary"]["workload"]
    assert wl["baseline"]["known_hours"] == 0 and wl["scenario"]["unquantified"] > 0
    assert wl["delta"]["estimated_typical"] == 15
    assert out["summary"]["congestion"]["calculable"] is False  # an assessment lacks a documented week
    f = [f for f in out["findings"] if f["rule_id"] == "workload_estimate"][0]
    assert f["evidence_basis"] == "assumption"


def test_relative_assessment_move_requires_documented_week(client, v1, ent):
    s = new_scenario(client, v1, "move assessment")
    add_change(client, s, {"op": "change_assessment", "entity_id": ent("SYN102-A1"), "shift_weeks": 2}, expect=422)
    s = add_change(client, s, {"op": "change_assessment", "entity_id": ent("SYN407-A1"), "shift_weeks": 2})
    ch = client.get(f"/api/v1/scenarios/{s['id']}/changes").json()[0]
    assert ch["before"]["timing_week"] == 6 and ch["after"]["timing_week"] == 8


def test_prerequisite_cycle_detected(client, v1, ent):
    s = new_scenario(client, v1, "cycle")
    s = add_change(client, s, {"op": "modify_requirement", "course_id": ent("SYN 101"), "text": "SYN 130"})
    out = run(client, s)
    assert any(f["rule_id"] == "prerequisite_cycle" for f in out["findings"])


def test_rebase_conflict_handling(client, v1, v2, ent):
    s = new_scenario(client, v1, "rebase")
    s = add_change(client, s, {"op": "move_course", "entity_id": ent("SYN 340"), "year": 4, "term": "winter"})
    s = add_change(client, s, {"op": "remove_course", "entity_id": ent("SYN 350")})
    r = client.post(
        f"/api/v1/scenarios/{s['id']}/rebase",
        json={"new_base_version_id": v2},
        headers={"If-Match": str(s["revision"])},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "rebase_conflicts"
    reasons = {c["op_type"]: c["reason"] for c in r.json()["error"]["details"]}
    assert "Baseline value changed" in reasons["move_course"]  # SYN 340 is already year 4 in v2
    assert "does not exist" in reasons["remove_course"]  # SYN 350 was removed in v2
    assert client.get(f"/api/v1/scenarios/{s['id']}").json()["base_version_id"] == v1  # nothing changed
    r = client.post(
        f"/api/v1/scenarios/{s['id']}/rebase",
        json={"new_base_version_id": v2, "resolution": "drop_conflicting"},
        headers={"If-Match": str(s["revision"])},
    )
    assert r.status_code == 200
    assert r.json()["scenario"]["base_version_id"] == v2 and r.json()["scenario"]["head"] == 1


def test_compare_two_scenarios_requires_same_base(client, v1, v2, ent):
    a = new_scenario(client, v1, "A")
    add_change(client, a, {"op": "remove_course", "entity_id": ent("SYN 420")})
    b = new_scenario(client, v1, "B")
    r = client.get(f"/api/v1/scenarios/{a['id']}/compare?other={b['id']}")
    assert r.status_code == 200
    # present in B, absent in A: the course and the outcome/assessment it contains
    assert sorted(e["key"] for e in r.json()["diff"]["entities"]["added"]) == ["SYN 420", "SYN420-A1", "SYN420-CO1"]
    c = new_scenario(client, v2, "C")
    assert client.get(f"/api/v1/scenarios/{a['id']}/compare?other={c['id']}").status_code == 409


def test_export_report_contains_sources_assumptions_changes(client, v1, ent):
    s = new_scenario(client, v1, "export")
    s = add_change(
        client,
        s,
        {
            "op": "remove_topic",
            "entity_id": ent("literature-appraisal"),
            "course_id": ent("SYN 310"),
            "assumptions": ["Appraisal is taught in SYN 407 instead"],
        },
    )
    run(client, s)
    md = client.get(f"/api/v1/scenarios/{s['id']}/export?format=md").text
    assert "SYNTHETIC FIXTURE DATA" in md and "Appraisal is taught in SYN 407 instead" in md
    assert "Remove topic 'Literature appraisal' from SYN 310" in md
    assert "Synthetic Program Outline" in md and "Source: “" in md
    html = client.get(f"/api/v1/scenarios/{s['id']}/export?format=html").text
    assert html.startswith("<!doctype html>") and "<script" not in html


def test_publish_creates_new_version_and_keeps_base(client, v1, ent):
    s = new_scenario(client, v1, "publishable")
    s = add_change(
        client,
        s,
        {
            "op": "add_course",
            "code": "SYN 415",
            "title": "Synthetic Data Ethics",
            "year": 4,
            "term": "winter",
            "credits": 3,
            "prerequisite_text": "SYN 320",
        },
    )
    s = add_change(client, s, {"op": "remove_course", "entity_id": ent("SYN 350")})
    r = client.post(
        f"/api/v1/scenarios/{s['id']}/publish",
        json={"label": "2026–27 (synthetic, from scenario)"},
        headers={"If-Match": str(s["revision"])},
    )
    assert r.status_code == 201, r.text
    nv = r.json()["version_id"]
    keys = {n["key"] for n in client.get(f"/api/v1/versions/{nv}/graph").json()["nodes"]}
    assert "SYN 415" in keys and "SYN 350" not in keys
    assert "SYN 350" in {n["key"] for n in client.get(f"/api/v1/versions/{v1}/graph").json()["nodes"]}
    ver = client.get(f"/api/v1/versions/{nv}").json()
    assert ver["status"] == "published"
    new_course = [n for n in client.get(f"/api/v1/versions/{nv}/entities").json() if n["key"] == "SYN 415"][0]
    rules = client.get(f"/api/v1/versions/{nv}/entities/{new_course['id']}").json()["requirement_rules"]
    assert rules[0]["rendered"] == "SYN 320" and rules[0]["basis"] == "assumption"
    # scenario is now locked
    assert (
        client.post(
            f"/api/v1/scenarios/{s['id']}/changes",
            json={"op": "remove_course", "entity_id": ent("SYN 101")},
            headers={"If-Match": str(r.json()["scenario"]["revision"])},
        ).status_code
        == 409
    )
