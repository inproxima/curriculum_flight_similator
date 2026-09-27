"""AI-proposed alignment links and inline link decisions (fake provider; no network).

Covers: deterministic verification (scope, type pairs, quotes, no duplicates of documented links), proposals written
as dashed `proposed` links in the draft only, published versions refused, decisions (accept / edit / reject) that
never relabel interpretations, closing inbox items, and rejected links never proposed again.
"""

import json

from sqlalchemy import select
from test_ai import Fake, use

from cfs.models import ReviewItem
from cfs.models.enums import ReviewItemStatus


def _script(model, req):
    if "[SYN202-CO2]" not in req.user:
        return json.dumps({"links": [], "no_link": []}), []
    links = [
        # valid: new, allowed, quote from the source's own text
        {
            "source_key": "SYN202-CO2",
            "target_key": "PLO1",
            "rel_type": "contributes_to",
            "strength": 0.7,
            "level": "reinforce",
            "rationale": "Kinetics interpretation builds biochemical foundations.",
            "quote": "enzyme kinetics",
        },
        # quote in neither element
        {
            "source_key": "SYN202-CO2",
            "target_key": "PLO2",
            "rel_type": "contributes_to",
            "strength": 0.9,
            "level": None,
            "rationale": "x",
            "quote": "regression modelling",
        },
        # an assessment cannot contribute to a program outcome directly
        {
            "source_key": "SYN202-A1",
            "target_key": "PLO1",
            "rel_type": "contributes_to",
            "strength": 0.5,
            "level": None,
            "rationale": "x",
            "quote": "Enzyme kinetics",
        },
        # already documented
        {
            "source_key": "SYN202-A1",
            "target_key": "SYN202-CO1",
            "rel_type": "assesses",
            "strength": 0.9,
            "level": None,
            "rationale": "x",
            "quote": "problem set",
        },
        # key the model invented
        {
            "source_key": "SYN999-CO1",
            "target_key": "PLO1",
            "rel_type": "contributes_to",
            "strength": 0.9,
            "level": None,
            "rationale": "x",
            "quote": "enzyme",
        },
    ]
    return json.dumps({"links": links, "no_link": []}), []


def _run(client, version, course_id):
    r = client.post(
        f"/api/v1/versions/{version}/ai-alignment",
        json={"course_ids": [course_id], "kinds": ["clo_plo", "assessment_clo", "topic_clo"]},
    )
    assert r.status_code == 202, r.text
    job = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "succeeded", job
    return job["result"]


def _edge(client, version, src_key, tgt_key, rtype):
    w = client.get(f"/api/v1/versions/{version}/web", params={"include_rejected": True}).json()
    by_key = {n["id"]: n["key"] for n in w["nodes"]}
    return next(
        (
            e
            for e in w["edges"]
            if e["type"] == rtype and by_key.get(e["source"]) == src_key and by_key.get(e["target"]) == tgt_key
        ),
        None,
    )


def test_ai_alignment_verifies_and_writes_proposed_links(client, db, v2, ent, monkeypatch):
    fake = Fake(_script)
    use(monkeypatch, "openai", fake)
    out = _run(client, v2, ent("SYN 202", v2))
    assert out["batches"] == 1 and out["proposed"] == 1, out
    reasons = sorted(d["reason"] for d in out["dropped"])
    assert reasons == sorted(
        [
            "quote not found in either element's text",
            "contributes_to cannot connect a assessment to a program_outcome",
            "link already exists",
            "unknown element key (not in this batch)",
        ]
    )
    e = _edge(client, v2, "SYN202-CO2", "PLO1", "contributes_to")
    assert e["provenance"] == "proposed" and e["origin"] == "ai_inferred" and e["basis"] == "interpretation"
    assert e["strength"]["asserted"] == 0.7 and e["strength"]["asserted_source"] == "ai_estimate"
    assert e["strength"]["effective"] < 0.7  # proposals rank below documented links
    item = db.scalar(select(ReviewItem).where(ReviewItem.dedupe_key.like(f"align:{v2}:SYN202-CO2:PLO1:%")))
    assert item is not None and item.model_run_id is not None and item.payload["origin"] == "model"

    # re-running is served from cache and proposes nothing new (the link now exists)
    again = _run(client, v2, ent("SYN 202", v2))
    assert again["proposed"] == 0 and len(fake.requests) == 1


def test_rejected_link_is_never_proposed_again_and_closes_inbox_item(client, db, v2, ent, monkeypatch):
    use(monkeypatch, "openai", Fake(_script))
    e = _edge(client, v2, "SYN202-CO2", "PLO1", "contributes_to")
    if e is None:  # independent of test order
        _run(client, v2, ent("SYN 202", v2))
        e = _edge(client, v2, "SYN202-CO2", "PLO1", "contributes_to")
    r = client.post(f"/api/v1/versions/{v2}/relationships/{e['key']}/decision", json={"decision": "reject"})
    assert r.status_code == 200 and r.json()["review_state"] == "rejected"
    w = client.get(f"/api/v1/versions/{v2}/web").json()
    assert e["key"] not in {x["key"] for x in w["edges"]}
    db.expire_all()
    item = db.scalar(select(ReviewItem).where(ReviewItem.dedupe_key.like(f"align:{v2}:SYN202-CO2:PLO1:%")))
    assert item.status == ReviewItemStatus.rejected
    out = _run(client, v2, ent("SYN 202", v2))
    assert out["proposed"] == 0
    assert "a reviewer rejected this link before" in {d["reason"] for d in out["dropped"]}


def test_published_version_refuses_ai_alignment_and_decisions(client, v1, ent):
    r = client.post(f"/api/v1/versions/{v1}/ai-alignment", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_immutable"
    e = _edge(client, v1, "SYN340-CO1", "SYN401-CO1", "prepares_for")
    r = client.post(f"/api/v1/versions/{v1}/relationships/{e['key']}/decision", json={"decision": "accept"})
    assert r.status_code == 409


def test_edit_accepts_with_reviewer_strength_and_keeps_interpretation(client, v2):
    e = _edge(client, v2, "SYN340-CO1", "SYN401-CO1", "prepares_for")
    assert e["provenance"] == "proposed"
    r = client.post(
        f"/api/v1/versions/{v2}/relationships/{e['key']}/decision",
        json={"decision": "edit", "strength": 0.55, "rationale": "Reviewed with the project coordinator."},
    )
    assert r.status_code == 200, r.text
    assert (
        r.json()["strength"] == 0.55
        and r.json()["strength_source"] == "reviewer"
        and r.json()["review_state"] == "accepted"
    )
    after = _edge(client, v2, "SYN340-CO1", "SYN401-CO1", "prepares_for")
    assert after["provenance"] == "accepted_inference" and after["basis"] == "interpretation"
    assert after["strength"]["effective"] == round(0.85 * 0.55, 3)


def test_documented_links_cannot_be_rejected_inline(client, v2):
    e = _edge(client, v2, "SYN101-CO1", "PLO1", "contributes_to")
    assert e["provenance"] == "documented"
    r = client.post(f"/api/v1/versions/{v2}/relationships/{e['key']}/decision", json={"decision": "reject"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "documented_link"


def test_bulk_accept(client, v2):
    w = client.get(f"/api/v1/versions/{v2}/web").json()
    keys = [e["key"] for e in w["edges"] if e["provenance"] == "proposed" and e["type"] == "prepares_for"][:2]
    assert keys
    r = client.post(f"/api/v1/versions/{v2}/relationships/decisions", json={"keys": keys, "decision": "accept"})
    assert r.status_code == 200 and r.json()["updated"] == len(keys) and r.json()["errors"] == []
