"""Everything one reconciliation works from, with short refs the model can use (never database ids).

e1, e2, …  the course's existing outcomes, assessments and topics in the draft version
n1, n2, …  items extracted from the outline and verified against its text
p1, p2, …  the program's outcomes
g1, g2, …  the course's missing pieces
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.analysis.alignment import provenance
from cfs.curriculum.rules import normalize_code
from cfs.graph.snapshot import Snapshot
from cfs.ingest.text import normalize
from cfs.models import DocumentPage, DocumentVersion, EntityFieldEvidence, EvidenceSpan
from cfs.models.enums import EvidenceStance
from cfs.outlines.edition import Edition, compare, item_sources, parse_academic_year
from cfs.outlines.missing import course_missing
from cfs.outlines.state import course_members, program_outcomes

KIND_TYPE = {"outcome": "course_outcome", "assessment": "assessment", "topic": "topic"}
SECTION_OF = {"course_outcome": "outcomes", "assessment": "assessments", "topic": "topics"}
SECTION_LABEL = {"outcomes": "Learning outcomes", "assessments": "Assessments", "topics": "Topics"}
PAGE_BUDGET = 60_000


@dataclass
class Element:
    ref: str
    type: str
    text: str
    id: str | None = None  # entity id for existing elements and program outcomes
    key: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    origin: str | None = None
    edition: Edition | None = None
    doc_ids: list[str] = field(default_factory=list)
    source: str | None = None
    number: str | None = None  # the outline's own number for an outcome ("3" in "LO 3")
    page: int | None = None
    quote: str | None = None
    span_id: str | None = None

    def as_prompt(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ref": self.ref, "type": self.type, "text": self.text}
        if self.key:
            d["key"] = self.key
        if self.type == "assessment":
            for k in ("format", "weight_percent", "timing_week", "timing_text"):
                if self.details.get(k) is not None:
                    d[k] = self.details[k]
        if self.number:
            d["number"] = self.number
        if self.page:
            d["page"] = self.page
        if self.source:
            d["source"] = self.source
        return d


@dataclass
class ImportContext:
    snap: Snapshot
    course: dict[str, Any]
    code: str
    aliases: list[str]
    dv: DocumentVersion
    pages: dict[int, str]
    outline: dict[str, Any]  # outline edition, with how it was determined
    version_year: int | None
    existing: dict[str, Element]
    extracted: dict[str, Element]
    plos: dict[str, Element]
    missing: dict[str, Any]
    gap_ref: dict[str, str]
    course_fields: dict[str, dict[str, Any]]
    extraction_dropped: list[dict[str, Any]]
    doc_ids: set[uuid.UUID]
    ref_of: dict[str, str] = field(default_factory=dict)  # entity id → ref

    @property
    def course_id(self) -> str:
        return self.course["id"]

    @property
    def outline_edition(self) -> Edition:
        return Edition.from_dict(self.outline) or Edition(None)

    @property
    def fit(self) -> str:
        """How the outline's academic year relates to the version's: match | older | later | unknown."""
        o = self.outline.get("start_year")
        if o is None or self.version_year is None:
            return "unknown"
        return "match" if o == self.version_year else ("older" if o < self.version_year else "later")

    def compare_item(self, el: Element) -> int | None:
        """1 outline newer than the element's source, 0 same edition, -1 element newer, None undated/unknown."""
        return compare(self.outline_edition, el.edition)

    def elements_of(self, etype: str, pool: str = "existing") -> list[Element]:
        src = self.existing if pool == "existing" else self.extracted
        return [e for e in src.values() if e.type == etype]


def _existing_text(e: dict[str, Any]) -> str:
    return (e["details"].get("statement_verbatim") or e["title"] or "").strip()


def pages_of(db: Session, dv_id: uuid.UUID) -> dict[int, str]:
    return {
        p.page_number: p.text
        for p in db.scalars(
            select(DocumentPage).where(DocumentPage.document_version_id == dv_id).order_by(DocumentPage.page_number)
        )
    }


def outline_text(pages: dict[int, str], budget: int = PAGE_BUDGET) -> str:
    out, used = [], 0
    for n in sorted(pages):
        block = f"=== PAGE {n} ===\n{pages[n]}"
        if used + len(block) > budget:
            out.append(block[: max(0, budget - used)] + "\n[… truncated]")
            break
        out.append(block)
        used += len(block)
    return "\n".join(out)


def course_candidates(cands: list[dict[str, Any]], code: str, aliases: list[str]) -> list[dict[str, Any]]:
    codes = {normalize_code(code), *(normalize_code(a) for a in aliases)}
    out = []
    for c in cands:
        if c["kind"] in KIND_TYPE and normalize_code(c.get("course", "")) in codes:
            out.append(c)
        elif c["kind"] == "course" and normalize_code(c.get("code", "")) in codes:
            out.append(c)
    return out


def codes_found(cands: list[dict[str, Any]]) -> list[str]:
    return sorted({c.get("course") or c.get("code") for c in cands if c.get("course") or c.get("code")})


def _plo_doc_ids(db: Session, snap: Snapshot, ids: list[str]) -> set[uuid.UUID]:
    revs = [uuid.UUID(snap.entities[i]["revision_id"]) for i in ids]
    if not revs:
        return set()
    return set(
        db.scalars(
            select(EvidenceSpan.document_version_id)
            .join(EntityFieldEvidence, EntityFieldEvidence.evidence_span_id == EvidenceSpan.id)
            .where(
                EntityFieldEvidence.entity_revision_id.in_(revs),
                EntityFieldEvidence.stance == EvidenceStance.supporting,
            )
        )
    )


def build_context(
    db: Session,
    snap: Snapshot,
    course_id: str,
    dv: DocumentVersion,
    *,
    candidates: list[dict[str, Any]],
    dropped: list[dict[str, Any]],
    outline: dict[str, Any],
    aliases: list[str] | None = None,
) -> ImportContext:
    course = snap.entities[course_id]
    code = course["key"]
    members = course_members(snap, course_id)
    member_ids = [i for t in ("course_outcome", "assessment", "topic") for i in members[t]]
    sources = item_sources(db, snap, course_id, member_ids)

    existing: dict[str, Element] = {}
    ref_of: dict[str, str] = {}
    for i, eid in enumerate(member_ids, 1):
        e = snap.entities[eid]
        src = sources.get(eid) or {}
        el = Element(
            ref=f"e{i}",
            type=e["type"],
            text=_existing_text(e),
            id=eid,
            key=e["key"],
            details={
                k: v for k, v in e["details"].items() if k in ("format", "weight_percent", "timing_week", "timing_text")
            },
            origin=e["origin"],
            edition=Edition.from_dict(src.get("edition")),
            doc_ids=src.get("doc_ids") or [],
            source=src.get("source"),
        )
        existing[el.ref] = el
        ref_of[eid] = el.ref

    plos: dict[str, Element] = {}
    plo_ids = program_outcomes(snap)
    for i, pid in enumerate(plo_ids, 1):
        p = snap.entities[pid]
        stmt = p["details"].get("statement_verbatim")
        el = Element(ref=f"p{i}", type="program_outcome", text=stmt or p["title"], id=pid, key=p["key"])
        el.details = {"label": p["details"].get("label") or p["title"]}
        plos[el.ref] = el
        ref_of[pid] = el.ref

    extracted: dict[str, Element] = {}
    course_fields: dict[str, dict[str, Any]] = {}
    n = 0
    for c in course_candidates(candidates, code, aliases or []):
        if c["kind"] == "course":
            for f in ("description", "credits"):
                fv = (c.get("fields") or {}).get(f)
                if fv and f not in course_fields:
                    course_fields[f] = fv
            continue
        n += 1
        et = KIND_TYPE[c["kind"]]
        details: dict[str, Any] = {}
        if et == "assessment":
            details = {
                "format": c.get("format"),
                "weight_percent": c.get("weight"),
                "timing_week": c.get("week"),
                "timing_text": c.get("timing_text"),
            }
        extracted[f"n{n}"] = Element(
            ref=f"n{n}",
            type=et,
            text=c.get("statement") if et == "course_outcome" else c.get("title") or "",
            details=details,
            number=c.get("number"),
            page=c.get("page"),
            quote=c.get("quote"),
            span_id=c.get("span_id"),
        )

    missing = course_missing(snap, course_id)
    gap_ref = {g["id"]: f"g{i}" for i, g in enumerate([*missing["items"], *missing["program"]], 1)}
    doc_ids = {dv.id}
    for el in existing.values():
        doc_ids |= {uuid.UUID(d) for d in el.doc_ids}
    # the course's own text (title, description) is sent too: its sources' AI policy applies
    doc_ids |= {uuid.UUID(d) for d in item_sources(db, snap, course_id, [course_id])[course_id]["doc_ids"]}
    doc_ids |= _plo_doc_ids(db, snap, plo_ids)
    return ImportContext(
        snap=snap,
        course=course,
        code=code,
        aliases=aliases or [],
        dv=dv,
        pages=pages_of(db, dv.id),
        outline=outline,
        version_year=parse_academic_year(snap.version.get("academic_year")),
        existing=existing,
        extracted=extracted,
        plos=plos,
        missing=missing,
        gap_ref=gap_ref,
        course_fields=course_fields,
        extraction_dropped=[
            d
            for d in dropped
            if normalize_code(str((d.get("item") or {}).get("course_code") or "")) == normalize_code(code)
        ],
        doc_ids=doc_ids,
        ref_of=ref_of,
    )


def prompt_state(ctx: ImportContext) -> dict[str, Any]:
    """The <course_state> JSON the agent sees."""
    scope = set(ctx.ref_of)
    links = []
    for r in sorted(ctx.snap.relationships.values(), key=lambda r: r["key"]):
        if r["type"] not in ("contributes_to", "assesses", "supports") or not (
            r["source"] in scope and r["target"] in scope
        ):
            continue
        links.append(
            {
                "source": ctx.ref_of[r["source"]],
                "target": ctx.ref_of[r["target"]],
                "type": r["type"],
                "state": "rejected" if r["review_state"] == "rejected" else provenance(r),
            }
        )
    missing = []
    for g in [*ctx.missing["items"], *[p for p in ctx.missing["program"] if p["fillable"]]]:
        if not g["fillable"]:
            continue
        missing.append(
            {
                "ref": ctx.gap_ref[g["id"]],
                "kind": g["kind"],
                "message": g["message"],
                "elements": [ctx.ref_of.get(i, "course") for i in g["entity_ids"]],
            }
        )
    c = ctx.course
    return {
        "course": {
            "code": ctx.code,
            "title": c["title"],
            "description": (c.get("description") or "")[:1500] or None,
            "version": ctx.snap.version.get("label"),
            "version_academic_year": ctx.snap.version.get("academic_year"),
        },
        "outline_edition": {
            "academic_year": ctx.outline.get("academic_year"),
            "determined_from": ctx.outline.get("source"),
        },
        "existing": [e.as_prompt() for e in ctx.existing.values()],
        "existing_links": links,
        "program_outcomes": [
            {
                "ref": p.ref,
                "key": p.key,
                "label": p.details.get("label"),
                "statement": p.text if p.text != p.details.get("label") else None,
            }
            for p in ctx.plos.values()
        ],
        "missing": missing,
        "extracted": [e.as_prompt() for e in ctx.extracted.values()],
    }


def still_in_outline(ctx: ImportContext, text: str) -> tuple[int | None, bool]:
    """(page, certain) where an existing element's wording still appears in the outline. Long wording found verbatim is
    certain (it can't be retired); a short name found as a whole word ("Quiz") is only a hint to check."""
    want = normalize(text).lower().strip(" .;:")
    if not want:
        return None, False
    for n in sorted(ctx.pages):
        page = normalize(ctx.pages[n]).lower()
        if len(want) >= 12 and want in page:
            return n, True
        if len(want) < 12 and re.search(rf"\b{re.escape(want)}\b", page):
            return n, False
    return None, False
