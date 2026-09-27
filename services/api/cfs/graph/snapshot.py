"""Curriculum snapshots: an immutable, canonical, in-memory projection of one curriculum version.

The snapshot is plain data (string ids, JSON-safe values) so it can be hashed deterministically,
copied and modified by the scenario engine, and converted to NetworkX for traversal.
The database remains the source of truth; the snapshot is a read projection.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cfs.models import (
    AssessmentRevisionDetails,
    CoursePlacement,
    CourseRevisionDetails,
    CurriculumEntityMembership,
    CurriculumRelationshipMembership,
    CurriculumVersion,
    Entity,
    EntityFieldEvidence,
    EntityRevision,
    OutcomeRevisionDetails,
    Pathway,
    Program,
    RelationshipEvidence,
    RelationshipRevision,
    RequirementGroup,
    RequirementRule,
    TopicRevisionDetails,
)
from cfs.models.enums import EvidenceStance

TERM_ORDER = {"fall": 0, "winter": 1, "spring": 2, "summer": 3, "full_year": 0, "unknown": None}


def _num(v: Any) -> Any:
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() else f


@dataclass
class Snapshot:
    version: dict[str, Any]
    entities: dict[str, dict[str, Any]]
    placements: dict[str, list[dict[str, Any]]]  # course entity id → placements
    relationships: dict[str, dict[str, Any]]  # relationship key → relationship
    rules: dict[str, dict[str, Any]]  # rule id → rule
    groups: dict[str, dict[str, Any]]
    pathways: dict[str, dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)

    def copy(self) -> Snapshot:
        c = copy.deepcopy(self)
        c.meta = {}
        return c

    def invalidate(self) -> None:
        self.meta.clear()

    def canonical(self) -> dict[str, Any]:
        return {
            "version": self.version["id"],
            "entities": self.entities,
            "placements": self.placements,
            "relationships": self.relationships,
            "rules": self.rules,
            "groups": self.groups,
        }

    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical(), sort_keys=True, default=str).encode()).hexdigest()

    # ── convenience ──
    def by_key(self, etype: str | None, key: str) -> dict[str, Any] | None:
        for e in self.entities.values():
            if e["key"] == key and (etype is None or e["type"] == etype):
                return e
        return None

    def courses(self) -> list[dict[str, Any]]:
        return [e for e in self.entities.values() if e["type"] == "course"]

    def primary_placement(self, course_id: str) -> dict[str, Any] | None:
        pls = self.placements.get(course_id) or []
        return pls[0] if pls else None

    def _rel_index(self) -> dict[str, dict]:
        """Lazily built endpoint indexes (invalidated with meta on copy/modification)."""
        idx = self.meta.get("rel_idx")
        if idx is None:
            by_s: dict[str, list] = {}
            by_t: dict[str, list] = {}
            by_type: dict[str, list] = {}
            for r in sorted(self.relationships.values(), key=lambda r: r["key"]):
                by_s.setdefault(r["source"], []).append(r)
                by_t.setdefault(r["target"], []).append(r)
                by_type.setdefault(r["type"], []).append(r)
            idx = self.meta["rel_idx"] = {"s": by_s, "t": by_t, "type": by_type}
        return idx

    def rels(self, *types: str, source: str | None = None, target: str | None = None) -> list[dict[str, Any]]:
        idx = self._rel_index()
        if source is not None:
            pool = idx["s"].get(source, [])
        elif target is not None:
            pool = idx["t"].get(target, [])
        elif types:
            pool = [r for t in types for r in idx["type"].get(t, [])]
        else:
            pool = list(self.relationships.values())
        return [
            r
            for r in pool
            if (not types or r["type"] in types)
            and (source is None or r["source"] == source)
            and (target is None or r["target"] == target)
        ]

    def rules_for(self, target: str, kind: str | None = None) -> list[dict[str, Any]]:
        idx = self.meta.get("rule_idx")
        if idx is None:
            idx = {}
            for r in sorted(self.rules.values(), key=lambda r: r["id"]):
                idx.setdefault(r["target"], []).append(r)
            self.meta["rule_idx"] = idx
        return [r for r in idx.get(target, []) if kind is None or r["kind"] == kind]

    def to_nx(self, types: set[str] | None = None, *, include_rejected: bool = False) -> nx.MultiDiGraph:
        g = nx.MultiDiGraph()
        for eid, e in self.entities.items():
            g.add_node(eid, **{"type": e["type"], "key": e["key"]})
        for k, r in self.relationships.items():
            if types and r["type"] not in types:
                continue
            if not include_rejected and r["review_state"] in ("rejected", "superseded"):
                continue
            if r["source"] in g and r["target"] in g:
                g.add_edge(r["source"], r["target"], key=k, **r)
        return g


def time_index(pl: dict[str, Any] | None) -> float | None:
    """Ordinal position in the program: year*10 + term order. None if year or term unknown."""
    if not pl or pl.get("year") is None:
        return None
    t = TERM_ORDER.get(pl.get("term") or "unknown")
    if t is None:
        return None
    return pl["year"] * 10 + t


def load_snapshot(db: Session, version_id: uuid.UUID) -> Snapshot:
    v = db.get(CurriculumVersion, version_id)
    prog = db.get(Program, v.program_id)
    version = {
        "id": str(v.id),
        "label": v.label,
        "status": v.status.value,
        "program_id": str(v.program_id),
        "program_name": prog.name,
        "academic_year": v.academic_year,
        "cohort": v.cohort,
        "is_synthetic": v.is_synthetic or prog.is_synthetic,
    }

    pathways = {
        str(p.id): {"id": str(p.id), "code": p.code, "name": p.name}
        for p in db.scalars(select(Pathway).where(Pathway.program_id == v.program_id))
    }

    rows = db.execute(
        select(Entity, EntityRevision)
        .join(CurriculumEntityMembership, CurriculumEntityMembership.entity_id == Entity.id)
        .join(EntityRevision, EntityRevision.id == CurriculumEntityMembership.entity_revision_id)
        .where(CurriculumEntityMembership.curriculum_version_id == v.id)
    ).all()
    rev_ids = [r.id for _, r in rows]
    course_d = {
        d.entity_revision_id: d
        for d in db.scalars(select(CourseRevisionDetails).where(CourseRevisionDetails.entity_revision_id.in_(rev_ids)))
    }
    outcome_d = {
        d.entity_revision_id: d
        for d in db.scalars(
            select(OutcomeRevisionDetails).where(OutcomeRevisionDetails.entity_revision_id.in_(rev_ids))
        )
    }
    assess_d = {
        d.entity_revision_id: d
        for d in db.scalars(
            select(AssessmentRevisionDetails).where(AssessmentRevisionDetails.entity_revision_id.in_(rev_ids))
        )
    }
    topic_d = {
        d.entity_revision_id: d
        for d in db.scalars(select(TopicRevisionDetails).where(TopicRevisionDetails.entity_revision_id.in_(rev_ids)))
    }
    field_ev: dict[uuid.UUID, dict[str, int]] = {}
    for rid, fname, n in db.execute(
        select(EntityFieldEvidence.entity_revision_id, EntityFieldEvidence.field_name, func.count())
        .where(
            EntityFieldEvidence.entity_revision_id.in_(rev_ids), EntityFieldEvidence.stance == EvidenceStance.supporting
        )
        .group_by(EntityFieldEvidence.entity_revision_id, EntityFieldEvidence.field_name)
    ):
        field_ev.setdefault(rid, {})[fname] = n

    entities: dict[str, dict[str, Any]] = {}
    for ent, rev in rows:
        details: dict[str, Any] = {}
        if (d := course_d.get(rev.id)) is not None:
            details = {"course_code": d.course_code, "credits": _num(d.credits)}
        elif (d := outcome_d.get(rev.id)) is not None:
            details = {"scope": d.scope, "statement_verbatim": d.statement_verbatim, "label": d.normalized_label}
        elif (d := assess_d.get(rev.id)) is not None:
            details = {
                "format": d.format,
                "weight_percent": _num(d.weight_percent),
                "timing_week": d.timing_week,
                "timing_text": d.timing_text,
                "documented_hours": _num(d.documented_hours),
                "estimated_hours": {
                    "low": _num(d.estimated_hours_low),
                    "typical": _num(d.estimated_hours_typical),
                    "high": _num(d.estimated_hours_high),
                },
            }
        elif (d := topic_d.get(rev.id)) is not None:
            details = {"area": d.area, "level": d.level}
        entities[str(ent.id)] = {
            "id": str(ent.id),
            "type": ent.entity_type.value,
            "key": ent.stable_key,
            "revision_id": str(rev.id),
            "revision_number": rev.revision_number,
            "title": rev.title,
            "description": rev.description,
            "details": details,
            "origin": rev.origin.value,
            "is_synthetic": rev.is_synthetic,
            "field_evidence": field_ev.get(rev.id, {}),
        }

    placements: dict[str, list[dict[str, Any]]] = {}
    for p in db.scalars(select(CoursePlacement).where(CoursePlacement.curriculum_version_id == v.id)):
        placements.setdefault(str(p.entity_id), []).append(
            {
                "id": str(p.id),
                "year": p.year,
                "term": p.term.value,
                "classification": p.classification.value,
                "pathway": pathways[str(p.pathway_id)]["code"] if p.pathway_id else None,
                "offering_conditions": p.offering_conditions,
            }
        )
    for pls in placements.values():
        pls.sort(key=lambda x: (x["pathway"] or "", x["id"]))

    rel_rows = list(
        db.scalars(
            select(RelationshipRevision)
            .join(
                CurriculumRelationshipMembership,
                CurriculumRelationshipMembership.relationship_revision_id == RelationshipRevision.id,
            )
            .where(CurriculumRelationshipMembership.curriculum_version_id == v.id)
        )
    )
    ev_counts: dict[uuid.UUID, dict[str, int]] = {}
    for rid, stance, n in db.execute(
        select(RelationshipEvidence.relationship_revision_id, RelationshipEvidence.stance, func.count())
        .where(RelationshipEvidence.relationship_revision_id.in_([r.id for r in rel_rows]))
        .group_by(RelationshipEvidence.relationship_revision_id, RelationshipEvidence.stance)
    ):
        ev_counts.setdefault(rid, {})[stance.value] = n
    relationships = {}
    for r in rel_rows:
        relationships[str(r.relationship_key)] = {
            "key": str(r.relationship_key),
            "revision_id": str(r.id),
            "source": str(r.source_entity_id),
            "target": str(r.target_entity_id),
            "type": r.rel_type.value,
            "origin": r.origin.value,
            "basis": r.evidence_basis.value,
            "review_state": r.review_state.value,
            "level": r.contribution_level.value if r.contribution_level else None,
            "rule_id": str(r.requirement_rule_id) if r.requirement_rule_id else None,
            "rationale": r.rationale,
            "scope": r.scope,
            "is_synthetic": r.is_synthetic,
            "evidence": {
                "supporting": ev_counts.get(r.id, {}).get("supporting", 0),
                "contradicting": ev_counts.get(r.id, {}).get("contradicting", 0),
            },
        }

    rules = {
        str(r.id): {
            "id": str(r.id),
            "target": str(r.target_entity_id),
            "kind": r.rule_kind.value,
            "expression": r.expression,
            "source_text": r.source_text,
            "origin": r.origin.value,
            "basis": r.evidence_basis.value,
            "review_state": r.review_state.value,
            "evidence_span_id": str(r.evidence_span_id) if r.evidence_span_id else None,
        }
        for r in db.scalars(select(RequirementRule).where(RequirementRule.curriculum_version_id == v.id))
    }
    groups = {
        str(g.id): {
            "id": str(g.id),
            "code": g.code,
            "name": g.name,
            "kind": g.kind.value,
            "min_courses": g.min_courses,
            "min_credits": _num(g.min_credits),
            "members": sorted(str(m) for m in g.member_entity_ids),
        }
        for g in db.scalars(select(RequirementGroup).where(RequirementGroup.curriculum_version_id == v.id))
    }
    return Snapshot(version, entities, placements, relationships, rules, groups, pathways)
