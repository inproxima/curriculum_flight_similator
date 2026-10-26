"""Write-side services for curriculum entities, relationships, and rules.

Revisions are append-only. A curriculum version references exact revisions via membership rows;
"editing" an entity in a draft version creates a new revision and repoints the membership.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cfs.core.errors import Conflict, NotFound
from cfs.ingest.text import normalize
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


def assert_draft(db: Session, version_id: uuid.UUID, *, lock: bool = False) -> CurriculumVersion:
    """The version must be a draft. `lock=True` holds a row lock until commit, so concurrent writers that repoint
    memberships of the same version (reviews, link decisions, outline imports) are serialized."""
    if lock:
        v = db.scalar(
            select(CurriculumVersion)
            .where(CurriculumVersion.id == version_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
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
    strength: float | None = None,
    strength_source: str | None = None,
    model_run_id: uuid.UUID | None = None,
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
        strength=strength,
        strength_source=strength_source if strength is not None else None,
        model_run_id=model_run_id,
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
    carry_evidence: bool = True,
    add_evidence: list[tuple[uuid.UUID, EvidenceStance]] | tuple = (),
    **changes: Any,
) -> RelationshipRevision:
    """New immutable revision with the same key; membership in `version_id` is repointed.

    Accepting an inferred relationship changes review_state only. Callers must never pass
    evidence_basis=explicit_statement for something that was an interpretation.
    Evidence is copied from the old revision (unless `carry_evidence=False`) and `add_evidence` is attached to the
    new revision only, so versions still pointing at the old revision are unaffected (copy-on-write).
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
            "strength",
            "strength_source",
            "model_run_id",
            "is_synthetic",
        )
    }
    fields.update(changes)
    new = RelationshipRevision(revision_number=n, created_by=created_by, **fields)
    db.add(new)
    db.flush()
    seen: set[uuid.UUID] = set()
    if carry_evidence:
        for ev in db.scalars(
            select(RelationshipEvidence).where(RelationshipEvidence.relationship_revision_id == old.id)
        ):
            seen.add(ev.evidence_span_id)
            db.add(
                RelationshipEvidence(
                    relationship_revision_id=new.id,
                    evidence_span_id=ev.evidence_span_id,
                    stance=ev.stance,
                    note=ev.note,
                )
            )
    for span_id, stance in add_evidence:
        if span_id not in seen:
            seen.add(span_id)
            db.add(RelationshipEvidence(relationship_revision_id=new.id, evidence_span_id=span_id, stance=stance))
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


class _Keep:
    def __repr__(self) -> str:
        return "KEEP"


KEEP: Any = _Keep()  # "leave this field as it is" for revise_member


def membership(db: Session, version_id: uuid.UUID, entity_id: uuid.UUID) -> CurriculumEntityMembership | None:
    return db.scalar(
        select(CurriculumEntityMembership).where(
            CurriculumEntityMembership.curriculum_version_id == version_id,
            CurriculumEntityMembership.entity_id == entity_id,
        )
    )


def revision_details(db: Session, etype: EntityType, revision_id: uuid.UUID) -> dict[str, Any]:
    """The typed detail columns of one revision, as a dict suitable for create_revision(details=...)."""
    model = DETAIL_MODELS[etype]
    row = db.get(model, revision_id)
    if row is None:
        return {}
    return {c.key: getattr(row, c.key) for c in model.__table__.columns if c.key != "entity_revision_id"}


def revise_member(
    db: Session,
    version_id: uuid.UUID,
    entity: Entity,
    *,
    title: Any = KEEP,
    description: Any = KEEP,
    details: dict[str, Any] | None = None,
    origin: Origin | None = None,
    metadata: dict[str, Any] | None = None,
    drop_evidence_fields: tuple[str, ...] | list[str] = (),
    add_evidence: list[tuple[str, uuid.UUID]] | tuple = (),
    created_by: uuid.UUID | None = None,
) -> tuple[EntityRevision, EntityRevision]:
    """New revision of a member entity for THIS draft version only (copy-on-write). Returns (old, new).

    Unchanged fields and details are copied, field evidence is carried over except `drop_evidence_fields`, and
    `add_evidence` [(field, span)] is attached to the new revision only. The version's membership (and, for a
    course, its placements) is repointed; other versions keep pointing at the old revision. Relationships
    reference entities, so every link of the entity is preserved."""
    m = membership(db, version_id, entity.id)
    if m is None:
        raise NotFound(f"{entity.stable_key} is not part of this curriculum version")
    old = db.get(EntityRevision, m.entity_revision_id)
    new_title = old.title if title is KEEP else title
    d = {**revision_details(db, entity.entity_type, old.id), **(details or {})}
    if entity.entity_type == EntityType.course and title is not KEEP:
        d["title"] = new_title
    rev = create_revision(
        db,
        entity,
        title=new_title,
        description=old.description if description is KEEP else description,
        metadata=dict(old.metadata_ or {}) if metadata is None else metadata,
        origin=origin or old.origin,
        is_synthetic=old.is_synthetic,
        details=d,
        created_by=created_by,
    )
    drop = set(drop_evidence_fields)
    seen: set[tuple[str, uuid.UUID]] = set()
    for ev in db.scalars(select(EntityFieldEvidence).where(EntityFieldEvidence.entity_revision_id == old.id)):
        if ev.field_name in drop:
            continue
        seen.add((ev.field_name, ev.evidence_span_id))
        db.add(
            EntityFieldEvidence(
                entity_revision_id=rev.id,
                field_name=ev.field_name,
                evidence_span_id=ev.evidence_span_id,
                stance=ev.stance,
            )
        )
    for field, span_id in add_evidence:
        if span_id and (field, span_id) not in seen:
            seen.add((field, span_id))
            add_field_evidence(db, rev, field, span_id)
    m.entity_revision_id = rev.id
    for pl in db.scalars(
        select(CoursePlacement).where(
            CoursePlacement.curriculum_version_id == version_id, CoursePlacement.entity_id == entity.id
        )
    ):
        pl.entity_revision_id = rev.id
    db.flush()
    return old, rev


def revise_course_field(
    db: Session,
    version_id: uuid.UUID,
    entity: Entity,
    field: str,
    value: Any,
    span_id: uuid.UUID | None,
    *,
    created_by: uuid.UUID | None = None,
) -> EntityRevision:
    """New course revision in this version with one documented field changed (title, credits, description).

    Other versions keep their revision. Field evidence of unchanged fields is carried over."""
    kw: dict[str, Any] = {}
    if field == "title":
        kw["title"] = value
    elif field == "description":
        kw["description"] = value
    elif field == "credits":
        kw["details"] = {"credits": value}
    _, rev = revise_member(
        db,
        version_id,
        entity,
        drop_evidence_fields=(field,),
        add_evidence=[(field, span_id)] if span_id else [],
        created_by=created_by,
        **kw,
    )
    return rev


def drop_memberships(
    db: Session,
    version_id: uuid.UUID,
    *,
    entity_ids: list[uuid.UUID] | tuple = (),
    relationship_keys: list[uuid.UUID] | tuple = (),
) -> list[dict[str, Any]]:
    """Remove entities and relationships from ONE draft version (their revisions stay, other versions are
    unaffected). Returns undo-log entries [{kind, id, before, after=None}]. Callers must also drop every link that
    touches a removed entity (snapshot and analysis code assume link endpoints exist)."""
    undo: list[dict[str, Any]] = []
    for eid in entity_ids:
        m = membership(db, version_id, eid)
        if m is not None:
            undo.append({"kind": "entity", "id": str(eid), "before": str(m.entity_revision_id), "after": None})
            db.delete(m)
    for key in relationship_keys:
        m = db.scalar(
            select(CurriculumRelationshipMembership).where(
                CurriculumRelationshipMembership.curriculum_version_id == version_id,
                CurriculumRelationshipMembership.relationship_key == key,
            )
        )
        if m is not None:
            undo.append(
                {"kind": "relationship", "id": str(key), "before": str(m.relationship_revision_id), "after": None}
            )
            db.delete(m)
    db.flush()
    return undo


def allocate_key(
    db: Session,
    org_id: uuid.UUID,
    etype: EntityType,
    *,
    prefix: str,
    numbered: bool,
    text: str | None = None,
    version_id: uuid.UUID | None = None,
    taken: set[str] | frozenset = frozenset(),
) -> tuple[str, Entity | None]:
    """A stable key for a NEW member of `version_id`, free across the whole organization (keys are unique per org,
    not per version). Numbered keys continue the series ("CHEM351-CO7"); slug keys get "-2", "-3" on a clash. Keys in
    `taken` (already handed out in the same operation) are never returned.

    An existing entity with this prefix is reused only if its latest revision has identical text AND it is not a
    member of `version_id` (so one element keeps one identity across versions, but an addition can never collapse
    onto something this version already contains). Positional keys from the extractor are never reused blindly."""
    like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    ents = list(
        db.scalars(
            select(Entity).where(
                Entity.organization_id == org_id,
                Entity.entity_type == etype,
                Entity.stable_key.like(like, escape="\\"),
            )
        )
    )
    if text:
        want = normalize(text).lower()
        members = set()
        if version_id is not None and ents:
            members = set(
                db.scalars(
                    select(CurriculumEntityMembership.entity_id).where(
                        CurriculumEntityMembership.curriculum_version_id == version_id,
                        CurriculumEntityMembership.entity_id.in_([e.id for e in ents]),
                    )
                )
            )
        for e in sorted(ents, key=lambda x: x.stable_key):
            if e.id in members or e.stable_key in taken:
                continue
            latest = db.scalar(
                select(EntityRevision)
                .where(EntityRevision.entity_id == e.id)
                .order_by(EntityRevision.revision_number.desc())
                .limit(1)
            )
            if latest is not None and normalize(latest.title).lower() == want:
                return e.stable_key, e
    used = {e.stable_key for e in ents} | set(taken)
    if numbered:
        nums = [int(m.group(1)) for k in used if (m := re.fullmatch(re.escape(prefix) + r"(\d+)", k))]
        return f"{prefix}{max(nums, default=0) + 1}", None
    if prefix not in used:
        return prefix, None
    n = 2
    while f"{prefix}-{n}" in used:
        n += 1
    return f"{prefix}-{n}", None


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
