"""Identity resolution: compare validated candidates with a curriculum version and raise review items.

Rules:
- Nothing is written to the curriculum directly. New entities/rules become review items; acceptance
  materializes them (cfs.reviews).
- Matching documented facts attach evidence to the existing revision (entity_field_evidence).
- Disagreements are recorded as conflicts with both sides kept. Upload order never decides.
"""

from __future__ import annotations

import difflib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.curriculum.rules import parse_expr, render
from cfs.ingest.pipeline import review_item
from cfs.models import (
    AssessmentRevisionDetails,
    CoursePlacement,
    CourseRevisionDetails,
    CurriculumEntityMembership,
    CurriculumRelationshipMembership,
    CurriculumSource,
    CurriculumVersion,
    DocumentVersion,
    Entity,
    EntityFieldEvidence,
    EntityRevision,
    RelationshipEvidence,
    RelationshipRevision,
    RequirementRule,
)
from cfs.models.enums import EntityType, RelType, ReviewItemKind, RuleKind, VersionStatus


def _member(db: Session, cv_id: uuid.UUID, org_id: uuid.UUID, etype: EntityType, key: str):
    row = db.execute(
        select(Entity, EntityRevision)
        .join(CurriculumEntityMembership, CurriculumEntityMembership.entity_id == Entity.id)
        .join(EntityRevision, EntityRevision.id == CurriculumEntityMembership.entity_revision_id)
        .where(
            CurriculumEntityMembership.curriculum_version_id == cv_id,
            Entity.organization_id == org_id,
            Entity.entity_type == etype,
            Entity.stable_key == key,
        )
    ).first()
    return row if row else (None, None)


def _attach(db: Session, rev: EntityRevision, field: str, span_id: str | None) -> None:
    if not span_id:
        return
    sid = uuid.UUID(span_id)
    if not db.scalar(
        select(EntityFieldEvidence.id).where(
            EntityFieldEvidence.entity_revision_id == rev.id,
            EntityFieldEvidence.field_name == field,
            EntityFieldEvidence.evidence_span_id == sid,
        )
    ):
        db.add(EntityFieldEvidence(entity_revision_id=rev.id, field_name=field, evidence_span_id=sid))


def _attach_rel(
    db: Session,
    cv_id: uuid.UUID,
    span_id: str | None,
    *,
    rel_types: set[RelType],
    source: uuid.UUID | None = None,
    target: uuid.UUID | None = None,
) -> int:
    """Attach a span as supporting evidence to matching relationship revisions in this version."""
    if not span_id:
        return 0
    q = (
        select(RelationshipRevision)
        .join(
            CurriculumRelationshipMembership,
            CurriculumRelationshipMembership.relationship_revision_id == RelationshipRevision.id,
        )
        .where(
            CurriculumRelationshipMembership.curriculum_version_id == cv_id,
            RelationshipRevision.rel_type.in_(rel_types),
        )
    )
    if source is not None:
        q = q.where(RelationshipRevision.source_entity_id == source)
    if target is not None:
        q = q.where(RelationshipRevision.target_entity_id == target)
    n = 0
    sid = uuid.UUID(span_id)
    for rel in db.scalars(q):
        if not db.scalar(
            select(RelationshipEvidence.id).where(
                RelationshipEvidence.relationship_revision_id == rel.id, RelationshipEvidence.evidence_span_id == sid
            )
        ):
            db.add(RelationshipEvidence(relationship_revision_id=rel.id, evidence_span_id=sid))
            n += 1
    return n


AUTHORITY_RANK = {"authoritative": 3, "supporting": 2, "historical_reference": 1, "exploratory": 0}


def _source_ref(dv: DocumentVersion, src: CurriculumSource, c: dict) -> dict:
    return {
        "document_version_id": str(dv.id),
        "authority": src.authority.value,
        "origin": c.get("origin", "deterministic"),
        "span_id": c.get("span_id"),
    }


def _open_item(db: Session, cv: uuid.UUID, key_prefix: str):
    from cfs.models import ReviewItem
    from cfs.models.enums import ReviewItemStatus

    return db.scalar(
        select(ReviewItem)
        .where(
            ReviewItem.curriculum_version_id == cv,
            ReviewItem.dedupe_key.like(f"{key_prefix}{cv}:%"),
            ReviewItem.status.in_([ReviewItemStatus.open, ReviewItemStatus.deferred]),
        )
        .order_by(ReviewItem.created_at)
    )


def _merge_open(
    db: Session, cv: uuid.UUID, key_prefix: str, c: dict, src: CurriculumSource, dv: DocumentVersion
) -> bool:
    """Fold a candidate from another document (or extractor) into the open review item for the same thing.

    Higher-authority sources win field values; disagreements are kept in `field_conflicts` for the reviewer.
    """
    it = _open_item(db, cv, key_prefix)
    if it is None:
        return False
    p = dict(it.payload)
    sources = list(p.get("sources", []))
    if any(
        x["document_version_id"] == str(dv.id) and x.get("origin") == c.get("origin", "deterministic") for x in sources
    ):
        return True  # same document and extractor already merged (idempotent re-run)
    new_rank = AUTHORITY_RANK.get(src.authority.value, 0)
    old_rank = max(
        (AUTHORITY_RANK.get(x["authority"], 0) for x in sources),
        default=AUTHORITY_RANK.get(p.get("authority", "exploratory"), 0),
    )
    sources.append(_source_ref(dv, src, c))
    p["sources"] = sources
    spans = [x for x in [c.get("span_id")] + [f.get("span_id") for f in c.get("fields", {}).values()] if x]
    if c["kind"] == "course":
        if (
            c.get("title")
            and c["title"] != c["code"]
            and (p.get("title") in (None, p.get("code")) or (c.get("origin") == "model" and new_rank >= old_rank))
        ):
            p["title"] = c["title"]
        fields = dict(p.get("fields", {}))
        conflicts = list(p.get("field_conflicts", []))
        for name, f in c.get("fields", {}).items():
            cur = fields.get(name)
            if cur is None:
                fields[name] = {**f, "authority": src.authority.value}
            elif str(cur.get("value")).lower() != str(f.get("value")).lower():
                conflicts.append(
                    {
                        "field": name,
                        "kept": cur.get("value"),
                        "other": f.get("value"),
                        "other_document_version_id": str(dv.id),
                        "other_authority": src.authority.value,
                    }
                )
                if new_rank > old_rank:
                    conflicts[-1].update({"kept": f.get("value"), "other": cur.get("value")})
                    fields[name] = {**f, "authority": src.authority.value}
        p["fields"] = fields
        p["field_conflicts"] = conflicts
        it.title = f"New course found: {p['code']} {p.get('title') or ''}".strip()
    elif c["kind"] == "option_group":
        p["members"] = sorted(set(p.get("members", [])) | set(c.get("members", [])))
    it.payload = p
    it.evidence_span_ids = list(dict.fromkeys(list(it.evidence_span_ids) + [uuid.UUID(x) for x in spans]))
    return True


def _close_mentions(db: Session, cv: uuid.UUID, code: str, dv: DocumentVersion) -> None:
    from cfs.models import ReviewDecision, ReviewItem
    from cfs.models.enums import ReviewItemKind, ReviewItemStatus

    for it in db.scalars(
        select(ReviewItem).where(
            ReviewItem.curriculum_version_id == cv,
            ReviewItem.kind == ReviewItemKind.uncertain_course_match,
            ReviewItem.status == ReviewItemStatus.open,
            ReviewItem.dedupe_key.like(f"mention:{code}:{cv}:%"),
        )
    ):
        it.status = ReviewItemStatus.accepted
        db.add(
            ReviewDecision(
                review_item_id=it.id,
                decision=ReviewItemStatus.accepted,
                rationale=f"Superseded automatically: a course candidate for {code} was extracted "
                f"from document version {dv.id}.",
            )
        )


def _slug(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80]


def _canon(expr: dict) -> str:
    return render(parse_expr(expr))


def resolve_candidates(
    db: Session, dv: DocumentVersion, src: CurriculumSource, candidates: list[dict[str, Any]]
) -> dict[str, int]:
    org, cv = dv.organization_id, src.curriculum_version_id
    stats = {"matched": 0, "new_items": 0, "conflicts": 0, "evidence_attached": 0}
    base = {"org_id": org, "curriculum_version_id": cv, "document_version_id": dv.id}
    known_codes = set(
        db.scalars(
            select(Entity.stable_key).where(Entity.organization_id == org, Entity.entity_type == EntityType.course)
        )
    )

    cand_ctx: dict = {}

    def item(
        kind: ReviewItemKind,
        title: str,
        key: str,
        payload: dict,
        span_ids: list[str | None],
        detail: str | None = None,
        subject: uuid.UUID | None = None,
    ) -> None:
        created = review_item(
            db,
            kind=kind,
            title=title,
            dedupe_key=f"{key}:{cv}:{dv.id}",
            payload={**payload, "origin": cand_ctx.get("origin", "deterministic")},
            detail=(detail or "")
            + (
                " Proposed by the extraction model; its quote was verified against the source text."
                if cand_ctx.get("origin") == "model"
                else ""
            ),
            evidence_span_ids=[uuid.UUID(s) for s in span_ids if s],
            subject_entity_id=subject,
            model_run_id=uuid.UUID(cand_ctx["model_run_id"]) if cand_ctx.get("model_run_id") else None,
            **base,
        )
        if created:
            stats["new_items"] += 1
            if kind == ReviewItemKind.conflicting_requirement:
                stats["conflicts"] += 1

    for c in candidates:
        cand_ctx.clear()
        cand_ctx.update(c)
        if c["kind"] == "course":
            ent, rev = _member(db, cv, org, EntityType.course, c["code"])
            if ent is None and _merge_open(db, cv, f"course:{c['code']}:", c, src, dv):
                stats["merged"] = stats.get("merged", 0) + 1
                continue
            if ent is None:
                _close_mentions(db, cv, c["code"], dv)
                item(
                    ReviewItemKind.candidate_entity,
                    f"New course found: {c['code']} {c['title']}",
                    f"course:{c['code']}",
                    {
                        "entity_type": "course",
                        **{k: c[k] for k in ("code", "title")},
                        "fields": c["fields"],
                        "span_id": c["span_id"],
                        "authority": src.authority.value,
                        "sources": [_source_ref(dv, src, c)],
                        "field_conflicts": [],
                    },
                    [c["span_id"]] + [f.get("span_id") for f in c["fields"].values()],
                    detail="Accepting adds the course to this curriculum version with only the "
                    "documented fields; unknown fields remain unknown.",
                )
                continue
            stats["matched"] += 1
            if rev.title.strip().lower() != c["title"].strip().lower():
                item(
                    ReviewItemKind.possible_duplicate,
                    f"{c['code']}: title differs between sources",
                    f"title:{c['code']}",
                    {"code": c["code"], "existing_title": rev.title, "document_title": c["title"]},
                    [c["span_id"]],
                    subject=ent.id,
                    detail="Same course code with a different title. Confirm whether this is the same course.",
                )
            else:
                _attach(db, rev, "title", c["span_id"])
                stats["evidence_attached"] += 1
            details = db.get(CourseRevisionDetails, rev.id)
            placement = db.scalar(
                select(CoursePlacement).where(
                    CoursePlacement.curriculum_version_id == cv, CoursePlacement.entity_id == ent.id
                )
            )
            for fname, f in c["fields"].items():
                current: Any = None
                if fname == "credits":
                    current = float(details.credits) if details and details.credits is not None else None
                elif fname == "description":
                    current = rev.description
                elif placement is not None and fname in ("year", "term", "classification"):
                    v = getattr(placement, fname)
                    current = v.value if hasattr(v, "value") else v
                same = (current is None and f["value"] is None) or (
                    current is not None and str(current).strip().lower() == str(f["value"]).strip().lower()
                )
                if same:
                    _attach(db, rev, fname, f.get("span_id"))
                    stats["evidence_attached"] += 1
                elif current is not None:
                    item(
                        ReviewItemKind.conflicting_requirement,
                        f"{c['code']}: {fname} differs between sources",
                        f"field:{c['code']}:{fname}",
                        {
                            "type": "field",
                            "code": c["code"],
                            "field": fname,
                            "existing": current,
                            "document": f["value"],
                        },
                        [f.get("span_id")],
                        subject=ent.id,
                        detail="Both values are kept. Select the interpretation that applies to this version.",
                    )
        elif c["kind"] == "requisite":
            ent, _ = _member(db, cv, org, EntityType.course, c["target"])
            kind = RuleKind(c["rule_kind"])
            existing = None
            if ent is not None:
                existing = db.scalar(
                    select(RequirementRule).where(
                        RequirementRule.curriculum_version_id == cv,
                        RequirementRule.target_entity_id == ent.id,
                        RequirementRule.rule_kind == kind,
                    )
                )
            if existing is None:
                item(
                    ReviewItemKind.candidate_relationship,
                    f"{kind.value.title()} for {c['target']}: {c['text']}",
                    f"rule:{c['target']}:{kind.value}:{_canon(c['expression'])}",
                    {
                        "rule_kind": kind.value,
                        "target": c["target"],
                        "expression": c["expression"],
                        "text": c["text"],
                        "span_id": c["span_id"],
                        "authority": src.authority.value,
                    },
                    [c["span_id"]],
                    subject=ent.id if ent else None,
                    detail="Parsed from documented text. OR alternatives are preserved as alternatives.",
                )
            elif _canon(existing.expression) == _canon(c["expression"]):
                stats["matched"] += 1
                version = db.get(CurriculumVersion, cv)
                if existing.evidence_span_id is None and version.status == VersionStatus.draft:
                    existing.evidence_span_id = uuid.UUID(c["span_id"])
                rt = {RelType.formal_prerequisite} if kind == RuleKind.prerequisite else {RelType.corequisite}
                stats["evidence_attached"] += _attach_rel(db, cv, c["span_id"], rel_types=rt, target=ent.id)
                if kind == RuleKind.corequisite:
                    stats["evidence_attached"] += _attach_rel(db, cv, c["span_id"], rel_types=rt, source=ent.id)
            else:
                item(
                    ReviewItemKind.conflicting_requirement,
                    f"Conflicting {kind.value} for {c['target']}",
                    f"ruleconflict:{c['target']}:{kind.value}:{_canon(c['expression'])}",
                    {
                        "type": "requirement",
                        "target": c["target"],
                        "rule_kind": kind.value,
                        "existing_rule_id": str(existing.id),
                        "existing_text": existing.source_text,
                        "existing_rendered": _canon(existing.expression),
                        "proposed_expression": c["expression"],
                        "proposed_text": c["text"],
                        "proposed_rendered": _canon(c["expression"]),
                        "document_authority": src.authority.value,
                    },
                    [c["span_id"]] + ([str(existing.evidence_span_id)] if existing.evidence_span_id else []),
                    subject=ent.id if ent else None,
                    detail="Two sources state different requirements. Both are recorded; choose which applies "
                    "to this curriculum version.",
                )
        elif c["kind"] == "outcome":
            ent, rev = _member(db, cv, org, EntityType.course_outcome, c["key"])
            if ent is None:
                item(
                    ReviewItemKind.candidate_entity,
                    f"New course outcome for {c['course']}: {c['statement'][:80]}",
                    f"outcome:{c['key']}",
                    {
                        "entity_type": "course_outcome",
                        **{k: c[k] for k in ("key", "course", "statement", "contributes")},
                        "span_id": c["span_id"],
                    },
                    [c["span_id"]],
                )
            else:
                stats["matched"] += 1
                _attach(db, rev, "statement", c["span_id"])
                stats["evidence_attached"] += 1
                stats["evidence_attached"] += _attach_rel(
                    db, cv, c["span_id"], rel_types={RelType.contributes_to}, source=ent.id
                )
                stats["evidence_attached"] += _attach_rel(
                    db, cv, c["span_id"], rel_types={RelType.has_outcome}, target=ent.id
                )
        elif c["kind"] == "assessment":
            ent, rev = _member(db, cv, org, EntityType.assessment, c["key"])
            if ent is None:
                item(
                    ReviewItemKind.candidate_entity,
                    f"New assessment for {c['course']}: {c['title']}",
                    f"assessment:{c['key']}",
                    {
                        "entity_type": "assessment",
                        **{k: c[k] for k in ("key", "course", "title", "format", "weight", "week", "assesses")},
                        "span_id": c["span_id"],
                    },
                    [c["span_id"]],
                )
                continue
            stats["matched"] += 1
            det = db.get(AssessmentRevisionDetails, rev.id)
            doc_vals = {"weight_percent": c["weight"], "timing_week": c["week"], "format": c["format"]}
            for fname, val in doc_vals.items():
                cur = getattr(det, fname) if det else None
                cur = float(cur) if fname == "weight_percent" and cur is not None else cur
                if cur == val or (cur is None and val is None):
                    _attach(db, rev, fname, c["span_id"])
                    stats["evidence_attached"] += 1
                elif cur is not None and val is not None:
                    item(
                        ReviewItemKind.conflicting_requirement,
                        f"{c['key']}: {fname} differs between sources",
                        f"field:{c['key']}:{fname}",
                        {"type": "field", "code": c["key"], "field": fname, "existing": cur, "document": val},
                        [c["span_id"]],
                        subject=ent.id,
                    )
            stats["evidence_attached"] += _attach_rel(
                db, cv, c["span_id"], rel_types={RelType.has_assessment}, target=ent.id
            )
            for okey in c.get("assesses", []):
                oent, _ = _member(db, cv, org, EntityType.course_outcome, okey)
                if oent is not None:
                    stats["evidence_attached"] += _attach_rel(
                        db, cv, c["span_id"], rel_types={RelType.assesses}, source=ent.id, target=oent.id
                    )
        elif c["kind"] == "program_outcome":
            key = f"PLO{c['number']}" if c.get("number") else _slug(c["label"])
            ent, _rev = _member(db, cv, org, EntityType.program_outcome, key)
            if ent is None:
                item(
                    ReviewItemKind.candidate_entity,
                    f"Program learning outcome: {key} {c['label']}",
                    f"plo:{key}",
                    {
                        "entity_type": "program_outcome",
                        "key": key,
                        "label": c["label"],
                        "statement": c.get("statement"),
                        "span_id": c.get("span_id"),
                    },
                    [c.get("span_id")],
                    detail=(
                        "Only a heading is documented; no full outcome statement." if not c.get("statement") else None
                    ),
                )
            else:
                stats["matched"] += 1
        elif c["kind"] == "option_group":
            code = _slug(f"{c['name']}-y{c['year']}" if c.get("year") else c["name"])
            if _merge_open(db, cv, f"group:{code}:", c, src, dv):
                stats["merged"] = stats.get("merged", 0) + 1
                continue
            item(
                ReviewItemKind.candidate_relationship,
                f"Option rule: {c['name']}"
                + (f" (Year {c['year']}, {c['slot_count']} slot(s))" if c.get("year") else ""),
                f"group:{code}",
                {
                    "type": "requirement_group",
                    "code": code,
                    "name": c["name"],
                    "year": c.get("year"),
                    "slot_count": c.get("slot_count"),
                    "members": c.get("members", []),
                    "rule_text": c["quote"],
                    "span_id": c.get("span_id"),
                },
                [c.get("span_id")],
                detail="Elective/option rule as documented. Members not in this version are kept as text only.",
            )
        elif c["kind"] == "preparation":
            tgt, _ = _member(db, cv, org, EntityType.course, c["target"])
            sources = [s for s in c.get("sources", []) if s != c["target"]]
            item(
                ReviewItemKind.inferred_mapping,
                f"Stated preparation for {c['target']}: {c['described'][:80]}",
                f"prep:{c['target']}:{','.join(sorted(sources))}",
                {
                    "proposal": "inferred_preparation",
                    "target": c["target"],
                    "sources": sources,
                    "described": c["described"],
                    "quote": c["quote"],
                    "span_id": c.get("span_id"),
                },
                [c.get("span_id")],
                subject=tgt.id if tgt else None,
                detail="The source describes expected earlier learning without naming a formal prerequisite. "
                "Which courses provide it is an interpretation; accepting keeps it labelled inferred.",
            )
        elif c["kind"] == "mention":
            if c["code"] in known_codes or _open_item(db, cv, f"course:{c['code']}:"):
                continue
            similar = difflib.get_close_matches(c["code"], list(known_codes), n=3, cutoff=0.8)
            item(
                ReviewItemKind.uncertain_course_match,
                f"Unrecognized course code {c['code']}",
                f"mention:{c['code']}",
                {"code": c["code"], "quote": c["quote"], "similar": similar},
                [c.get("span_id")],
                detail="Mentioned in the document but not described there or known to "
                "this workspace. It may be a typo, an external course, or a historical course.",
            )
    db.flush()
    return stats
