"""Materialize a scenario projection as a new curriculum version (explicit publish only).

The new version starts as a clone of the base (same exact revisions), then the projection diff is applied:
added entities/relationships/rules are created with origin=manual; changed entities get new revisions;
removed items lose their membership in the new version only. The base version is untouched.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cfs.core.auth import Principal
from cfs.curriculum.service import (
    clone_version,
    create_relationship,
    create_revision,
    set_membership,
)
from cfs.graph.snapshot import Snapshot
from cfs.models import (
    CoursePlacement,
    CurriculumEntityMembership,
    CurriculumRelationshipMembership,
    CurriculumVersion,
    Entity,
    RelationshipRevision,
    RequirementRule,
    Scenario,
)
from cfs.models.enums import (
    ContributionLevel,
    EntityType,
    EvidenceBasis,
    Origin,
    PlacementClass,
    RelType,
    ReviewState,
    RuleKind,
    Term,
)
from cfs.scenarios.ops import diff

DETAIL_FIELDS = {
    "course": ("credits",),
    "course_outcome": ("statement_verbatim", "label"),
    "program_outcome": ("statement_verbatim", "label"),
    "assessment": ("format", "weight_percent", "timing_week", "timing_text", "documented_hours"),
    "topic": ("area", "level"),
    "activity": ("delivery_format",),
}


def _details(e: dict[str, Any]) -> dict[str, Any]:
    d = e["details"]
    t = e["type"]
    if t == "course":
        return {"credits": d.get("credits")}
    if t in ("course_outcome", "program_outcome"):
        return {
            "statement_verbatim": d.get("statement_verbatim"),
            "normalized_label": (d.get("label") or e["title"])[:300],
        }
    if t == "assessment":
        return {
            "format": d.get("format"),
            "weight_percent": d.get("weight_percent"),
            "timing_week": d.get("timing_week"),
            "timing_text": d.get("timing_text"),
            **(
                {
                    "estimated_hours_low": d["workload_assumptions"]["assessment"]["low"],
                    "estimated_hours_typical": d["workload_assumptions"]["assessment"]["typical"],
                    "estimated_hours_high": d["workload_assumptions"]["assessment"]["high"],
                }
                if "assessment" in (d.get("workload_assumptions") or {})
                else {}
            ),
        }
    if t == "topic":
        return {"area": d.get("area"), "level": d.get("level")}
    return {}


def materialize(
    db: Session, p: Principal, base_v: CurriculumVersion, base: Snapshot, proj: Snapshot, label: str, s: Scenario
) -> CurriculumVersion:
    new_v = clone_version(db, base_v, label=label, notes=f"Published from scenario '{s.title}'")
    cv = new_v.id
    d = diff(base, proj)
    idmap: dict[str, uuid.UUID] = {}

    def real(eid: str) -> uuid.UUID:
        return idmap.get(eid, uuid.UUID(eid))

    # entities
    for eid, st in sorted(d["entities"].items()):
        if st == "removed":
            u = uuid.UUID(eid)
            db.execute(
                delete(CoursePlacement).where(
                    CoursePlacement.curriculum_version_id == cv, CoursePlacement.entity_id == u
                )
            )
            db.execute(
                delete(RequirementRule).where(
                    RequirementRule.curriculum_version_id == cv, RequirementRule.target_entity_id == u
                )
            )
            db.execute(
                delete(CurriculumEntityMembership).where(
                    CurriculumEntityMembership.curriculum_version_id == cv, CurriculumEntityMembership.entity_id == u
                )
            )
            continue
        e = proj.entities[eid]
        if st == "added":
            ent = db.scalar(
                select(Entity).where(
                    Entity.organization_id == p.org_id,
                    Entity.entity_type == EntityType(e["type"]),
                    Entity.stable_key == e["key"],
                )
            )
            if ent is None:
                ent = Entity(
                    id=uuid.UUID(eid), organization_id=p.org_id, entity_type=EntityType(e["type"]), stable_key=e["key"]
                )
                db.add(ent)
                db.flush()
            idmap[eid] = ent.id
        else:
            ent = db.get(Entity, uuid.UUID(eid))
        b = base.entities.get(eid)
        content_changed = st == "added" or (
            b is not None and (b["title"], b["description"], _details(b)) != (e["title"], e["description"], _details(e))
        )
        if content_changed:
            rev = create_revision(
                db,
                ent,
                title=e["title"],
                description=e["description"],
                metadata={
                    "from_scenario": str(s.id),
                    **{k: v for k, v in e["details"].items() if k in ("delivery_format", "workload_assumptions")},
                },
                origin=Origin.manual,
                details=_details(e),
                created_by=p.user_id,
            )
            set_membership(db, cv, ent, rev)
        if e["type"] == "course" and (st == "added" or base.placements.get(eid) != proj.placements.get(eid)):
            db.execute(
                delete(CoursePlacement).where(
                    CoursePlacement.curriculum_version_id == cv, CoursePlacement.entity_id == ent.id
                )
            )
            m = db.scalar(
                select(CurriculumEntityMembership).where(
                    CurriculumEntityMembership.curriculum_version_id == cv,
                    CurriculumEntityMembership.entity_id == ent.id,
                )
            )
            for pl in proj.placements.get(eid, []):
                pw = next((pid for pid, pwy in proj.pathways.items() if pwy["code"] == pl["pathway"]), None)
                db.add(
                    CoursePlacement(
                        curriculum_version_id=cv,
                        entity_id=ent.id,
                        entity_revision_id=m.entity_revision_id,
                        year=pl["year"],
                        term=Term(pl["term"]),
                        classification=PlacementClass(pl["classification"]),
                        pathway_id=uuid.UUID(pw) if pw else None,
                        offering_conditions=pl["offering_conditions"],
                    )
                )
    db.flush()

    # rules
    rule_map: dict[str, uuid.UUID] = {}
    for rid in d["rules"]["removed"]:
        r = base.rules[rid]
        db.execute(
            delete(RequirementRule).where(
                RequirementRule.curriculum_version_id == cv,
                RequirementRule.target_entity_id == uuid.UUID(r["target"]),
                RequirementRule.rule_kind == RuleKind(r["kind"]),
            )
        )
    for rid in d["rules"]["added"]:
        r = proj.rules[rid]
        row = RequirementRule(
            organization_id=p.org_id,
            curriculum_version_id=cv,
            target_entity_id=real(r["target"]),
            rule_kind=RuleKind(r["kind"]),
            expression=r["expression"],
            source_text=r["source_text"],
            origin=Origin.manual,
            evidence_basis=EvidenceBasis.assumption,
            review_state=ReviewState.accepted,
        )
        db.add(row)
        db.flush()
        rule_map[rid] = row.id

    # relationships
    for k, st in sorted(d["relationships"].items()):
        if st == "removed":
            db.execute(
                delete(CurriculumRelationshipMembership).where(
                    CurriculumRelationshipMembership.curriculum_version_id == cv,
                    CurriculumRelationshipMembership.relationship_key == uuid.UUID(k),
                )
            )
            continue
        r = proj.relationships[k]
        create_relationship(
            db,
            p.org_id,
            cv,
            real(r["source"]),
            real(r["target"]),
            RelType(r["type"]),
            origin=Origin.manual,
            basis=EvidenceBasis(r["basis"]),
            review=ReviewState.accepted,
            level=ContributionLevel(r["level"]) if r["level"] else None,
            rule_id=rule_map.get(r["rule_id"]) if r["rule_id"] else None,
            rationale=r["rationale"] or f"Added in scenario '{s.title}'",
            created_by=p.user_id,
        )
    # drop memberships of relationships touching removed entities (cloned from base)
    removed_ids = [uuid.UUID(e) for e, st in d["entities"].items() if st == "removed"]
    if removed_ids:
        keys = select(RelationshipRevision.relationship_key).where(
            RelationshipRevision.source_entity_id.in_(removed_ids)
            | RelationshipRevision.target_entity_id.in_(removed_ids)
        )
        db.execute(
            delete(CurriculumRelationshipMembership).where(
                CurriculumRelationshipMembership.curriculum_version_id == cv,
                CurriculumRelationshipMembership.relationship_key.in_(keys),
            )
        )
    db.flush()
    return new_v
