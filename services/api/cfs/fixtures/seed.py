"""Seed the SYNTHETIC fixture program.  Usage: python -m cfs.fixtures.seed

Builds two curriculum versions from fixtures/synthetic/program.yaml, generates matching synthetic PDFs,
and runs them through the real ingestion pipeline so that evidence spans are genuine page offsets.
Everything created here is flagged is_synthetic and labelled as not University of Calgary data.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import cfs.handlers  # noqa: F401  (register job handlers)
from cfs.core.auth import Principal, ensure_local_principal
from cfs.core.db import get_sessionmaker
from cfs.core.jobs import create_job, run_job
from cfs.curriculum.rules import dump_expr, leaf_edges, parse_requisite_text
from cfs.curriculum.service import (
    clone_version,
    create_relationship,
    create_revision,
    get_or_create_entity,
    place_course,
    set_membership,
)
from cfs.documents import store_upload
from cfs.fixtures.synthetic import build_outline_pdf, build_review_pdf, load_fixture
from cfs.ingest.pipeline import make_span
from cfs.models import (
    CoursePlacement,
    CurriculumEntityMembership,
    CurriculumRelationshipMembership,
    CurriculumSource,
    CurriculumVersion,
    DocumentVersion,
    Entity,
    Pathway,
    Program,
    RelationshipRevision,
    RequirementGroup,
    RequirementRule,
    ReviewItem,
)
from cfs.models.base import utcnow
from cfs.models.enums import (
    ApprovalStatus,
    ContributionLevel,
    DocumentType,
    EntityType,
    EvidenceBasis,
    EvidenceStance,
    Origin,
    PlacementClass,
    RelType,
    RequirementGroupKind,
    ReviewItemKind,
    ReviewState,
    RuleKind,
    SourceAuthority,
    Term,
    VersionStatus,
)

FIXTURE = Origin.synthetic_fixture
EXPLICIT, INTERP = EvidenceBasis.explicit_statement, EvidenceBasis.interpretation


def materialize(
    db: Session, p: Principal, fx: dict[str, Any], cv: CurriculumVersion, pathways: dict[str, uuid.UUID]
) -> dict[str, Entity]:
    org = p.org_id
    ents: dict[str, Entity] = {}

    def ent(
        etype: EntityType,
        key: str,
        title: str,
        *,
        description: str | None = None,
        details: dict | None = None,
        meta: dict | None = None,
    ) -> Entity:
        e = get_or_create_entity(db, org, etype, key)
        rev = create_revision(
            db,
            e,
            title=title,
            description=description,
            metadata={"synthetic": True, **(meta or {})},
            origin=FIXTURE,
            is_synthetic=True,
            details=details,
            created_by=p.user_id,
        )
        set_membership(db, cv.id, e, rev)
        ents[key] = e
        e._rev = rev  # type: ignore[attr-defined]
        return e

    def rel(src: str, tgt: str, t: RelType, basis=EXPLICIT, review=ReviewState.accepted, **kw) -> RelationshipRevision:
        return create_relationship(
            db,
            org,
            cv.id,
            ents[src].id,
            ents[tgt].id,
            t,
            origin=FIXTURE,
            basis=basis,
            review=review,
            is_synthetic=True,
            created_by=p.user_id,
            **kw,
        )

    for plo in fx["program_outcomes"]:
        ent(
            EntityType.program_outcome,
            plo["key"],
            f"{plo['key']}: {plo['label']}",
            details={"statement_verbatim": plo["statement"], "normalized_label": plo["label"]},
        )
    for t in fx["topics"]:
        ent(EntityType.topic, t["key"], t["title"], details={"area": t["area"], "level": t["level"]})

    for c in fx["courses"]:
        ent(
            EntityType.course,
            c["code"],
            c["title"],
            description=c["description"],
            details={"credits": c.get("credits")},
        )
        place_course(
            db,
            cv.id,
            ents[c["code"]],
            ents[c["code"]]._rev,
            year=c.get("year"),  # type: ignore[attr-defined]
            term=Term(c.get("term", "unknown")),
            classification=PlacementClass(c["class"]),
            pathway_id=pathways.get(c["pathway"]) if c.get("pathway") else None,
        )
        for o in c.get("outcomes", []):
            ent(
                EntityType.course_outcome,
                o["key"],
                o["statement"],
                details={"statement_verbatim": o["statement"], "normalized_label": o["statement"][:300]},
            )
            rel(c["code"], o["key"], RelType.has_outcome)
            for plo, lvl in o.get("contributes", []):
                rel(o["key"], plo, RelType.contributes_to, level=ContributionLevel(lvl))
        for tk in c.get("topics", []):
            rel(c["code"], tk, RelType.covers_topic)
        for a in c.get("assessments", []):
            ent(
                EntityType.assessment,
                a["key"],
                a["title"],
                details={
                    "format": a["format"],
                    "weight_percent": a.get("weight"),
                    "timing_week": a.get("week"),
                    "timing_text": f"week {a['week']}" if a.get("week") else None,
                },
            )
            rel(c["code"], a["key"], RelType.has_assessment)
            for ok in a.get("assesses", []):
                rel(a["key"], ok, RelType.assesses)

    # Requisite rules (explicit expressions) and the formal edges derived from their leaves.
    coreq_pairs: set[frozenset[str]] = set()
    for c in fx["courses"]:
        for field, kind in (("prerequisite", RuleKind.prerequisite), ("corequisite", RuleKind.corequisite)):
            if not c.get(field):
                continue
            text = c[field] + (", which may be taken concurrently" if kind == RuleKind.corequisite else "")
            expr = parse_requisite_text(text)
            rule = RequirementRule(
                organization_id=org,
                curriculum_version_id=cv.id,
                target_entity_id=ents[c["code"]].id,
                rule_kind=kind,
                expression=dump_expr(expr),
                source_text=c[field],
                origin=FIXTURE,
                evidence_basis=EXPLICIT,
                review_state=ReviewState.accepted,
            )
            db.add(rule)
            db.flush()
            for ref, _path, alt in leaf_edges(expr):
                if ref.code not in ents:
                    continue
                if kind == RuleKind.prerequisite:
                    rel(
                        ref.code,
                        c["code"],
                        RelType.formal_prerequisite,
                        rule_id=rule.id,
                        rationale="One of several alternatives" if alt else None,
                    )
                else:
                    pair = frozenset((ref.code, c["code"]))
                    if pair not in coreq_pairs:
                        coreq_pairs.add(pair)
                        rel(ref.code, c["code"], RelType.corequisite, rule_id=rule.id)

    for tp in fx.get("topic_preparation", []):
        basis = EXPLICIT if tp["basis"] == "explicit_statement" else INTERP
        rel(
            tp["from"],
            tp["to"],
            RelType.prepares_for,
            basis=basis,
            review=ReviewState(tp["review"]),
            rationale=tp.get("rationale") or tp.get("evidence"),
        )
    for ip in fx.get("inferred_preparation", []):
        rel(
            ip["from"],
            ip["to"],
            RelType.inferred_preparation,
            basis=INTERP,
            review=ReviewState(ip["review"]),
            rationale=ip["rationale"],
        )
    for ov in fx.get("overlaps", []):
        rel(ov["a"], ov["b"], RelType.possible_overlap, basis=INTERP, rationale=ov["rationale"])
    for g in fx.get("elective_groups", []):
        db.add(
            RequirementGroup(
                organization_id=org,
                curriculum_version_id=cv.id,
                code=g["code"],
                name=g["name"],
                kind=RequirementGroupKind.elective_group,
                min_courses=g.get("min_courses"),
                member_entity_ids=[ents[m].id for m in g["members"]],
                description="SYNTHETIC fixture elective group",
            )
        )
    db.flush()
    return ents


def _run_inline(db: Session, job) -> None:
    db.commit()
    run_job(job.id)
    db.expire_all()


def _assign(
    db: Session,
    p: Principal,
    dv: DocumentVersion,
    cv: CurriculumVersion,
    authority: SourceAuthority,
    applicability: str,
) -> CurriculumSource:
    src = CurriculumSource(
        organization_id=p.org_id,
        document_version_id=dv.id,
        curriculum_version_id=cv.id,
        authority=authority,
        approval_status=ApprovalStatus.approved,
        applicability=applicability,
        assigned_by=p.user_id,
    )
    db.add(src)
    db.flush()
    return src


def seed(db: Session, *, quiet: bool = False) -> dict[str, Any]:
    say = (lambda *a: None) if quiet else print
    p = ensure_local_principal(db)
    fx = load_fixture()
    prog_fx = fx["program"]
    if db.scalar(select(Program).where(Program.organization_id == p.org_id, Program.code == prog_fx["code"])):
        say(f"Synthetic program {prog_fx['code']} already present; nothing to do (use `make reset` to rebuild).")
        return {"skipped": True}

    prog = Program(
        organization_id=p.org_id,
        code=prog_fx["code"],
        name=prog_fx["name"],
        institution=prog_fx["institution"],
        parent_degree=prog_fx["parent_degree"],
        description=prog_fx["description"],
        is_synthetic=True,
    )
    db.add(prog)
    db.flush()
    pathways = {}
    for pw in fx["pathways"]:
        row = Pathway(organization_id=p.org_id, program_id=prog.id, code=pw["code"], name=pw["name"], is_synthetic=True)
        db.add(row)
        db.flush()
        pathways[pw["code"]] = row.id

    v1fx, v2fx = fx["versions"]
    v1 = CurriculumVersion(
        organization_id=p.org_id,
        program_id=prog.id,
        label=v1fx["label"],
        academic_year=v1fx["academic_year"],
        cohort=v1fx["cohort"],
        is_synthetic=True,
        notes="SYNTHETIC fixture version",
    )
    db.add(v1)
    db.flush()
    ents = materialize(db, p, fx, v1, pathways)
    say(f"Materialized {len(ents)} synthetic entities into {v1.label}")

    # Outline PDF → real pipeline → evidence spans attached to matching entities/relationships.
    outline, job, _ = store_upload(
        db,
        p,
        build_outline_pdf(fx, v1fx["academic_year"]),
        filename="synthetic_program_outline_2024-25.pdf",
        title="Synthetic Program Outline 2024–25 (SYNTHETIC)",
        document_type=DocumentType.program_outline,
        publication_date=date(2024, 5, 1),
        retrieval_date=date.today(),
        academic_year="2024-25",
        cohort_applicability="Students entering Fall 2024",
        is_synthetic=True,
    )
    _assign(db, p, outline, v1, SourceAuthority.authoritative, "Authoritative outline for the 2024–25 cohort")
    _run_inline(db, job)

    # Explicit topic-preparation statements get their own spans.
    for tp in fx.get("topic_preparation", []):
        if tp.get("evidence"):
            span = None
            for pg in range(1, (outline.page_count or 0) + 1):
                span = make_span(db, outline, pg, tp["evidence"])
                if span:
                    break
            rel = db.scalar(
                select(RelationshipRevision).where(
                    RelationshipRevision.source_entity_id == ents[tp["from"]].id,
                    RelationshipRevision.target_entity_id == ents[tp["to"]].id,
                    RelationshipRevision.rel_type == RelType.prepares_for,
                )
            )
            if span and rel:
                from cfs.models import RelationshipEvidence

                db.add(
                    RelationshipEvidence(
                        relationship_revision_id=rel.id, evidence_span_id=span.id, stance=EvidenceStance.supporting
                    )
                )
    db.flush()

    v1 = db.get(CurriculumVersion, v1.id)
    v1.status, v1.published_at, v1.published_by = VersionStatus.published, utcnow(), p.user_id
    db.commit()
    say(f"Published {v1.label}")

    # Version 2: clone + documented differences (draft).
    v2 = clone_version(
        db,
        v1,
        label=v2fx["label"],
        academic_year=v2fx["academic_year"],
        cohort=v2fx["cohort"],
        notes="SYNTHETIC fixture draft version",
    )
    for code, ov in (v2fx.get("overrides") or {}).items():
        pl = db.scalar(
            select(CoursePlacement).where(
                CoursePlacement.curriculum_version_id == v2.id, CoursePlacement.entity_id == ents[code].id
            )
        )
        pl.year = ov.get("year", pl.year)
        pl.term = Term(ov.get("term", pl.term))
    for code in v2fx.get("remove") or []:
        eid = ents[code].id
        db.execute(
            delete(CoursePlacement).where(
                CoursePlacement.curriculum_version_id == v2.id, CoursePlacement.entity_id == eid
            )
        )
        db.execute(
            delete(RequirementRule).where(
                RequirementRule.curriculum_version_id == v2.id, RequirementRule.target_entity_id == eid
            )
        )
        keys = select(RelationshipRevision.relationship_key).where(
            (RelationshipRevision.source_entity_id == eid) | (RelationshipRevision.target_entity_id == eid)
        )
        db.execute(
            delete(CurriculumRelationshipMembership).where(
                CurriculumRelationshipMembership.curriculum_version_id == v2.id,
                CurriculumRelationshipMembership.relationship_key.in_(keys),
            )
        )
        db.execute(
            delete(CurriculumEntityMembership).where(
                CurriculumEntityMembership.curriculum_version_id == v2.id, CurriculumEntityMembership.entity_id == eid
            )
        )
        for g in db.scalars(select(RequirementGroup).where(RequirementGroup.curriculum_version_id == v2.id)):
            if eid in g.member_entity_ids:
                g.member_entity_ids = [m for m in g.member_entity_ids if m != eid]
    db.flush()

    src2 = _assign(
        db,
        p,
        outline,
        v2,
        SourceAuthority.supporting,
        "2024–25 outline carried forward; differences in the 2025–26 draft need confirmation",
    )
    rjob = create_job(db, p.org_id, "resolve_source", {"curriculum_source_id": str(src2.id)}, user_id=p.user_id)
    _run_inline(db, rjob)

    review, job2, _ = store_upload(
        db,
        p,
        build_review_pdf(fx),
        filename="synthetic_curriculum_review_2019.pdf",
        title=fx["review_report"]["title"],
        document_type=DocumentType.review_report,
        publication_date=date.fromisoformat(fx["review_report"]["publication_date"]),
        retrieval_date=date.today(),
        is_synthetic=True,
    )
    _assign(
        db,
        p,
        review,
        v2,
        SourceAuthority.historical_reference,
        "Historical review; recommendations are not requirements",
    )
    _run_inline(db, job2)

    # Proposed inferred relationships in the draft go to the review inbox.
    for rel in db.scalars(
        select(RelationshipRevision)
        .join(
            CurriculumRelationshipMembership,
            CurriculumRelationshipMembership.relationship_revision_id == RelationshipRevision.id,
        )
        .where(
            CurriculumRelationshipMembership.curriculum_version_id == v2.id,
            RelationshipRevision.review_state == ReviewState.proposed,
        )
    ):
        s, t = db.get(Entity, rel.source_entity_id), db.get(Entity, rel.target_entity_id)
        db.add(
            ReviewItem(
                organization_id=p.org_id,
                kind=ReviewItemKind.inferred_mapping,
                curriculum_version_id=v2.id,
                subject_relationship_revision_id=rel.id,
                title=f"Inferred {rel.rel_type.value.replace('_', ' ')}: {s.stable_key} → {t.stable_key}",
                detail=rel.rationale,
                payload={"relationship_key": str(rel.relationship_key)},
                dedupe_key=f"inferred:{rel.relationship_key}:{v2.id}",
            )
        )
    db.commit()
    n_items = len(list(db.scalars(select(ReviewItem.id).where(ReviewItem.organization_id == p.org_id))))
    say(f"Created draft {v2.label}; review inbox has {n_items} items")
    say("All seeded content is SYNTHETIC and is not University of Calgary data.")
    return {"program_id": str(prog.id), "v1": str(v1.id), "v2": str(v2.id)}


def main() -> None:
    db = get_sessionmaker()()
    try:
        seed(db)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
