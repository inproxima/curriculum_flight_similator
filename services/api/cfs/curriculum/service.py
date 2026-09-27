"""Write-side services for curriculum entities, relationships, and rules.

Revisions are append-only. A curriculum version references exact revisions via membership rows;
"editing" an entity in a draft version creates a new revision and repoints the membership.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cfs.core.errors import Conflict, NotFound
from cfs.models import (
    ActivityRevisionDetails,
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
    RelationshipEvidence,
    RelationshipRevision,
    TopicRevisionDetails,
)
from cfs.models.enums import (
    ContributionLevel,
    EntityType,
    EvidenceBasis,
    EvidenceStance,
    Origin,
    RelType,
    ReviewState,
    VersionStatus,
)

DETAIL_MODELS = {
    EntityType.course: CourseRevisionDetails,
    EntityType.program_outcome: OutcomeRevisionDetails,
    EntityType.course_outcome: OutcomeRevisionDetails,
    EntityType.assessment: AssessmentRevisionDetails,
    EntityType.topic: TopicRevisionDetails,
    EntityType.activity: ActivityRevisionDetails,
}


def assert_draft(db: Session, version_id: uuid.UUID) -> CurriculumVersion:
    v = db.get(CurriculumVersion, version_id)
    if v is None:
        raise NotFound("Curriculum version not found")
    if v.status != VersionStatus.draft:
        raise Conflict(
            "Published curriculum versions are immutable. Create a scenario or a new draft version.",
            code="version_immutable",
        )
    return v


def get_or_create_entity(db: Session, org_id: uuid.UUID, etype: EntityType, key: str) -> Entity:
    e = db.scalar(
        select(Entity).where(Entity.organization_id == org_id, Entity.entity_type == etype, Entity.stable_key == key)
    )
    if e is None:
        e = Entity(organization_id=org_id, entity_type=etype, stable_key=key)
        db.add(e)
        db.flush()
    return e


def create_revision(
    db: Session,
    entity: Entity,
    *,
    title: str,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
    origin: Origin,
    is_synthetic: bool = False,
    details: dict[str, Any] | None = None,
    created_by: uuid.UUID | None = None,
) -> EntityRevision:
    n = (
        db.scalar(select(func.max(EntityRevision.revision_number)).where(EntityRevision.entity_id == entity.id)) or 0
    ) + 1
    rev = EntityRevision(
        organization_id=entity.organization_id,
        entity_id=entity.id,
        revision_number=n,
        title=title,
        description=description,
        metadata_=metadata or {},
        origin=origin,
        is_synthetic=is_synthetic,
        created_by=created_by,
    )
    db.add(rev)
    db.flush()
    model = DETAIL_MODELS[entity.entity_type]
    d = dict(details or {})
    if entity.entity_type == EntityType.course:
        d.setdefault("course_code", entity.stable_key)
        d.setdefault("title", title)
    if entity.entity_type in (EntityType.program_outcome, EntityType.course_outcome):
        d.setdefault("scope", "program" if entity.entity_type == EntityType.program_outcome else "course")
        d.setdefault("normalized_label", title)
    db.add(model(entity_revision_id=rev.id, **d))
    db.flush()
    return rev


def set_membership(db: Session, version_id: uuid.UUID, entity: Entity, rev: EntityRevision) -> None:
    m = db.scalar(
        select(CurriculumEntityMembership).where(
            CurriculumEntityMembership.curriculum_version_id == version_id,
            CurriculumEntityMembership.entity_id == entity.id,
        )
    )
    if m is None:
        db.add(
            CurriculumEntityMembership(curriculum_version_id=version_id, entity_id=entity.id, entity_revision_id=rev.id)
        )
    else:
        m.entity_revision_id = rev.id
    db.flush()


def add_field_evidence(
    db: Session, rev: EntityRevision, field: str, span_id: uuid.UUID, stance: EvidenceStance = EvidenceStance.supporting
) -> None:
    db.add(EntityFieldEvidence(entity_revision_id=rev.id, field_name=field, evidence_span_id=span_id, stance=stance))


def create_relationship(
    db: Session,
    org_id: uuid.UUID,
    version_id: uuid.UUID,
    source: uuid.UUID,
    target: uuid.UUID,
    rel_type: RelType,
    *,
    origin: Origin,
    basis: EvidenceBasis,
    review: ReviewState,
    level: ContributionLevel | None = None,
    rule_id: uuid.UUID | None = None,
    rationale: str | None = None,
    scope: str | None = None,
    is_synthetic: bool = False,
    evidence: list[tuple[uuid.UUID, EvidenceStance]] | None = None,
    created_by: uuid.UUID | None = None,
) -> RelationshipRevision:
    if rel_type == RelType.possible_overlap and str(source) > str(target):
        source, target = target, source
    rev = RelationshipRevision(
        organization_id=org_id,
        relationship_key=uuid.uuid4(),
        revision_number=1,
        source_entity_id=source,
        target_entity_id=target,
        rel_type=rel_type,
        origin=origin,
        evidence_basis=basis,
        review_state=review,
        contribution_level=level,
        requirement_rule_id=rule_id,
        rationale=rationale,
        scope=scope,
        is_synthetic=is_synthetic,
        created_by=created_by,
    )
    db.add(rev)
    db.flush()
    db.add(
        CurriculumRelationshipMembership(
            curriculum_version_id=version_id, relationship_key=rev.relationship_key, relationship_revision_id=rev.id
        )
    )
    for span_id, stance in evidence or []:
        db.add(RelationshipEvidence(relationship_revision_id=rev.id, evidence_span_id=span_id, stance=stance))
    db.flush()
    return rev


def revise_relationship(
    db: Session,
    version_id: uuid.UUID,
    old: RelationshipRevision,
    *,
    created_by: uuid.UUID | None = None,
    **changes: Any,
) -> RelationshipRevision:
    """New immutable revision with the same key; membership in `version_id` is repointed.

    Accepting an inferred relationship changes review_state only. Callers must never pass
    evidence_basis=explicit_statement for something that was an interpretation.
    """
    if "evidence_basis" in changes and changes["evidence_basis"] != old.evidence_basis:
        if changes["evidence_basis"] == EvidenceBasis.explicit_statement:
            raise Conflict(
                "Review cannot relabel an interpretation as an explicit source statement.",
                code="basis_relabel_forbidden",
            )
    n = (
        db.scalar(
            select(func.max(RelationshipRevision.revision_number)).where(
                RelationshipRevision.relationship_key == old.relationship_key
            )
        )
        or 0
    ) + 1
    fields = {
        c: getattr(old, c)
        for c in (
            "organization_id",
            "relationship_key",
            "source_entity_id",
            "target_entity_id",
            "rel_type",
            "scope",
            "origin",
            "evidence_basis",
            "review_state",
            "contribution_level",
            "requirement_rule_id",
            "rationale",
            "is_synthetic",
        )
    }
    fields.update(changes)
    new = RelationshipRevision(revision_number=n, created_by=created_by, **fields)
    db.add(new)
    db.flush()
    for ev in db.scalars(select(RelationshipEvidence).where(RelationshipEvidence.relationship_revision_id == old.id)):
        db.add(
            RelationshipEvidence(
                relationship_revision_id=new.id, evidence_span_id=ev.evidence_span_id, stance=ev.stance, note=ev.note
            )
        )
    m = db.scalar(
        select(CurriculumRelationshipMembership).where(
            CurriculumRelationshipMembership.curriculum_version_id == version_id,
            CurriculumRelationshipMembership.relationship_key == old.relationship_key,
        )
    )
    if m is None:
        db.add(
            CurriculumRelationshipMembership(
                curriculum_version_id=version_id, relationship_key=old.relationship_key, relationship_revision_id=new.id
            )
        )
    else:
        m.relationship_revision_id = new.id
    db.flush()
    return new


def place_course(db: Session, version_id: uuid.UUID, entity: Entity, rev: EntityRevision, **kw: Any) -> CoursePlacement:
    p = CoursePlacement(curriculum_version_id=version_id, entity_id=entity.id, entity_revision_id=rev.id, **kw)
    db.add(p)
    db.flush()
    return p


def clone_version(
    db: Session, src: CurriculumVersion, *, label: str, status: VersionStatus = VersionStatus.draft, **fields: Any
) -> CurriculumVersion:
    """Copy memberships/placements/rules/groups by reference to exact revisions (no revision copies)."""
    from cfs.models import RequirementGroup, RequirementRule

    new = CurriculumVersion(
        organization_id=src.organization_id,
        program_id=src.program_id,
        label=label,
        status=VersionStatus.draft,
        parent_version_id=src.id,
        is_synthetic=src.is_synthetic,
        academic_year=fields.pop("academic_year", src.academic_year),
        cohort=fields.pop("cohort", src.cohort),
        **fields,
    )
    db.add(new)
    db.flush()
    for m in db.scalars(
        select(CurriculumEntityMembership).where(CurriculumEntityMembership.curriculum_version_id == src.id)
    ):
        db.add(
            CurriculumEntityMembership(
                curriculum_version_id=new.id, entity_id=m.entity_id, entity_revision_id=m.entity_revision_id
            )
        )
    for m in db.scalars(
        select(CurriculumRelationshipMembership).where(CurriculumRelationshipMembership.curriculum_version_id == src.id)
    ):
        db.add(
            CurriculumRelationshipMembership(
                curriculum_version_id=new.id,
                relationship_key=m.relationship_key,
                relationship_revision_id=m.relationship_revision_id,
            )
        )
    for p in db.scalars(select(CoursePlacement).where(CoursePlacement.curriculum_version_id == src.id)):
        db.add(
            CoursePlacement(
                curriculum_version_id=new.id,
                entity_id=p.entity_id,
                entity_revision_id=p.entity_revision_id,
                year=p.year,
                term=p.term,
                pathway_id=p.pathway_id,
                classification=p.classification,
                offering_conditions=p.offering_conditions,
            )
        )
    for r in db.scalars(select(RequirementRule).where(RequirementRule.curriculum_version_id == src.id)):
        db.add(
            RequirementRule(
                organization_id=r.organization_id,
                curriculum_version_id=new.id,
                target_entity_id=r.target_entity_id,
                rule_kind=r.rule_kind,
                expression=r.expression,
                source_text=r.source_text,
                origin=r.origin,
                evidence_basis=r.evidence_basis,
                review_state=r.review_state,
                evidence_span_id=r.evidence_span_id,
            )
        )
    for g in db.scalars(select(RequirementGroup).where(RequirementGroup.curriculum_version_id == src.id)):
        db.add(
            RequirementGroup(
                organization_id=g.organization_id,
                curriculum_version_id=new.id,
                code=g.code,
                name=g.name,
                kind=g.kind,
                min_courses=g.min_courses,
                min_credits=g.min_credits,
                member_entity_ids=list(g.member_entity_ids),
                description=g.description,
            )
        )
    db.flush()
    if status == VersionStatus.published:
        new.status = VersionStatus.published
    return new
