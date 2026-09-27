"""Programs, curriculum versions, graph views, entities, evidence, and analysis matrices."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cfs.api.v1.schemas import (
    PathwayOut,
    ProgramOut,
    SavedViewIn,
    SavedViewOut,
    SpanOut,
    VersionCreate,
    VersionOut,
)
from cfs.core.audit import audit
from cfs.core.auth import Principal, get_principal
from cfs.core.db import get_db
from cfs.core.errors import NotFound
from cfs.core.scope import get_scoped
from cfs.curriculum.service import clone_version
from cfs.graph import queries
from cfs.graph.snapshot import Snapshot, load_snapshot
from cfs.graph.view import build_view
from cfs.models import (
    CurriculumSource,
    CurriculumVersion,
    Document,
    DocumentVersion,
    Entity,
    EntityFieldEvidence,
    EntityRevision,
    EvidenceSpan,
    Pathway,
    Program,
    RelationshipEvidence,
    RelationshipRevision,
    RequirementRule,
    SavedView,
)
from cfs.models.enums import Role

router = APIRouter()


def version_snapshot(db: Session, p: Principal, version_id: uuid.UUID) -> Snapshot:
    get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    return load_snapshot(db, version_id)


@router.get("/programs", response_model=list[ProgramOut], tags=["programs"])
def list_programs(db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    return db.scalars(select(Program).where(Program.organization_id == p.org_id).order_by(Program.name)).all()


@router.get("/programs/{program_id}", response_model=ProgramOut, tags=["programs"])
def get_program(program_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    return get_scoped(db, Program, program_id, p, "Program")


@router.get("/programs/{program_id}/pathways", response_model=list[PathwayOut], tags=["programs"])
def list_pathways(program_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    get_scoped(db, Program, program_id, p, "Program")
    return db.scalars(select(Pathway).where(Pathway.program_id == program_id).order_by(Pathway.code)).all()


@router.get("/programs/{program_id}/versions", response_model=list[VersionOut], tags=["programs"])
def list_versions(program_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    get_scoped(db, Program, program_id, p, "Program")
    return db.scalars(
        select(CurriculumVersion)
        .where(CurriculumVersion.program_id == program_id)
        .order_by(CurriculumVersion.created_at)
    ).all()


@router.post("/programs/{program_id}/versions", response_model=VersionOut, status_code=201, tags=["programs"])
def create_version(
    program_id: uuid.UUID, body: VersionCreate, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    p.require(Role.editor)
    get_scoped(db, Program, program_id, p, "Program")
    if body.from_version_id:
        src = get_scoped(db, CurriculumVersion, body.from_version_id, p, "Curriculum version")
        v = clone_version(
            db,
            src,
            label=body.label,
            academic_year=body.academic_year or src.academic_year,
            cohort=body.cohort or src.cohort,
        )
    else:
        v = CurriculumVersion(
            organization_id=p.org_id,
            program_id=program_id,
            label=body.label,
            academic_year=body.academic_year,
            cohort=body.cohort,
        )
        db.add(v)
        db.flush()
    audit(db, p, "version.created", "curriculum_version", v.id, {"from": str(body.from_version_id)})
    db.commit()
    return v


@router.get("/versions/{version_id}", response_model=VersionOut, tags=["versions"])
def get_version(version_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    return get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")


@router.get("/versions/{version_id}/graph", tags=["graph"])
def get_graph(
    version_id: uuid.UUID,
    layers: list[str] = Query(default=["prerequisites", "preparation"]),
    expand: list[str] = Query(default=[]),
    focus: str | None = None,
    focus_depth: int = Query(default=1, ge=1, le=4),
    include_rejected: bool = False,
    node_budget: int = Query(default=300, ge=10, le=2000),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
) -> dict[str, Any]:
    snap = version_snapshot(db, p, version_id)
    view = build_view(
        snap,
        layers=layers,
        expand=expand,
        focus=focus,
        focus_depth=focus_depth,
        include_rejected=include_rejected,
        node_budget=node_budget,
    )
    view["scenario"] = None
    view["scenario_revision"] = None
    return view


@router.get("/versions/{version_id}/table", tags=["graph"])
def get_table(
    version_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
) -> dict[str, Any]:
    """Accessible list alternative to the graph: every course with its placement and direct relationships."""
    snap = version_snapshot(db, p, version_id)
    rows = []
    for c in sorted(snap.courses(), key=lambda c: c["key"]):
        pl = snap.primary_placement(c["id"])

        def names(rels, end):
            return [
                {
                    "id": r[end],
                    "key": snap.entities[r[end]]["key"],
                    "type": r["type"],
                    "basis": r["basis"],
                    "review_state": r["review_state"],
                }
                for r in rels
                if r[end] in snap.entities
            ]

        rows.append(
            {
                "id": c["id"],
                "key": c["key"],
                "title": c["title"],
                "credits": c["details"].get("credits"),
                "year": pl["year"] if pl else None,
                "term": pl["term"] if pl else None,
                "classification": pl["classification"] if pl else None,
                "pathway": pl["pathway"] if pl else None,
                "requires": names(
                    [
                        r
                        for r in snap.relationships.values()
                        if r["target"] == c["id"] and r["type"] in ("formal_prerequisite", "inferred_preparation")
                    ],
                    "source",
                ),
                "supports": names(
                    [
                        r
                        for r in snap.relationships.values()
                        if r["source"] == c["id"] and r["type"] in ("formal_prerequisite", "inferred_preparation")
                    ],
                    "target",
                ),
                "outcome_count": len(snap.rels("has_outcome", source=c["id"])),
                "evidence_fields": sorted(c["field_evidence"].keys()),
            }
        )
    groups = [
        {
            "code": g["code"],
            "name": g["name"],
            "min_courses": g["min_courses"],
            "members": sorted(snap.entities[m]["key"] for m in g["members"] if m in snap.entities),
        }
        for g in sorted(snap.groups.values(), key=lambda g: g["code"])
    ]
    from cfs.models import RequirementGroup

    texts = {
        g.code: g.description
        for g in db.scalars(select(RequirementGroup).where(RequirementGroup.curriculum_version_id == version_id))
    }
    for g in groups:
        g["rule_text"] = texts.get(g["code"])
    return {"curriculum_version": snap.version, "rows": rows, "groups": groups}


def _entity_in_version(snap: Snapshot, entity_id: uuid.UUID) -> str:
    eid = str(entity_id)
    if eid not in snap.entities:
        raise NotFound("Entity is not part of this curriculum version")
    return eid


@router.get("/versions/{version_id}/entities/{entity_id}", tags=["entities"])
def get_entity(
    version_id: uuid.UUID, entity_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
) -> dict[str, Any]:
    snap = version_snapshot(db, p, version_id)
    eid = _entity_in_version(snap, entity_id)
    e = snap.entities[eid]
    ev_rows = db.execute(
        select(EntityFieldEvidence.field_name, EntityFieldEvidence.stance, EntityFieldEvidence.evidence_span_id).where(
            EntityFieldEvidence.entity_revision_id == uuid.UUID(e["revision_id"])
        )
    ).all()
    field_evidence: dict[str, list[dict]] = {}
    for f, stance, sid in ev_rows:
        field_evidence.setdefault(f, []).append({"span_id": str(sid), "stance": stance.value})
    revisions = db.execute(
        select(EntityRevision.id, EntityRevision.revision_number, EntityRevision.origin, EntityRevision.created_at)
        .where(EntityRevision.entity_id == entity_id)
        .order_by(EntityRevision.revision_number)
    ).all()
    rels = [r for r in snap.relationships.values() if eid in (r["source"], r["target"])]
    result: dict[str, Any] = {
        "entity": e,
        "placements": snap.placements.get(eid, []),
        "field_evidence": field_evidence,
        "revisions": [
            {"id": str(i), "revision_number": n, "origin": o.value, "created_at": c} for i, n, o, c in revisions
        ],
        "relationships": [
            {**r, "other": snap.entities.get(r["target"] if r["source"] == eid else r["source"], {}).get("key")}
            for r in rels
        ],
        "curriculum_version": snap.version,
    }
    if e["type"] == "course":
        result["contribution"] = queries.contribution(snap, eid)
        result["requirement_rules"] = queries.evaluate_rules(snap, eid)
        result["unknowns"] = _course_unknowns(snap, eid, result)
    return result


def _course_unknowns(snap: Snapshot, eid: str, res: dict) -> list[str]:
    e = snap.entities[eid]
    out = []
    pl = snap.primary_placement(eid)
    if e["details"].get("credits") is None:
        out.append("Credit value is not documented.")
    if not pl or pl.get("year") is None:
        out.append("Year of study is not documented.")
    if not pl or pl.get("term") in (None, "unknown"):
        out.append("Term is not documented.")
    if not res["contribution"]["outcomes"]:
        out.append("No formal learning outcomes are documented (a description is not a set of outcomes).")
    if not res["contribution"]["assessments"]:
        out.append("No assessment documentation has been imported (this does not mean the course has none).")
    if not snap.rules_for(eid, "prerequisite"):
        out.append("No prerequisite statement found in assigned sources (a year-based list does not establish one).")
    return out


@router.get("/versions/{version_id}/entities/{entity_id}/prior-learning", tags=["entities"])
def get_prior(
    version_id: uuid.UUID,
    entity_id: uuid.UUID,
    pathway: str | None = None,
    depth: int = Query(default=4, ge=1, le=8),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
) -> dict[str, Any]:
    snap = version_snapshot(db, p, version_id)
    return queries.prior_learning(snap, _entity_in_version(snap, entity_id), pathway=pathway, depth=depth)


@router.get("/versions/{version_id}/entities/{entity_id}/downstream", tags=["entities"])
def get_downstream(
    version_id: uuid.UUID,
    entity_id: uuid.UUID,
    depth: int = Query(default=6, ge=1, le=10),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
) -> dict[str, Any]:
    snap = version_snapshot(db, p, version_id)
    return queries.downstream(snap, _entity_in_version(snap, entity_id), depth=depth)


@router.get("/versions/{version_id}/relationships/{relationship_key}", tags=["entities"])
def get_relationship(
    version_id: uuid.UUID,
    relationship_key: uuid.UUID,
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
) -> dict[str, Any]:
    snap = version_snapshot(db, p, version_id)
    r = snap.relationships.get(str(relationship_key))
    if r is None:
        raise NotFound("Relationship is not part of this curriculum version")
    ev = db.execute(
        select(RelationshipEvidence.evidence_span_id, RelationshipEvidence.stance, RelationshipEvidence.note).where(
            RelationshipEvidence.relationship_revision_id == uuid.UUID(r["revision_id"])
        )
    ).all()
    history = db.execute(
        select(
            RelationshipRevision.id,
            RelationshipRevision.revision_number,
            RelationshipRevision.review_state,
            RelationshipRevision.created_at,
        )
        .where(RelationshipRevision.relationship_key == relationship_key)
        .order_by(RelationshipRevision.revision_number)
    ).all()
    rule = None
    if r["rule_id"]:
        rr = db.get(RequirementRule, uuid.UUID(r["rule_id"]))
        if rr:
            from cfs.curriculum.rules import parse_expr, render

            rule = {"id": str(rr.id), "rendered": render(parse_expr(rr.expression)), "source_text": rr.source_text}
    return {
        "relationship": r,
        "meaning": MEANINGS.get(r["type"], r["type"]),
        "source": snap.entities[r["source"]],
        "target": snap.entities[r["target"]],
        "evidence": [{"span_id": str(s), "stance": st.value, "note": n} for s, st, n in ev],
        "history": [
            {"revision_id": str(i), "revision_number": n, "review_state": rs.value, "created_at": c}
            for i, n, rs, c in history
        ],
        "rule": rule,
        "curriculum_version": snap.version,
    }


MEANINGS = {
    "formal_prerequisite": (
        "Documented requisite: the source course (or an alternative) must be completed before the target."
    ),
    "corequisite": "Documented co-requisite: the courses may or must be taken in the same term.",
    "inferred_preparation": "Pedagogical preparation: the source is expected to prepare students for the target. "
    "This is an interpretation, not a formal enrolment rule.",
    "prepares_for": "Earlier learning (topic or outcome) that later learning is expected to build on.",
    "contributes_to": "A course outcome contributes to a program learning outcome.",
    "assesses": "An assessment assesses an outcome.",
    "has_outcome": "The course states this learning outcome.",
    "covers_topic": "The course covers this topic.",
    "has_assessment": "The course includes this assessment.",
    "possible_overlap": "Possible overlap in content. Overlap does not create a dependency and may be deliberate.",
    "supports": "An activity supports an outcome or topic.",
}


@router.get("/versions/{version_id}/search", tags=["entities"])
def search(
    version_id: uuid.UUID,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
) -> dict[str, Any]:
    snap = version_snapshot(db, p, version_id)
    ql = q.lower()
    ents = [
        {"id": e["id"], "type": e["type"], "key": e["key"], "title": e["title"]}
        for e in snap.entities.values()
        if ql in e["key"].lower() or ql in e["title"].lower() or ql in (e["description"] or "").lower()
    ]
    ents.sort(key=lambda e: (e["type"] != "course", e["key"]))
    # full-text search across documents assigned to this version (PostgreSQL FTS)
    from cfs.models import DocumentChunk

    tsq = func.websearch_to_tsquery("english", q)
    chunk_rows = db.execute(
        select(
            DocumentChunk.id,
            DocumentChunk.document_version_id,
            DocumentChunk.page_start,
            DocumentChunk.section_title,
            func.ts_headline("english", DocumentChunk.text, tsq),
            func.ts_rank(DocumentChunk.tsv, tsq).label("rank"),
        )
        .join(CurriculumSource, CurriculumSource.document_version_id == DocumentChunk.document_version_id)
        .where(
            CurriculumSource.curriculum_version_id == version_id,
            DocumentChunk.organization_id == p.org_id,
            DocumentChunk.tsv.op("@@")(tsq),
        )
        .order_by(func.ts_rank(DocumentChunk.tsv, tsq).desc())
        .limit(limit)
    ).all()
    return {
        "entities": ents[:limit],
        "documents": [
            {
                "chunk_id": str(c),
                "document_version_id": str(dv),
                "page": pg,
                "section": s,
                "headline": h,
                "rank": float(rk),
            }
            for c, dv, pg, s, h, rk in chunk_rows
        ],
        "retrieval_mode": "full_text_only (embeddings not configured)",
    }


@router.get("/evidence/{span_id}", response_model=SpanOut, tags=["evidence"])
def get_span(span_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    span = get_scoped(db, EvidenceSpan, span_id, p, "Evidence span")
    dv = db.get(DocumentVersion, span.document_version_id)
    doc = db.get(Document, dv.document_id)
    out = SpanOut.model_validate(span)
    out.document_title, out.document_type = doc.title, doc.document_type.value
    out.publication_date, out.academic_year, out.is_synthetic = dv.publication_date, dv.academic_year, dv.is_synthetic
    return out


@router.post("/evidence/batch", response_model=list[SpanOut], tags=["evidence"])
def get_spans(ids: list[uuid.UUID], db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    return [get_span(i, db, p) for i in ids[:200]]


# ── saved views (visual state only) ──


@router.get("/versions/{version_id}/views", response_model=list[SavedViewOut], tags=["views"])
def list_views(version_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    return db.scalars(
        select(SavedView).where(SavedView.curriculum_version_id == version_id, SavedView.user_id == p.user_id)
    ).all()


@router.put("/versions/{version_id}/views", response_model=SavedViewOut, tags=["views"])
def save_view(
    version_id: uuid.UUID, body: SavedViewIn, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    get_scoped(db, CurriculumVersion, version_id, p, "Curriculum version")
    from cfs.models.base import utcnow

    v = db.scalar(
        select(SavedView).where(
            SavedView.curriculum_version_id == version_id, SavedView.user_id == p.user_id, SavedView.name == body.name
        )
    )
    if v is None:
        v = SavedView(organization_id=p.org_id, user_id=p.user_id, curriculum_version_id=version_id, name=body.name)
        db.add(v)
    for k in ("scenario_id", "viewport", "positions", "filters", "layers"):
        setattr(v, k, getattr(body, k))
    v.updated_at = utcnow()
    db.commit()
    return v


@router.get("/entities/{entity_id}/versions", tags=["entities"])
def entity_across_versions(
    entity_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
) -> list[dict[str, Any]]:
    """Stable identity across curriculum versions: which exact revision each version uses."""
    e = get_scoped(db, Entity, entity_id, p, "Entity")
    from cfs.models import CurriculumEntityMembership

    rows = db.execute(
        select(
            CurriculumVersion.id,
            CurriculumVersion.label,
            CurriculumVersion.status,
            EntityRevision.id,
            EntityRevision.revision_number,
            EntityRevision.title,
        )
        .join(CurriculumEntityMembership, CurriculumEntityMembership.curriculum_version_id == CurriculumVersion.id)
        .join(EntityRevision, EntityRevision.id == CurriculumEntityMembership.entity_revision_id)
        .where(CurriculumEntityMembership.entity_id == e.id)
        .order_by(CurriculumVersion.created_at)
    ).all()
    return [
        {
            "version_id": str(v),
            "label": lbl,
            "status": st.value,
            "revision_id": str(r),
            "revision_number": n,
            "title": t,
        }
        for v, lbl, st, r, n, t in rows
    ]


@router.get("/versions/{version_id}/entities", tags=["entities"])
def list_entities(
    version_id: uuid.UUID, type: str | None = None, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
) -> list[dict[str, Any]]:
    snap = version_snapshot(db, p, version_id)
    return sorted(
        (
            {"id": e["id"], "type": e["type"], "key": e["key"], "title": e["title"]}
            for e in snap.entities.values()
            if type is None or e["type"] == type
        ),
        key=lambda e: (e["type"], e["key"]),
    )
