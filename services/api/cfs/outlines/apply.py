"""Apply a reviewed selection of an outline-import plan to the DRAFT version in one transaction, and revert it.

Order (all under a row lock on the version):
  1. check: import ready, course state unchanged since planning (plan_stale), ETag (plan_changed), selection closed
     under depends_on / excludes
  2. record the outline's academic year on the document if it had none; assign the document as a source of the
     version for this course (never upgrading an existing assignment; no resolve job)
  3. course description / credits  → one new course revision (revise_member)
  4. retire / detach               → memberships removed from this draft only, with every link touching them
  5. replace                       → new revision of the SAME entity (key and links persist) + outline evidence
  6. keep (not yet citing)         → copy-on-write revision carrying the outline as evidence
  7. add                           → new entity (org-unique key), revision, membership, containment link
  8. links                         → stated: documented (explicit, accepted); inferred: accepted interpretation
  9. inbox items this document raised for the course → closed with a reason
 10. integrity: no link may point at a removed element; missing pieces recomputed
Evidence is never attached to a revision another version shares (copy-on-write), so applying and reverting leave
every other version, including published ones, byte-for-byte unchanged.

The undo log is an ordered list of {kind, id, before, after}; revert restores every `before` after checking that
each `after` is still the current state (409 revert_conflict otherwise).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.core.audit import audit
from cfs.core.auth import Principal
from cfs.core.errors import AppError, Conflict, NotFound
from cfs.curriculum.rules import normalize_code
from cfs.curriculum.service import (
    add_field_evidence,
    allocate_key,
    assert_draft,
    create_relationship,
    create_revision,
    drop_memberships,
    get_or_create_entity,
    membership,
    revise_member,
    revise_relationship,
    set_membership,
)
from cfs.documents import assign_source
from cfs.graph.snapshot import load_snapshot
from cfs.models import (
    CoursePlacement,
    CurriculumEntityMembership,
    CurriculumRelationshipMembership,
    CurriculumSource,
    DocumentVersion,
    Entity,
    OutlineImport,
    RelationshipRevision,
    ReviewDecision,
    ReviewItem,
)
from cfs.models.base import utcnow
from cfs.models.enums import (
    ContributionLevel,
    EntityType,
    EvidenceBasis,
    EvidenceStance,
    Origin,
    OutlineImportStatus,
    RelType,
    ReviewItemKind,
    ReviewItemStatus,
    ReviewState,
    Role,
    SourceAuthority,
)
from cfs.outlines.edition import parse_academic_year, year_label
from cfs.outlines.missing import course_missing
from cfs.outlines.plan import FIELD_OF, etag, key_prefix
from cfs.outlines.project import CONTAINMENT_FOR, detach_scope, preview
from cfs.outlines.state import course_members, course_state_hash
from cfs.outlines.verify import canon

OPEN = (ReviewItemStatus.open, ReviewItemStatus.deferred)
RECORDABLE = ("entered", "document", "extraction", "page 1")  # years read from the document or entered by a person


def _uuid(v: Any) -> uuid.UUID | None:
    return uuid.UUID(str(v)) if v else None


def _span(it: dict[str, Any]) -> uuid.UUID | None:
    for ev in it.get("evidence") or []:
        if ev.get("span_id"):
            return uuid.UUID(ev["span_id"])
    return None


def lock_import(db: Session, p: Principal, import_id: uuid.UUID) -> OutlineImport:
    imp = db.scalar(
        select(OutlineImport)
        .where(OutlineImport.id == import_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if imp is None or imp.organization_id != p.org_id:
        raise NotFound("Outline import not found")
    return imp


def validate_selection(
    plan: dict[str, Any], selected: list[str], edits: dict[str, dict[str, Any]]
) -> tuple[set[str], dict[str, dict[str, Any]]]:
    items = {i["id"]: i for i in plan["items"]}
    sel = set(selected)
    if unknown := sorted(x for x in sel if x not in items):
        raise AppError("Unknown plan items", code="unknown_items", status=422, details={"items": unknown})
    if blocked := sorted(x for x in sel if not items[x]["selectable"]):
        raise AppError("Some items can't be applied", code="not_selectable", status=422, details={"items": blocked})
    for x in sorted(sel):
        if missing := [d for d in items[x]["depends_on"] if d not in sel]:
            raise AppError(
                f"“{items[x]['title']}” needs “{items[missing[0]]['title']}”, which isn't selected.",
                code="dependency_missing",
                status=422,
                details={"item": x, "requires": missing},
            )
        if clash := [e for e in items[x]["excludes"] if e in sel]:
            raise AppError(
                f"“{items[x]['title']}” and “{items[clash[0]]['title']}” can't both be applied.",
                code="excluded_pair",
                status=422,
                details={"item": x, "excludes": clash},
            )
    clean: dict[str, dict[str, Any]] = {}
    for iid, ed in (edits or {}).items():
        if iid not in sel or items[iid]["action"] != "link" or not isinstance(ed, dict):
            continue
        out: dict[str, Any] = {}
        if ed.get("strength") is not None and items[iid]["link"]["basis"] != "stated":
            s = float(ed["strength"])
            if not 0 <= s <= 1:
                raise AppError("strength must be between 0 and 1", code="invalid_strength", status=422)
            out["strength"] = round(s, 3)
        L = items[iid]["link"]
        if "level" in ed and L["rel_type"] == "contributes_to" and L["basis"] != "stated":
            if ed["level"] not in (None, "", "introduce", "reinforce", "assess"):
                raise AppError("level must be introduce, reinforce or assess", code="invalid_level", status=422)
            out["level"] = ed["level"] or None
        if out:
            clean[iid] = out
    return sel, clean


class _Writer:
    """Writes with an undo log."""

    def __init__(self, db: Session, p: Principal, cv_id: uuid.UUID):
        self.db, self.p, self.cv = db, p, cv_id
        self.undo: list[dict[str, Any]] = []

    def log(self, kind: str, id_: Any, before: Any, after: Any, item: str | None = None) -> None:
        self.undo.append(
            {
                "kind": kind,
                "id": str(id_),
                "before": None if before is None else str(before),
                "after": None if after is None else str(after),
                "item": item,
            }
        )

    def revise(self, ent: Entity, item: str | None, **kw: Any):
        pls = {
            pl.id: pl.entity_revision_id
            for pl in self.db.scalars(
                select(CoursePlacement).where(
                    CoursePlacement.curriculum_version_id == self.cv, CoursePlacement.entity_id == ent.id
                )
            )
        }
        old, new = revise_member(self.db, self.cv, ent, created_by=self.p.user_id, **kw)
        self.log("entity", ent.id, old.id, new.id, item)
        for pid, before in pls.items():
            self.log("placement", pid, before, new.id, item)
        return new

    def contained_link(self, course_id: uuid.UUID, eid: uuid.UUID, rtype: str) -> RelationshipRevision | None:
        return self.db.scalar(
            select(RelationshipRevision)
            .join(
                CurriculumRelationshipMembership,
                CurriculumRelationshipMembership.relationship_revision_id == RelationshipRevision.id,
            )
            .where(
                CurriculumRelationshipMembership.curriculum_version_id == self.cv,
                RelationshipRevision.source_entity_id == course_id,
                RelationshipRevision.target_entity_id == eid,
                RelationshipRevision.rel_type == RelType(rtype),
            )
        )

    def cite_on_containment(self, course_id: uuid.UUID, eid: uuid.UUID, rtype: str, span: uuid.UUID | None, item: str):
        """Copy-on-write the course → element link with the outline span as supporting evidence."""
        cur = self.contained_link(course_id, eid, rtype)
        if cur is None or span is None:
            return
        new = revise_relationship(
            self.db,
            self.cv,
            cur,
            created_by=self.p.user_id,
            add_evidence=[(span, EvidenceStance.supporting)],
        )
        self.log("relationship", cur.relationship_key, cur.id, new.id, item)

    def link(self, item: str, *args: Any, **kw: Any) -> RelationshipRevision:
        rel = create_relationship(self.db, self.p.org_id, self.cv, *args, created_by=self.p.user_id, **kw)
        self.log("relationship", rel.relationship_key, None, rel.id, item)
        return rel


def _dangling(snap) -> set[str]:
    return {
        k for k, r in snap.relationships.items() if r["source"] not in snap.entities or r["target"] not in snap.entities
    }


def apply_import(
    db: Session,
    p: Principal,
    import_id: uuid.UUID,
    *,
    selected: list[str],
    edits: dict[str, dict[str, Any]] | None,
    if_match: str | None,
) -> OutlineImport:
    p.require(Role.editor)
    if not if_match:
        raise AppError(
            "Send the plan's ETag in an If-Match header to apply it.", code="precondition_required", status=428
        )
    imp = lock_import(db, p, import_id)
    cv = assert_draft(db, imp.curriculum_version_id, lock=True)
    if imp.status == OutlineImportStatus.applied:
        raise Conflict("This import has already been applied.", code="already_applied")
    if imp.status != OutlineImportStatus.ready:
        raise Conflict(f"This import is {imp.status.value}, not ready to apply.", code="not_ready")
    snap = load_snapshot(db, cv.id)
    cid = str(imp.course_entity_id)
    dv = db.get(DocumentVersion, imp.document_version_id)
    if cid not in snap.entities or course_state_hash(snap, cid, extra=(dv.academic_year,)) != imp.state_hash:
        raise Conflict(
            "The course changed after this plan was made. Re-run the import to plan against the current version.",
            code="plan_stale",
        )
    if if_match.strip().strip('"') != etag(imp.state_hash, imp.plan_hash):
        raise Conflict("The plan was re-run since you loaded it. Reload it and review again.", code="plan_changed")
    plan = imp.plan
    sel, edits = validate_selection(plan, selected, edits or {})
    items = [i for i in plan["items"] if i["id"] in sel and not i["noop"]]
    by_action: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_action.setdefault(it["action"], []).append(it)

    w = _Writer(db, p, cv.id)
    course = db.get(Entity, imp.course_entity_id)
    code = course.stable_key
    run_id = _uuid((plan.get("agent") or {}).get("model_run_id"))
    dangling_before = _dangling(snap)

    # 2. the document: academic year (if it had none) and source assignment
    outline = (plan.get("edition") or {}).get("outline") or {}
    if not dv.academic_year and outline.get("start_year") is not None and outline.get("source") in RECORDABLE:
        w.log("document_year", dv.id, None, year_label(outline["start_year"]))
        dv.academic_year = year_label(outline["start_year"])
        for ri in db.scalars(
            select(ReviewItem).where(
                ReviewItem.document_version_id == dv.id,
                ReviewItem.kind == ReviewItemKind.missing_academic_year,
                ReviewItem.status.in_(OPEN),
            )
        ):
            _close(
                db, p, w, ri, ReviewItemStatus.accepted, f"Academic year set to {dv.academic_year} by outline import."
            )
    if not db.scalar(
        select(CurriculumSource.id).where(
            CurriculumSource.document_version_id == dv.id, CurriculumSource.curriculum_version_id == cv.id
        )
    ):
        dy, vy = parse_academic_year(dv.academic_year), parse_academic_year(cv.academic_year)
        mismatch = dy is not None and vy is not None and dy != vy
        src, _ = assign_source(
            db,
            p,
            dv,
            cv,
            authority=SourceAuthority.supporting if mismatch else SourceAuthority.authoritative,
            applicability=code,
            notes=f"Course outline for {code}, imported and reviewed through an outline import.",
            enqueue_resolve=False,
        )
        w.log("source", src.id, None, "present")

    # 3. course description / credits
    if fields := by_action.get("set_field"):
        kw: dict[str, Any] = {}
        details: dict[str, Any] = {}
        drop, ev = [], []
        for it in fields:
            f, v = it["proposed"]["field"], it["proposed"]["value"]
            if f == "description":
                kw["description"] = v
            else:
                details["credits"] = v
            drop.append(f)
            if sp := _span(it):
                ev.append((f, sp))
        w.revise(course, "course", details=details or None, drop_evidence_fields=drop, add_evidence=ev, **kw)

    # 4. retire / detach (scope from the locked snapshot)
    for it in [*by_action.get("retire", []), *by_action.get("detach", [])]:
        eid = it["target"]["entity_id"]
        if eid not in snap.entities:
            continue
        scope = detach_scope(snap, cid, eid)
        for u in drop_memberships(
            db,
            cv.id,
            entity_ids=[uuid.UUID(eid)] if scope["remove_entity"] else [],
            relationship_keys=[uuid.UUID(k) for k in scope["keys"]],
        ):
            w.log(u["kind"], u["id"], u["before"], None, it["id"])

    # 5. replace
    for it in by_action.get("replace", []):
        ent = db.get(Entity, uuid.UUID(it["target"]["entity_id"]))
        pr = it["proposed"]
        changed = [d["field"] for d in it["diff"]] or [FIELD_OF[pr["type"]]]
        sp = _span(it)
        w.revise(
            ent,
            it["id"],
            title=pr["title"],
            details={k: v for k, v in pr["details"].items() if v is not None},
            origin=Origin.ai_inferred,
            drop_evidence_fields=changed,
            add_evidence=[(f, sp) for f in changed] if sp else [],
        )
        w.cite_on_containment(course.id, ent.id, CONTAINMENT_FOR[pr["type"]], sp, it["id"])

    # 6. keep (cite this outline)
    for it in by_action.get("keep", []):
        ent = db.get(Entity, uuid.UUID(it["target"]["entity_id"]))
        sp = _span(it)
        if sp is None:
            continue
        et = it["target"]["type"]
        w.revise(ent, it["id"], add_evidence=[(FIELD_OF[et], sp)])
        w.cite_on_containment(course.id, ent.id, CONTAINMENT_FOR[et], sp, it["id"])

    # 7. add: a new member (an entity outside this version with identical text keeps its identity)
    members = {i for ids in course_members(snap, cid).values() for i in ids}
    new_ids: dict[str, uuid.UUID] = {}
    taken: set[str] = set()
    for it in by_action.get("add", []):
        pr = it["proposed"]
        etype = EntityType(pr["type"])
        prefix, numbered = key_prefix(code, pr["type"], pr["title"])
        key, reuse = allocate_key(
            db, p.org_id, etype, prefix=prefix, numbered=numbered, text=pr["title"], version_id=cv.id, taken=taken
        )
        taken.add(key)
        ent = reuse or get_or_create_entity(db, p.org_id, etype, key)
        if membership(db, cv.id, ent.id) is not None:
            raise AppError(f"{key} is already part of this version.", code="integrity_error", status=500)
        new_ids[it["id"]] = ent.id
        sp = _span(it)
        rev = create_revision(
            db,
            ent,
            title=pr["title"],
            origin=Origin.ai_inferred,
            details={k: v for k, v in pr["details"].items() if v is not None},
            created_by=p.user_id,
        )
        if sp:
            add_field_evidence(db, rev, FIELD_OF[pr["type"]], sp)
            for f in ("format", "weight_percent", "timing_week", "timing_text"):
                if pr["details"].get(f) is not None:
                    add_field_evidence(db, rev, f, sp)
        set_membership(db, cv.id, ent, rev)
        w.log("entity", ent.id, None, rev.id, it["id"])
        w.link(
            it["id"],
            course.id,
            ent.id,
            RelType(CONTAINMENT_FOR[pr["type"]]),
            origin=Origin.ai_inferred,
            basis=EvidenceBasis.explicit_statement,
            review=ReviewState.accepted,
            evidence=[(sp, EvidenceStance.supporting)] if sp else [],
        )

    # 8. links
    add_item_of = {i["id"].split(":", 1)[1]: i["id"] for i in plan["items"] if i["action"] == "add"}
    for it in by_action.get("link", []):
        L = it["link"]
        ends = []
        for end in (L["source"], L["target"]):
            ends.append(_uuid(end.get("entity_id")) or new_ids.get(add_item_of.get(end.get("ref") or "", "")))
        if None in ends:
            raise AppError(
                f"“{it['title']}” needs an element that wasn't added.", code="dependency_missing", status=422
            )
        ed = edits.get(it["id"]) or {}
        stated = L["basis"] == "stated"
        level = ed.get("level", L.get("level"))
        sp = _span(it)
        quote = (it.get("evidence") or [{}])[0].get("quote")
        w.link(
            it["id"],
            ends[0],
            ends[1],
            RelType(L["rel_type"]),
            origin=Origin.ai_inferred,
            basis=EvidenceBasis.explicit_statement if stated else EvidenceBasis.interpretation,
            review=ReviewState.accepted,
            level=ContributionLevel(level) if level else None,
            rationale=(f"{L.get('rationale') or ''} — “{quote}”" if quote and not stated else L.get("rationale"))
            or None,
            evidence=[(sp, EvidenceStance.supporting)] if sp else [],
            strength=None if stated else ed.get("strength", L.get("strength")),
            strength_source=None if stated else ("reviewer" if "strength" in ed else "ai_estimate"),
            model_run_id=run_id,
        )

    # 9. inbox items this document raised for the course, where the plan decided the same question
    by_text: dict[str, dict[str, Any]] = {}
    for i in plan["items"]:
        if i["action"] in ("add", "replace", "keep"):
            src = i.get("target") if i["action"] == "keep" else i.get("proposed")
            t = (src or {}).get("label" if i["action"] == "keep" else "title")
            if t:
                by_text.setdefault(canon(t), i)
    replace_fields = {
        i["target"]["entity_id"]: ({d["field"] for d in i["diff"]}, i["id"] in sel)
        for i in plan["items"]
        if i["action"] == "replace"
    }
    fields_set = {i["proposed"]["field"] for i in items if i["action"] == "set_field"}
    retired = {i["target"]["entity_id"] for i in items if i["action"] == "retire"}  # detached ones stay in the version
    member_uuids = {uuid.UUID(m) for m in members}
    for ri in db.scalars(
        select(ReviewItem)
        .where(
            ReviewItem.curriculum_version_id == cv.id,
            ReviewItem.document_version_id == dv.id,
            ReviewItem.status.in_(OPEN),
        )
        .with_for_update()
    ):
        pl = ri.payload or {}
        if (
            ri.kind == ReviewItemKind.candidate_entity
            and pl.get("entity_type") in ("course_outcome", "assessment", "topic")
            and normalize_code(str(pl.get("course") or "")) == normalize_code(code)
        ):
            match = by_text.get(canon(pl.get("statement") or pl.get("title")))
            if match is None:
                continue  # the plan never saw it: stays in the inbox
            ok = match["noop"] or match["id"] in sel
            why = (
                "the program already has it" if match["noop"] else ("it was applied" if ok else "the plan left it out")
            )
        elif ri.kind == ReviewItemKind.conflicting_requirement and str(ri.subject_entity_id) in retired:
            ok, why = False, "the element was retired from this version by the outline import"
        elif ri.kind == ReviewItemKind.conflicting_requirement and ri.subject_entity_id in member_uuids:
            fields, applied = replace_fields.get(str(ri.subject_entity_id), (set(), False))
            if pl.get("field") not in fields:
                continue
            ok = applied
            why = "the outline's value was applied" if ok else "the recorded value was kept"
        elif (
            ri.kind == ReviewItemKind.conflicting_requirement
            and ri.subject_entity_id == course.id
            and pl.get("field") in fields_set
        ):
            ok, why = True, "the outline's value was applied"
        else:
            continue
        _close(
            db,
            p,
            w,
            ri,
            ReviewItemStatus.accepted if ok else ReviewItemStatus.rejected,
            f"Resolved by outline import {imp.id} for {code}: {why}.",
        )

    # 10. integrity and result
    db.flush()
    after = load_snapshot(db, cv.id)
    if bad := _dangling(after) - dangling_before:
        raise AppError(
            "Applying would leave links pointing at removed elements; nothing was changed.",
            code="integrity_error",
            status=500,
            details={"relationships": sorted(bad)[:20]},
        )
    missing_after = course_missing(after, cid)
    summary = preview(snap, plan, sel, str(imp.id), edits)
    counts = {a: len(v) for a, v in by_action.items()}
    imp.status = OutlineImportStatus.applied
    imp.applied_by, imp.applied_at, imp.updated_at = p.user_id, utcnow(), utcnow()
    imp.result = {
        "applied": sorted(sel),
        "edits": edits,
        "counts": counts,
        "new_entities": {k: str(v) for k, v in new_ids.items()},
        "missing_after": missing_after["items"],
        "program_after": missing_after["program"],
        "summary": {**summary, "missing_after": len(missing_after["items"])},
        "undo": {"v": 1, "ops": w.undo},
    }
    audit(
        db,
        p,
        "outline_import.applied",
        "outline_import",
        imp.id,
        {"course": code, "curriculum_version_id": str(cv.id), "items": len(sel), "counts": counts},
    )
    return imp


def _close(db: Session, p: Principal, w: _Writer, ri: ReviewItem, status: ReviewItemStatus, why: str) -> None:
    w.log("review_item", ri.id, ri.status.value, status.value)
    ri.status = status
    db.add(ReviewDecision(review_item_id=ri.id, decision=status, rationale=why, decided_by=p.user_id))


def _current(db: Session, cv: uuid.UUID, kind: str, id_: str) -> str | None:
    if kind == "entity":
        m = membership(db, cv, uuid.UUID(id_))
        return str(m.entity_revision_id) if m else None
    if kind == "relationship":
        m = db.scalar(
            select(CurriculumRelationshipMembership).where(
                CurriculumRelationshipMembership.curriculum_version_id == cv,
                CurriculumRelationshipMembership.relationship_key == uuid.UUID(id_),
            )
        )
        return str(m.relationship_revision_id) if m else None
    if kind == "placement":
        pl = db.get(CoursePlacement, uuid.UUID(id_))
        return str(pl.entity_revision_id) if pl else None
    if kind == "review_item":
        ri = db.get(ReviewItem, uuid.UUID(id_))
        return ri.status.value if ri else None
    if kind == "document_year":
        dv = db.get(DocumentVersion, uuid.UUID(id_))
        return dv.academic_year if dv else None
    if kind == "source":
        return "present" if db.get(CurriculumSource, uuid.UUID(id_)) else None
    raise ValueError(kind)


def _restore(db: Session, p: Principal, cv: uuid.UUID, kind: str, id_: str, before: str | None) -> None:
    if kind == "entity":
        m = membership(db, cv, uuid.UUID(id_))
        if before is None:
            if m is not None:
                db.delete(m)
        elif m is None:
            db.add(
                CurriculumEntityMembership(
                    curriculum_version_id=cv, entity_id=uuid.UUID(id_), entity_revision_id=uuid.UUID(before)
                )
            )
        else:
            m.entity_revision_id = uuid.UUID(before)
    elif kind == "relationship":
        m = db.scalar(
            select(CurriculumRelationshipMembership).where(
                CurriculumRelationshipMembership.curriculum_version_id == cv,
                CurriculumRelationshipMembership.relationship_key == uuid.UUID(id_),
            )
        )
        if before is None:
            if m is not None:
                db.delete(m)
        elif m is None:
            db.add(
                CurriculumRelationshipMembership(
                    curriculum_version_id=cv,
                    relationship_key=uuid.UUID(id_),
                    relationship_revision_id=uuid.UUID(before),
                )
            )
        else:
            m.relationship_revision_id = uuid.UUID(before)
    elif kind == "placement":
        db.get(CoursePlacement, uuid.UUID(id_)).entity_revision_id = uuid.UUID(before)
    elif kind == "review_item":
        ri = db.get(ReviewItem, uuid.UUID(id_))
        ri.status = ReviewItemStatus(before)
        db.add(
            ReviewDecision(
                review_item_id=ri.id,
                decision=ri.status,
                rationale="Reopened: the outline import that resolved it was reverted.",
                decided_by=p.user_id,
            )
        )
    elif kind == "document_year":
        db.get(DocumentVersion, uuid.UUID(id_)).academic_year = before
    elif kind == "source":
        src = db.get(CurriculumSource, uuid.UUID(id_))
        if src is not None and before is None:
            db.delete(src)
    db.flush()


def revert_import(db: Session, p: Principal, import_id: uuid.UUID) -> OutlineImport:
    p.require(Role.editor)
    imp = lock_import(db, p, import_id)
    cv = assert_draft(db, imp.curriculum_version_id, lock=True)
    if imp.status != OutlineImportStatus.applied:
        raise Conflict(f"Only an applied import can be reverted (this one is {imp.status.value}).", code="not_applied")
    ops = (imp.result or {}).get("undo", {}).get("ops", [])
    final: dict[tuple[str, str], str | None] = {}
    first: dict[tuple[str, str], str | None] = {}
    for op in ops:
        k = (op["kind"], op["id"])
        final[k] = op["after"]
        first.setdefault(k, op["before"])
    conflicts = [
        {"kind": k[0], "id": k[1], "expected": after, "current": cur}
        for k, after in final.items()
        if (cur := _current(db, cv.id, *k)) != after
    ]
    if conflicts:
        raise Conflict(
            "Later changes touched what this import changed, so it can't be reverted automatically.",
            code="revert_conflict",
            details={"conflicts": conflicts[:50], "total": len(conflicts)},
        )
    dangling_before = _dangling(load_snapshot(db, cv.id))
    kept: list[str] = []
    for k in reversed(list(first)):
        if k[0] in ("source", "document_year") and _still_relied_on(db, imp, k[0], k[1]):
            kept.append(k[0])  # another applied import of the same document still relies on it
            continue
        _restore(db, p, cv.id, k[0], k[1], first[k])
    db.flush()
    if bad := _dangling(load_snapshot(db, cv.id)) - dangling_before:
        raise Conflict(
            "Links added after this import point at elements it would remove; reject or remove those links first.",
            code="revert_conflict",
            details={"dangling_relationships": sorted(bad)[:50], "total": len(bad)},
        )
    imp.status = OutlineImportStatus.reverted
    imp.reverted_by, imp.reverted_at, imp.updated_at = p.user_id, utcnow(), utcnow()
    audit(db, p, "outline_import.reverted", "outline_import", imp.id, {"operations": len(ops), "kept": kept})
    return imp


def _still_relied_on(db: Session, imp: OutlineImport, kind: str, id_: str) -> bool:
    """A source assignment or a document's academic year that another applied import of the same document uses."""
    others = select(OutlineImport.id).where(
        OutlineImport.id != imp.id,
        OutlineImport.document_version_id == imp.document_version_id,
        OutlineImport.status == OutlineImportStatus.applied,
    )
    if kind == "source":
        src = db.get(CurriculumSource, uuid.UUID(id_))
        if src is None:
            return False
        others = others.where(OutlineImport.curriculum_version_id == src.curriculum_version_id)
    return db.scalar(others.limit(1)) is not None
