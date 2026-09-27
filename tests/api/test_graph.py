"""Graph semantics: identity across versions, explicit vs inferred, elective preparation, co-reqs vs cycles."""

import uuid

import pytest
from sqlalchemy import select

from cfs.core.errors import Conflict
from cfs.curriculum.service import revise_relationship
from cfs.models import RelationshipRevision
from cfs.models.enums import EvidenceBasis, RelType


def test_course_identity_across_versions(client, v1, v2, ent):
    sid = ent("SYN 340")
    assert ent("SYN 340", v2) == sid  # same stable entity in both versions
    rows = client.get(f"/api/v1/entities/{sid}/versions").json()
    assert {r["version_id"] for r in rows} == {v1, v2}
    assert len({r["revision_id"] for r in rows}) == 1  # same exact revision, different placement
    p1 = client.get(f"/api/v1/versions/{v1}/entities/{sid}").json()["placements"][0]
    p2 = client.get(f"/api/v1/versions/{v2}/entities/{sid}").json()["placements"][0]
    assert (p1["year"], p2["year"]) == (3, 4)


def test_graph_response_identifies_version_and_is_course_level_by_default(client, v1):
    g = client.get(f"/api/v1/versions/{v1}/graph").json()
    assert g["curriculum_version"]["id"] == v1 and g["scenario_revision"] is None
    assert {n["type"] for n in g["nodes"]} == {"course"}  # not a dense cluster of every entity
    types = {e["type"] for e in g["edges"]}
    assert "formal_prerequisite" in types and "inferred_preparation" in types
    assert not any(e["type"] == "possible_overlap" for e in g["edges"])  # only selected layers


def test_formal_and_inferred_are_distinct_types(client, v1):
    g = client.get(f"/api/v1/versions/{v1}/graph").json()
    formal = [e for e in g["edges"] if e["type"] == "formal_prerequisite"]
    inferred = [e for e in g["edges"] if e["type"] == "inferred_preparation"]
    assert all(e["basis"] == "explicit_statement" and e["has_evidence"] for e in formal)
    assert all(e["basis"] == "interpretation" for e in inferred)
    # OR alternatives are marked, not flattened into independent mandatory edges
    assert any(e.get("alternative_group") for e in formal)


def test_accepting_inferred_keeps_interpretation_basis(client, v2):
    items = client.get(f"/api/v1/reviews?kind=inferred_mapping&curriculum_version_id={v2}").json()["items"]
    item = [i for i in items if "SYN 340" in i["title"]][0]
    r = client.post(f"/api/v1/reviews/{item['id']}/decision", json={"decision": "accept"})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["review_state"] == "accepted"
    assert r.json()["applied"]["evidence_basis"] == "interpretation"
    key = r.json()["item"]["payload"]["relationship_key"]
    rel = client.get(f"/api/v1/versions/{v2}/relationships/{key}").json()["relationship"]
    assert rel["basis"] == "interpretation" and rel["review_state"] == "accepted"


def test_relabeling_inferred_as_explicit_is_forbidden(db, v2):
    rel = db.scalar(select(RelationshipRevision).where(RelationshipRevision.rel_type == RelType.inferred_preparation))
    with pytest.raises(Conflict):
        revise_relationship(db, uuid.UUID(v2), rel, evidence_basis=EvidenceBasis.explicit_statement)


def test_published_version_is_immutable_in_database(db, v1):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM course_placements WHERE curriculum_version_id = :v"), {"v": v1})
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE entity_revisions SET title = 'x' WHERE id = (SELECT id FROM entity_revisions LIMIT 1)"))
    db.rollback()


def test_review_cannot_modify_published_version(client, v1, db):
    from cfs.models import ReviewItem
    from cfs.models.enums import ReviewItemKind

    rel = db.scalar(select(RelationshipRevision).where(RelationshipRevision.rel_type == RelType.inferred_preparation))
    from cfs.core.auth import ensure_local_principal

    p = ensure_local_principal(db)
    item = ReviewItem(
        organization_id=p.org_id,
        kind=ReviewItemKind.inferred_mapping,
        curriculum_version_id=uuid.UUID(v1),
        subject_relationship_revision_id=rel.id,
        title="test on published",
        dedupe_key=f"t:{uuid.uuid4()}",
    )
    db.add(item)
    db.commit()
    r = client.post(f"/api/v1/reviews/{item.id}/decision", json={"decision": "accept"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_immutable"


def test_elective_dependent_preparation(client, v1, ent):
    d = client.get(f"/api/v1/versions/{v1}/entities/{ent('SYN 330')}/prior-learning").json()
    rule = d["requirement_rules"][0]
    assert rule["status"] == "conditional"  # SYN 211/212 are electives: not universal preparation
    issues = client.get(f"/api/v1/versions/{v1}/issues").json()["issues"]
    assert any(i["rule_id"] == "requisite_depends_on_elective" and "SYN 330" in i["title"] for i in issues)


def test_pathway_assumption_changes_exposure(client, v1, ent):
    all_ = client.get(f"/api/v1/versions/{v1}/entities/{ent('SYN 402')}/prior-learning").json()
    res = client.get(f"/api/v1/versions/{v1}/entities/{ent('SYN 402')}/prior-learning?pathway=RES").json()
    ex = lambda d: {i["entity"]["key"]: i.get("exposure") for i in d["items"]}  # noqa: E731
    assert ex(all_)["SYN 401"] == "pathway_dependent"
    assert ex(res)["SYN 401"] == "required_path"


def test_corequisites_are_not_cycles(client, v1):
    issues = client.get(f"/api/v1/versions/{v1}/issues").json()["issues"]
    assert not any(i["rule_id"] == "prerequisite_cycle" for i in issues)
    g = client.get(f"/api/v1/versions/{v1}/graph").json()
    assert sum(1 for e in g["edges"] if e["type"] == "corequisite") == 1


def test_overlap_is_not_a_dependency(client, v1, ent):
    d = client.get(f"/api/v1/versions/{v1}/entities/{ent('SYN 211')}/downstream").json()
    assert "SYN 212" not in {c["course"]["key"] for c in d["dependent_courses"]}


def test_downstream_prefers_documented_paths(client, v1, ent):
    d = client.get(f"/api/v1/versions/{v1}/entities/{ent('SYN 110')}/downstream").json()
    basis = {c["course"]["key"]: c["basis"] for c in d["dependent_courses"]}
    assert basis["SYN 330"] == "documented"  # via SYN 210 formal chain, although an inferred edge also exists


def test_outcome_matrix_defines_denominator_and_unknowns(client, v1):
    m = client.get(f"/api/v1/versions/{v1}/outcome-matrix").json()
    assert m["coverage"]["denominator"] == 8 and "divided by all program outcomes" in m["coverage"]["definition"]
    rows = {r["course"]["key"]: r for r in m["rows"]}
    assert all(c["state"] == "insufficient" for c in rows["SYN 350"]["cells"].values())
    plo6 = [c for c in m["columns"] if c["plo"]["key"] == "PLO6"][0]
    assert plo6["assessed"] is False
