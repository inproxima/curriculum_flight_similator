"""Connection map (fake provider; no network, no cost).

Every outcome, assessment and topic is examined: the model proposes connections with a level (direct / strong /
partial) or says it found none. Covers: levels stored as asserted strengths on proposed links, verdicts ("none
found" vs "linked"), deterministic checks (batch scope, link type per source, quotes, duplicates), forward
(horizontal) links to later courses, the Web payload (connection level per edge, mapping per node), draft-only
writes, cost preview and cap, caching, and mapping a course right after an outline import is applied.
"""

import json
import re
import uuid

from sqlalchemy import select
from test_ai import Fake, use
from test_outline_import import OUTLINE, extraction, fake_for, fetch, new_draft, reconcile_main, snapshot, start

from cfs.models import AlignmentVerdict, Job


def cid(client, version, key):
    return next(e["id"] for e in client.get(f"/api/v1/versions/{version}/entities").json() if e["key"] == key)


def keys_in(user: str) -> set[str]:
    return set(re.findall(r"^\[([^\]]+)\]", user, re.M))


def link(t, rt, level, quote, why="because"):
    return {"target_key": t, "rel_type": rt, "level": level, "quote": quote, "rationale": why}


def map_script(model, req):
    if req.schema_name != "alignment_map":
        raise AssertionError(req.schema_name)
    u = req.user
    course = re.search(r"Course (\S+ \d+)", u).group(1)
    if course != "SYN 202":
        return json.dumps({"elements": [], "notes": []}), []
    if m := re.search(r"Follow-up: your previous answer had no entry for (.+)\. Give", u):
        asked = m.group(1).split(", ")
        if "LATER elements" not in u:
            return json.dumps({"elements": [], "notes": []}), []  # still no answer: stays "not examined"
        return json.dumps(
            {"elements": [{"source_key": k, "links": [], "none_reason": "Checked again."} for k in asked], "notes": []}
        ), []
    if "EARLIER elements" in u:  # backward batch: this course's topics / outcomes ← earlier courses
        earlier = keys_in(u.split("Targets (earlier and prerequisite courses' outcomes and topics):")[1])
        assert "SYN102-CO1" in earlier and "chemistry-foundations" in earlier
        assert not any(k.startswith(("SYN2", "SYN3", "SYN4", "PLO")) for k in earlier)
        assert "SYN202-CO1 → SYN102-CO1 (builds_on, accepted)" in u  # the documented link is listed
        return json.dumps(
            {
                "elements": [
                    {
                        "source_key": "SYN202-CO1",
                        "links": [
                            link("chemistry-foundations", "builds_on", "strong", "metabolic pathways"),
                            link("SYN102-CO1", "builds_on", "direct", "chemical equilibrium"),  # documented already
                        ],
                        "none_reason": None,
                    },
                    {"source_key": "SYN202-CO2", "links": [], "none_reason": "Kinetics is new in this course."},
                ],
                "notes": [],
            }
        ), []
    if "LATER elements" in u:  # forward batch
        later = keys_in(u.split("Targets (later courses' outcomes and topics):")[1])
        assert "SYN202-CO1" not in later and "PLO1" not in later  # only later courses' elements are targets
        target = sorted(k for k in later if k.startswith("SYN3"))[0]
        return json.dumps(
            {
                "elements": [
                    {
                        "source_key": "biochemistry",
                        "links": [link(target, "prepares_for", "strong", "biochemistry")],
                        "none_reason": None,
                    },
                    {"source_key": "SYN202-CO2", "links": [], "none_reason": "Nothing later builds on kinetics."},
                ],
                "notes": [],
            }
        ), []
    elements = [
        # a new partial surmise, plus four the checks must drop
        {
            "source_key": "SYN202-CO1",
            "links": [
                link("PLO3", "contributes_to", "partial", "metabolic pathways"),
                link("PLO1", "contributes_to", "direct", "metabolic pathways"),  # already documented
                link("SYN301-CO1", "contributes_to", "strong", "metabolic"),  # not in this batch
                link("PLO2", "contributes_to", "strong", "regression modelling"),  # quote nowhere
            ],
            "none_reason": None,
        },
        {"source_key": "SYN202-CO2", "links": [], "none_reason": "No program outcome concerns enzyme kinetics."},
        {
            "source_key": "SYN202-A1",
            "links": [link("PLO1", "contributes_to", "direct", "Enzyme kinetics")],  # wrong type for an assessment
            "none_reason": None,
        },
        {"source_key": "biochemistry", "links": [], "none_reason": None},
    ]
    assert {"PLO1", "SYN202-CO1", "SYN202-A1", "biochemistry"} <= keys_in(u)
    return json.dumps({"elements": elements, "notes": ["Kinetics is taught but not a program outcome."]}), []


def test_map_proposes_levels_and_records_none_found(client, seeded, v1, db, monkeypatch):
    fake = Fake(map_script)
    use(monkeypatch, "openai", fake)
    draft = new_draft(client, seeded, v1)
    course = cid(client, draft, "SYN 202")
    v1_hash = snapshot(v1).content_hash()

    pv = client.get(f"/api/v1/versions/{draft}/ai-map/preview", params={"course_id": course}).json()
    assert pv["calls"] == 3 and pv["estimate_usd"] > 0 and pv["draft"] is True and pv["elements"] >= 4
    assert [b["kind"] for b in pv["batches"]] == ["vertical", "forward", "backward"]
    r = client.post(f"/api/v1/versions/{draft}/ai-map", json={"course_ids": [course]})
    assert r.status_code == 202, r.text
    job = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded", job
    res = job["result"]
    assert res["by_level"] == {"direct": 0, "strong": 2, "partial": 1}
    assert res["calls"] == 5 and res["cost_usd"] > 0  # 3 batches + 2 follow-ups for skipped elements
    # the app's job center finds it: by kind, by owner, by status, with its latest progress message
    mine = client.get("/api/v1/jobs", params={"kind": "ai_map_alignment", "mine": True, "status": ["succeeded"]}).json()
    assert mine[0]["id"] == r.json()["job_id"] and mine[0]["last_message"] == "finished"
    active = client.get("/api/v1/jobs", params={"kind": "ai_map_alignment", "status": ["queued", "running"]}).json()
    assert active == []
    assert client.get("/api/v1/jobs", params={"status": "bogus"}).status_code == 422
    assert client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()["last_message"] == "finished"
    assert db.get(Job, uuid.UUID(r.json()["job_id"])).payload["cost_cap_usd"] >= pv["estimate_usd"]
    reasons = " | ".join(d["reason"] for d in res["dropped"])
    for why in ("link already exists", "unknown target", "quote not found", "is not a link this batch maps"):
        assert why in reasons

    snap = snapshot(draft)
    co1, co2 = cid(client, draft, "SYN202-CO1"), cid(client, draft, "SYN202-CO2")
    plo3 = cid(client, draft, "PLO3")
    new = snap.rels("contributes_to", source=co1, target=plo3)[0]
    assert new["review_state"] == "proposed" and new["basis"] == "interpretation" and new["strength"] == 0.5
    topic = cid(client, draft, "biochemistry")
    fwd = [r for r in snap.rels("prepares_for", source=topic)]
    assert len(fwd) == 1 and fwd[0]["strength"] == 0.8
    assert snapshot(v1).content_hash() == v1_hash  # published version untouched

    rows = {
        (str(v.entity_id), v.rel_type): v
        for v in db.scalars(select(AlignmentVerdict).where(AlignmentVerdict.curriculum_version_id == uuid.UUID(draft)))
    }
    assert rows[(co2, "contributes_to")].verdict == "none" and "enzyme kinetics" in rows[(co2, "contributes_to")].reason
    assert rows[(co1, "contributes_to")].verdict == "linked"
    assert rows[(cid(client, draft, "SYN202-A1"), "assesses")].verdict == "linked"  # already had links
    assert rows[(co2, "prepares_for")].verdict == "none"
    # prerequisite side: CO1 builds on SYN 102 (documented) and now chemistry-foundations (proposed, strong);
    # CO2 was examined against every earlier course and builds on none of them
    chem = cid(client, draft, "chemistry-foundations")
    back = snap.rels("prepares_for", source=chem, target=co1)
    assert len(back) == 1 and back[0]["strength"] == 0.8 and back[0]["review_state"] == "proposed"
    assert rows[(co1, "prepared_by")].verdict == "linked"
    assert rows[(co2, "prepared_by")].verdict == "none"
    assert rows[(co2, "prepared_by")].reason == "Kinetics is new in this course."
    assert rows[(fwd[0]["target"], "prepared_by")].verdict == "linked"  # the later element now builds on SYN 202
    assert (topic, "prepared_by") not in rows  # skipped twice: stays "not examined"
    # each skipped source was asked about once more, and only those
    follow = [req.user for _m, req in fake.requests if "Follow-up" in req.user]
    assert len(follow) == 2
    assert any("no entry for SYN202-CO1." in u and "LATER elements" in u for u in follow)
    assert any("no entry for biochemistry." in u and "EARLIER elements" in u for u in follow)
    assert rows[(co1, "prepares_for")].verdict == "linked"  # the retry's "none" never overrides a link

    web = client.get(f"/api/v1/versions/{draft}/web", params={"detail": "full"}).json()
    nodes = {n["id"]: n for n in web["nodes"]}
    assert nodes[co2]["mapping"]["contributes_to"]["verdict"] == "none"
    assert nodes[co2]["mapping"]["contributes_to"]["best"] is None
    assert nodes[co1]["mapping"]["contributes_to"]["best"] == "direct"  # the documented PLO1 link
    edges = {(e["source"], e["target"], e["type"]): e for e in web["edges"]}
    assert edges[(co1, plo3, "contributes_to")]["connection"] == "partial"
    assert edges[(co1, cid(client, draft, "PLO1"), "contributes_to")]["connection"] == "direct"
    assert edges[(topic, fwd[0]["target"], "prepares_for")]["connection"] == "strong"
    assert nodes[co1]["mapping"]["prepared_by"]["best"] == "direct"  # the documented SYN102-CO1 link
    assert nodes[co1]["mapping"]["prepared_by"]["count"] == 2
    assert nodes[co2]["mapping"]["prepared_by"] == {
        "best": None,
        "count": 0,
        "verdict": "none",
        "reason": "Kinetics is new in this course.",
    }
    assert "prepared_by" not in (nodes[cid(client, draft, "SYN202-A1")]["mapping"] or {})
    assert "prepared_by" not in nodes[cid(client, draft, "SYN101-CO1")]["mapping"]  # nothing comes before it
    assert "prepares_for" not in nodes[cid(client, draft, "SYN402-CO1")]["mapping"]  # nothing comes after it

    # a second run sees the first run's proposals as "already linked" and adds nothing new
    calls = len(fake.requests)
    r = client.post(f"/api/v1/versions/{draft}/ai-map", json={"course_ids": [course]})
    assert client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()["result"]["proposed"] == 0
    second = [req.user for _m, req in fake.requests[calls:]]
    assert any("SYN202-CO1 → PLO3 (contributes_to, proposed)" in u for u in second)


def test_map_is_draft_only_and_needs_an_editor(client, seeded, v1, monkeypatch):
    use(monkeypatch, "openai", Fake(map_script))
    r = client.post(f"/api/v1/versions/{v1}/ai-map", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_immutable"
    pv = client.get(f"/api/v1/versions/{v1}/ai-map/preview").json()
    assert pv["draft"] is False and pv["calls"] > 0


def test_outline_apply_can_map_the_course_afterwards(client, seeded, v1, db, monkeypatch):
    rec = fake_for(extraction(), reconcile_main)

    def script(model, req):
        if req.schema_name == "alignment_map":
            return map_script(model, req)
        return rec.script(model, req)

    use(monkeypatch, "openai", Fake(script))
    draft = new_draft(client, seeded, v1)
    course = cid(client, draft, "SYN 202")
    # its own text, so the extraction cache the outline-import tests rely on stays cold
    text = OUTLINE + "\nOffice hours: Tuesdays."
    imp = fetch(client, start(client, draft, course, text=text)["outline_import"]["id"])
    r = client.post(
        f"/api/v1/outline-imports/{imp['id']}/apply",
        json={"selected_item_ids": [i["id"] for i in imp["plan"]["items"] if i["default_selected"]], "map_after": True},
        headers={"If-Match": imp["etag"]},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["result"]["map_job_id"] and out["result"]["map_estimate_usd"] > 0
    job = client.get(f"/api/v1/jobs/{out['result']['map_job_id']}").json()
    assert job["status"] == "succeeded" and job["result"]["batches"] == 3
    assert db.scalar(
        select(AlignmentVerdict).where(AlignmentVerdict.curriculum_version_id == uuid.UUID(draft)).limit(1)
    )


def test_batches_cover_every_pair_once(client, seeded, v1):
    """Whole program: forward batches only (each earlier → later pair is asked once). One course: its forward batch
    plus a backward batch against the earlier courses, prerequisites first. A course's own (shared) topics are never
    its targets."""
    from cfs.ai.mapping import earlier_courses, plan_map_batches

    snap = snapshot(v1)
    by_key = {c["key"]: c["id"] for c in snap.courses()}
    whole = plan_map_batches(snap)
    assert {b["kind"] for b in whole} == {"vertical", "forward"}
    one = plan_map_batches(snap, course_ids=[by_key["SYN 202"]])
    assert [b["kind"] for b in one] == ["vertical", "forward", "backward"]
    back = one[2]
    assert back["covers"][0] == by_key["SYN 102"]  # the documented prerequisite first
    assert set(back["covers"]) == set(earlier_courses(snap, by_key["SYN 202"]))
    # SYN 330 shares experimental-design with SYN 210 and SYN 401: never its own target
    fwd = next(b for b in plan_map_batches(snap, course_ids=[by_key["SYN 330"]]) if b["kind"] == "forward")
    assert "experimental-design" not in {e["key"] for e in fwd["targets"]["prepares_for"]}
    assert by_key["SYN 401"] in fwd["covers"]
