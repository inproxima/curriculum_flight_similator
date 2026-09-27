"""Review decisions. Accepting a proposal materializes it into a *draft* curriculum version.

Invariants:
- Accepting an inferred relationship sets review_state=accepted; evidence_basis stays "interpretation".
- Conflicts keep both sides: the non-chosen statement is attached as contradicting evidence.
- Published versions are immutable: materializing decisions against them is refused (409).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cfs.core.audit import audit
from cfs.core.auth import Principal
from cfs.core.errors import AppError, Conflict
from cfs.curriculum.rules import dump_expr, leaf_edges, parse_expr
from cfs.curriculum.service import (
    add_field_evidence,
    assert_draft,
    create_relationship,
    create_revision,
    get_or_create_entity,
    place_course,
    revise_relationship,
    set_membership,
)
from cfs.models import (
    CoursePlacement,
    CurriculumEntityMembership,
    CurriculumRelationshipMembership,
    DocumentVersion,
    Entity,
    RelationshipEvidence,
    RelationshipRevision,
    RequirementRule,
    ReviewDecision,
    ReviewItem,
)
from cfs.models.enums import (
    ContributionLevel,
    EntityType,
    EvidenceBasis,
    EvidenceStance,
    Origin,
    PlacementClass,
    RelType,
    ReviewItemKind,
    ReviewItemStatus,
    ReviewState,
    Role,
    RuleKind,
    Term,
)

ACK_ONLY = {
    ReviewItemKind.possible_duplicate,
    ReviewItemKind.uncertain_course_match,
    ReviewItemKind.unreadable_page,
    ReviewItemKind.weak_evidence,
}


def _member_entity(db: Session, cv: uuid.UUID, etype: EntityType, key: str) -> Entity | None:
    return db.scalar(
        select(Entity)
        .join(CurriculumEntityMembership, CurriculumEntityMembership.entity_id == Entity.id)
        .where(
            CurriculumEntityMembership.curriculum_version_id == cv,
            Entity.entity_type == etype,
            Entity.stable_key == key,
        )
    )


def _uuid(v: Any) -> uuid.UUID | None:
    return uuid.UUID(str(v)) if v else None


def decide(
    db: Session,
    p: Principal,
    item: ReviewItem,
    decision: str,
    *,
    rationale: str | None = None,
    edited_payload: dict | None = None,
    choice: str | None = None,
) -> dict[str, Any]:
    p.require(Role.reviewer)
    if item.status not in (ReviewItemStatus.open, ReviewItemStatus.deferred):
        raise Conflict(f"Review item already {item.status.value}")
    applied: dict[str, Any] = {}
    payload = {**item.payload, **(edited_payload or {})}

    if decision == "defer":
        item.status = ReviewItemStatus.deferred
    elif decision == "reject":
        if item.kind == ReviewItemKind.inferred_mapping and item.subject_relationship_revision_id:
            assert_draft(db, item.curriculum_version_id)
            rel = db.get(RelationshipRevision, item.subject_relationship_revision_id)
            new = revise_relationship(
                db, item.curriculum_version_id, rel, review_state=ReviewState.rejected, created_by=p.user_id
            )
            applied = {"relationship_revision_id": str(new.id), "review_state": "rejected"}
        item.status = ReviewItemStatus.rejected
    elif decision in ("accept", "edit"):
        applied = _materialize(db, p, item, payload, choice)
        item.status = ReviewItemStatus.edited if decision == "edit" else ReviewItemStatus.accepted
        if edited_payload:
            item.payload = payload
    else:
        raise AppError(f"Unknown decision {decision}")

    db.add(
        ReviewDecision(
            review_item_id=item.id,
            decision=item.status,
            edited_payload=edited_payload,
            rationale=rationale,
            decided_by=p.user_id,
        )
    )
    audit(
        db,
        p,
        f"review.{decision}",
        "review_item",
        item.id,
        {"kind": item.kind.value, "choice": choice, "applied": applied},
    )
    return applied


def _materialize(db: Session, p: Principal, item: ReviewItem, payload: dict, choice: str | None) -> dict[str, Any]:
    kind = item.kind
    if kind in ACK_ONLY:
        return {"acknowledged": True, "note": "No curriculum change; decision recorded."}
    if kind == ReviewItemKind.missing_academic_year:
        dv = db.get(DocumentVersion, item.document_version_id)
        if not payload.get("academic_year"):
            raise AppError("Provide academic_year in edited_payload to resolve this item", code="missing_field")
        dv.academic_year = payload["academic_year"]
        return {"document_version_id": str(dv.id), "academic_year": dv.academic_year}

    cv = item.curriculum_version_id
    assert_draft(db, cv)
    org = p.org_id

    if kind == ReviewItemKind.inferred_mapping and payload.get("proposal"):
        return _materialize_proposal(db, p, item, cv, payload)

    if kind == ReviewItemKind.inferred_mapping:
        rel = db.get(RelationshipRevision, item.subject_relationship_revision_id)
        new = revise_relationship(db, cv, rel, review_state=ReviewState.accepted, created_by=p.user_id)
        return {
            "relationship_revision_id": str(new.id),
            "review_state": "accepted",
            "evidence_basis": new.evidence_basis.value,
        }

    if kind == ReviewItemKind.candidate_entity:
        et = payload["entity_type"]
        span = _uuid(payload.get("span_id"))
        if et == "program_outcome":
            e = get_or_create_entity(db, org, EntityType.program_outcome, payload["key"])
            rev = create_revision(
                db,
                e,
                title=f"{payload['key']}: {payload['label']}",
                origin=Origin.extracted,
                created_by=p.user_id,
                details={"statement_verbatim": payload.get("statement"), "normalized_label": payload["label"][:300]},
            )
            set_membership(db, cv, e, rev)
            if span:
                add_field_evidence(db, rev, "label", span)
                if payload.get("statement"):
                    add_field_evidence(db, rev, "statement", span)
            return {"entity_id": str(e.id), "statement_documented": bool(payload.get("statement"))}
        if et == "course":
            e = get_or_create_entity(db, org, EntityType.course, payload["code"])
            f = payload.get("fields", {})
            val = lambda k: (f.get(k) or {}).get("value")  # noqa: E731
            rev = create_revision(
                db,
                e,
                title=payload["title"],
                description=val("description"),
                origin=Origin.extracted,
                details={"credits": val("credits")},
                created_by=p.user_id,
            )
            set_membership(db, cv, e, rev)
            place_course(
                db,
                cv,
                e,
                rev,
                year=val("year"),
                term=Term(val("term") or "unknown"),
                classification=PlacementClass(val("classification") or "unknown"),
            )
            if span:
                add_field_evidence(db, rev, "title", span)
            for k, fv in f.items():
                if fv.get("span_id"):
                    add_field_evidence(db, rev, k, uuid.UUID(fv["span_id"]))
            return {"entity_id": str(e.id), "revision_id": str(rev.id)}
        course = _member_entity(db, cv, EntityType.course, payload["course"])
        if course is None:
            raise Conflict(f"Course {payload['course']} is not in this curriculum version; accept it first.")
        ev = [(span, EvidenceStance.supporting)] if span else []
        if et == "course_outcome":
            e = get_or_create_entity(db, org, EntityType.course_outcome, payload["key"])
            rev = create_revision(
                db,
                e,
                title=payload["statement"],
                origin=Origin.extracted,
                created_by=p.user_id,
                details={"statement_verbatim": payload["statement"], "normalized_label": payload["statement"][:300]},
            )
            set_membership(db, cv, e, rev)
            if span:
                add_field_evidence(db, rev, "statement", span)
            create_relationship(
                db,
                org,
                cv,
                course.id,
                e.id,
                RelType.has_outcome,
                origin=Origin.extracted,
                basis=EvidenceBasis.explicit_statement,
                review=ReviewState.accepted,
                evidence=ev,
                created_by=p.user_id,
            )
            for plo_key, lvl in payload.get("contributes", []):
                plo = _member_entity(db, cv, EntityType.program_outcome, plo_key)
                if plo:
                    create_relationship(
                        db,
                        org,
                        cv,
                        e.id,
                        plo.id,
                        RelType.contributes_to,
                        origin=Origin.extracted,
                        basis=EvidenceBasis.explicit_statement,
                        review=ReviewState.accepted,
                        level=ContributionLevel(lvl),
                        evidence=ev,
                        created_by=p.user_id,
                    )
            return {"entity_id": str(e.id)}
        if et == "assessment":
            e = get_or_create_entity(db, org, EntityType.assessment, payload["key"])
            rev = create_revision(
                db,
                e,
                title=payload["title"],
                origin=Origin.extracted,
                created_by=p.user_id,
                details={
                    "format": payload.get("format"),
                    "weight_percent": payload.get("weight"),
                    "timing_week": payload.get("week"),
                },
            )
            set_membership(db, cv, e, rev)
            create_relationship(
                db,
                org,
                cv,
                course.id,
                e.id,
                RelType.has_assessment,
                origin=Origin.extracted,
                basis=EvidenceBasis.explicit_statement,
                review=ReviewState.accepted,
                evidence=ev,
                created_by=p.user_id,
            )
            for okey in payload.get("assesses", []):
                o = _member_entity(db, cv, EntityType.course_outcome, okey)
                if o:
                    create_relationship(
                        db,
                        org,
                        cv,
                        e.id,
                        o.id,
                        RelType.assesses,
                        origin=Origin.extracted,
                        basis=EvidenceBasis.explicit_statement,
                        review=ReviewState.accepted,
                        evidence=ev,
                        created_by=p.user_id,
                    )
            return {"entity_id": str(e.id)}
        raise AppError(f"Unsupported entity type {et}")

    if kind == ReviewItemKind.candidate_relationship and payload.get("type") == "requirement_group":
        return _install_group(db, p, cv, payload)

    if kind == ReviewItemKind.candidate_relationship:
        return _install_rule(
            db,
            p,
            cv,
            payload["target"],
            RuleKind(payload["rule_kind"]),
            payload["expression"],
            payload.get("text"),
            _uuid(payload.get("span_id")),
        )

    if kind == ReviewItemKind.conflicting_requirement:
        if choice not in ("existing", "proposed"):
            raise AppError(
                "Choose which interpretation applies: choice='existing' or 'proposed'", code="choice_required"
            )
        new_spans = [s for s in item.evidence_span_ids]
        if payload.get("type") == "requirement":
            old = db.get(RequirementRule, uuid.UUID(payload["existing_rule_id"]))
            if choice == "existing":
                _attach_contradicting(db, cv, old.target_entity_id, old.rule_kind, new_spans[:1])
                return {"kept": "existing", "contradicting_evidence_recorded": len(new_spans[:1])}
            old_span = old.evidence_span_id
            _remove_rule(db, cv, old)
            res = _install_rule(
                db,
                p,
                cv,
                payload["target"],
                RuleKind(payload["rule_kind"]),
                payload["proposed_expression"],
                payload.get("proposed_text"),
                new_spans[0] if new_spans else None,
            )
            if old_span:
                _attach_contradicting(db, cv, old.target_entity_id, old.rule_kind, [old_span])
            return {"kept": "proposed", **res}
        # field conflict
        ent = db.get(Entity, item.subject_entity_id)
        if choice == "existing":
            return {"kept": "existing"}
        field, value = payload["field"], payload["document"]
        if field in ("year", "term", "classification"):
            pl = db.scalar(
                select(CoursePlacement).where(
                    CoursePlacement.curriculum_version_id == cv, CoursePlacement.entity_id == ent.id
                )
            )
            setattr(
                pl,
                field,
                Term(value) if field == "term" else PlacementClass(value) if field == "classification" else int(value),
            )
            return {"kept": "proposed", "placement_id": str(pl.id), field: value}
        raise AppError(f"Field '{field}' changes must be made through a new entity revision (not yet supported here)")
    raise AppError(f"No materialization for {kind.value}")


def _install_rule(
    db: Session,
    p: Principal,
    cv: uuid.UUID,
    target_code: str,
    kind: RuleKind,
    expression: dict,
    text: str | None,
    span: uuid.UUID | None,
) -> dict[str, Any]:
    target = _member_entity(db, cv, EntityType.course, target_code)
    if target is None:
        raise Conflict(f"Course {target_code} is not in this curriculum version; accept it first.")
    expr = parse_expr(expression)
    rule = RequirementRule(
        organization_id=p.org_id,
        curriculum_version_id=cv,
        target_entity_id=target.id,
        rule_kind=kind,
        expression=dump_expr(expr),
        source_text=text,
        origin=Origin.extracted,
        evidence_basis=EvidenceBasis.explicit_statement,
        review_state=ReviewState.accepted,
        evidence_span_id=span,
    )
    db.add(rule)
    db.flush()
    edges, missing = [], []
    if kind in (RuleKind.prerequisite, RuleKind.corequisite):
        rt = RelType.formal_prerequisite if kind == RuleKind.prerequisite else RelType.corequisite
        for ref, _path, alt in leaf_edges(expr):
            src = _member_entity(db, cv, EntityType.course, ref.code)
            if src is None:
                missing.append(ref.code)
                continue
            r = create_relationship(
                db,
                p.org_id,
                cv,
                src.id,
                target.id,
                rt,
                origin=Origin.extracted,
                basis=EvidenceBasis.explicit_statement,
                review=ReviewState.accepted,
                rule_id=rule.id,
                rationale="One of several alternatives" if alt else None,
                evidence=[(span, EvidenceStance.supporting)] if span else [],
                created_by=p.user_id,
            )
            edges.append(str(r.id))
    return {"rule_id": str(rule.id), "edges": edges, "referenced_courses_not_in_version": missing}


def _remove_rule(db: Session, cv: uuid.UUID, rule: RequirementRule) -> None:
    keys = select(RelationshipRevision.relationship_key).where(RelationshipRevision.requirement_rule_id == rule.id)
    db.execute(
        delete(CurriculumRelationshipMembership).where(
            CurriculumRelationshipMembership.curriculum_version_id == cv,
            CurriculumRelationshipMembership.relationship_key.in_(keys),
        )
    )
    # also drop edges derived from equivalent rules cloned from a parent version
    for r in db.scalars(
        select(RelationshipRevision)
        .join(
            CurriculumRelationshipMembership,
            CurriculumRelationshipMembership.relationship_revision_id == RelationshipRevision.id,
        )
        .where(
            CurriculumRelationshipMembership.curriculum_version_id == cv,
            RelationshipRevision.target_entity_id == rule.target_entity_id,
            RelationshipRevision.rel_type
            == (RelType.formal_prerequisite if rule.rule_kind == RuleKind.prerequisite else RelType.corequisite),
        )
    ):
        db.execute(
            delete(CurriculumRelationshipMembership).where(
                CurriculumRelationshipMembership.curriculum_version_id == cv,
                CurriculumRelationshipMembership.relationship_key == r.relationship_key,
            )
        )
    db.delete(rule)
    db.flush()


def _attach_contradicting(
    db: Session, cv: uuid.UUID, target: uuid.UUID, kind: RuleKind, spans: list[uuid.UUID]
) -> None:
    rt = RelType.formal_prerequisite if kind == RuleKind.prerequisite else RelType.corequisite
    rels = db.scalars(
        select(RelationshipRevision)
        .join(
            CurriculumRelationshipMembership,
            CurriculumRelationshipMembership.relationship_revision_id == RelationshipRevision.id,
        )
        .where(
            CurriculumRelationshipMembership.curriculum_version_id == cv,
            RelationshipRevision.target_entity_id == target,
            RelationshipRevision.rel_type == rt,
        )
    )
    for r in rels:
        for s in spans:
            if not db.scalar(
                select(RelationshipEvidence.id).where(
                    RelationshipEvidence.relationship_revision_id == r.id, RelationshipEvidence.evidence_span_id == s
                )
            ):
                db.add(
                    RelationshipEvidence(
                        relationship_revision_id=r.id,
                        evidence_span_id=s,
                        stance=EvidenceStance.contradicting,
                        note="Conflicting source statement; reviewer kept the other interpretation",
                    )
                )


def _install_group(db: Session, p: Principal, cv: uuid.UUID, payload: dict) -> dict[str, Any]:
    from cfs.models import RequirementGroup
    from cfs.models.enums import RequirementGroupKind

    members, missing = [], []
    for code in payload.get("members", []):
        e = _member_entity(db, cv, EntityType.course, code)
        (members.append(e.id) if e else missing.append(code))
    g = RequirementGroup(
        organization_id=p.org_id,
        curriculum_version_id=cv,
        code=payload["code"][:80],
        name=payload["name"][:300],
        kind=RequirementGroupKind.elective_group,
        min_courses=payload.get("slot_count"),
        member_entity_ids=members,
        description=payload.get("rule_text"),
    )
    db.add(g)
    db.flush()
    return {"requirement_group_id": str(g.id), "members_linked": len(members), "members_not_in_version": missing}


def _materialize_proposal(db: Session, p: Principal, item: ReviewItem, cv: uuid.UUID, payload: dict) -> dict:
    origin = Origin.ai_inferred if payload.get("origin") == "model" else Origin.extracted
    spans = [(s, EvidenceStance.supporting) for s in item.evidence_span_ids]
    kind = payload["proposal"]
    if kind == "inferred_preparation":
        tgt = _member_entity(db, cv, EntityType.course, payload["target"])
        if tgt is None:
            raise Conflict(f"{payload['target']} is not in this curriculum version.")
        sources = payload.get("sources") or []
        if not sources:
            raise AppError(
                "The source text does not name the earlier courses. Edit the item to choose them "
                "(edited_payload.sources = [course codes]).",
                code="choose_sources",
            )
        made, missing = [], []
        for code in sources:
            src = _member_entity(db, cv, EntityType.course, code)
            if src is None:
                missing.append(code)
                continue
            r = create_relationship(
                db,
                p.org_id,
                cv,
                src.id,
                tgt.id,
                RelType.inferred_preparation,
                origin=origin,
                basis=EvidenceBasis.interpretation,
                review=ReviewState.accepted,
                rationale=f"Source describes: “{payload.get('described', '')}”",
                evidence=spans,
                created_by=p.user_id,
            )
            made.append(str(r.id))
        return {"relationships": made, "not_in_version": missing, "evidence_basis": "interpretation"}
    if kind == "course_contributes_to":
        c = db.get(Entity, uuid.UUID(payload["course_id"]))
        plo = db.get(Entity, uuid.UUID(payload["plo_id"]))
        r = create_relationship(
            db,
            p.org_id,
            cv,
            c.id,
            plo.id,
            RelType.contributes_to,
            origin=origin,
            basis=EvidenceBasis.interpretation,
            review=ReviewState.accepted,
            rationale=payload.get("rationale"),
            evidence=spans,
            created_by=p.user_id,
        )
        return {"relationship_revision_id": str(r.id), "evidence_basis": "interpretation"}
    raise AppError(f"Unknown proposal type {kind}")
