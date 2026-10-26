"""Editions: the academic year (and publication date) a document belongs to, and so the curriculum content it
supports. Entirely deterministic: the model never decides which source is newer, and neither does upload order.

    parse_academic_year("2026-27" | "2026–2027" | "2026/27" | "Fall 2026") → 2026
    parse_academic_year("Winter 2027" | "Spring 2027" | "Summer 2027")      → 2026 (the year that began in the fall)

An existing element's edition is the newest dated document behind it: the supporting field evidence of the revision
the version pins, plus the supporting evidence of its course → element containment link (assessments imported from
outlines often only have the latter). Documents without an academic year don't date anything.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.graph.snapshot import Snapshot
from cfs.ingest.text import normalize
from cfs.models import Document, DocumentVersion, EntityFieldEvidence, EvidenceSpan, RelationshipEvidence
from cfs.models.enums import EvidenceStance

CONTAINMENT = ("has_outcome", "has_assessment", "covers_topic")
_YEAR_PAIR = re.compile(r"\b((?:19|20)\d{2})\s*[-/]\s*((?:19|20)?\d{2})\b")
_TERM_YEAR = re.compile(
    r"\b(fall|autumn|winter|spring|summer)\b[\s,]*(?:term|session|semester|quarter)?[\s,]*((?:19|20)\d{2})\b", re.I
)
_TERM_OFFSET = {"fall": 0, "autumn": 0, "winter": 1, "spring": 1, "summer": 1}


def parse_academic_year(text: str | None) -> int | None:
    """Start year of the academic year a text names, or None."""
    if not text:
        return None
    t = normalize(str(text))
    for m in _YEAR_PAIR.finditer(t):
        start, tail = int(m.group(1)), m.group(2)
        end = int(tail) if len(tail) == 4 else (start // 100) * 100 + int(tail)
        if len(tail) == 2 and end < start:
            end += 100
        if end == start + 1:
            return start
    if m := _TERM_YEAR.search(t):
        return int(m.group(2)) - _TERM_OFFSET[m.group(1).lower()]
    return None


def term_quote(text: str | None) -> str | None:
    """The first 'Fall 2026'-style phrase in a text (used to date an outline from its own first page)."""
    if not text:
        return None
    m = _TERM_YEAR.search(text)
    return m.group(0) if m else None


def year_label(start: int | None) -> str | None:
    return f"{start}-{(start + 1) % 100:02d}" if start is not None else None


@dataclass(frozen=True)
class Edition:
    start_year: int | None
    publication_date: date | None = None

    @property
    def label(self) -> str | None:
        return year_label(self.start_year)

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_year": self.start_year,
            "academic_year": self.label,
            "publication_date": self.publication_date.isoformat() if self.publication_date else None,
        }

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> Edition | None:
        if not d:
            return None
        pd = d.get("publication_date")
        return Edition(d.get("start_year"), date.fromisoformat(pd) if pd else None)


def compare(a: Edition | None, b: Edition | None) -> int | None:
    """1 if a is newer than b, -1 if older, 0 if the same edition, None if either year is unknown."""
    if a is None or b is None or a.start_year is None or b.start_year is None:
        return None
    if a.start_year != b.start_year:
        return 1 if a.start_year > b.start_year else -1
    if a.publication_date and b.publication_date and a.publication_date != b.publication_date:
        return 1 if a.publication_date > b.publication_date else -1
    return 0


def newest(editions: list[Edition]) -> Edition | None:
    dated = [e for e in editions if e.start_year is not None]
    if not dated:
        return None
    return max(dated, key=lambda e: (e.start_year, e.publication_date or date.min))


def document_edition(dv: DocumentVersion) -> Edition:
    return Edition(parse_academic_year(dv.academic_year), dv.publication_date)


def outline_edition(
    dv: DocumentVersion,
    *,
    entered: str | None,
    extracted: dict[str, Any] | None,
    first_page: str | None,
    title: str | None,
) -> dict[str, Any]:
    """Which academic year the outline belongs to, and how that was determined (shown to the user).

    Order: what the user entered → the document's recorded academic year → the year the extraction model read from a
    verified quote → a 'Fall 2026'-style phrase on page 1. Titles and file names are not evidence of a year."""
    ex = extracted or {}
    ex_quote = ex.get("evidence_quote") if not ex.get("unverified") else None
    options = [
        ("entered", entered, entered),
        ("document", dv.academic_year, dv.academic_year),
        ("extraction", ex.get("academic_year") if ex_quote else None, ex_quote),
        ("extraction", ex_quote, ex_quote),
        ("page 1", term_quote(first_page), term_quote(first_page)),
    ]
    for source, text, quote in options:
        start = parse_academic_year(text)
        if start is not None:
            ed = Edition(start, dv.publication_date)
            return {**ed.as_dict(), "source": source, "quote": quote}
    return {**Edition(None, dv.publication_date).as_dict(), "source": None, "quote": None}


def item_sources(db: Session, snap: Snapshot, course_id: str, member_ids: list[str]) -> dict[str, dict[str, Any]]:
    """For each member element: the documents behind it and its edition (newest dated document), or undated."""
    rev_of = {
        uuid.UUID(snap.entities[m]["revision_id"]): m
        for m in member_ids
        if m in snap.entities and not str(snap.entities[m]["revision_id"]).startswith("proj:")
    }
    docs_of: dict[str, set[uuid.UUID]] = defaultdict(set)
    if rev_of:
        for rid, dvid in db.execute(
            select(EntityFieldEvidence.entity_revision_id, EvidenceSpan.document_version_id)
            .join(EvidenceSpan, EvidenceSpan.id == EntityFieldEvidence.evidence_span_id)
            .where(
                EntityFieldEvidence.entity_revision_id.in_(list(rev_of)),
                EntityFieldEvidence.stance == EvidenceStance.supporting,
            )
        ):
            docs_of[rev_of[rid]].add(dvid)
    link_of = {
        uuid.UUID(r["revision_id"]): r["target"]
        for r in snap.relationships.values()
        if r["source"] == course_id and r["type"] in CONTAINMENT and r["target"] in member_ids
    }
    if link_of:
        for rid, dvid in db.execute(
            select(RelationshipEvidence.relationship_revision_id, EvidenceSpan.document_version_id)
            .join(EvidenceSpan, EvidenceSpan.id == RelationshipEvidence.evidence_span_id)
            .where(
                RelationshipEvidence.relationship_revision_id.in_(list(link_of)),
                RelationshipEvidence.stance == EvidenceStance.supporting,
            )
        ):
            docs_of[link_of[rid]].add(dvid)
    all_docs = {d for ds in docs_of.values() for d in ds}
    info: dict[uuid.UUID, tuple[Edition, str]] = {}
    if all_docs:
        for dv, title in db.execute(
            select(DocumentVersion, Document.title)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id.in_(list(all_docs)))
        ):
            info[dv.id] = (document_edition(dv), title)
    out: dict[str, dict[str, Any]] = {}
    for m in member_ids:
        ds = sorted(docs_of.get(m, set()), key=str)
        eds = [info[d][0] for d in ds if d in info]
        best = newest(eds)
        src = None
        if best is not None:
            src = next(info[d][1] for d in ds if d in info and info[d][0] == best)
        out[m] = {
            "doc_ids": [str(d) for d in ds],
            "edition": best.as_dict() if best else None,
            "source": src or (info[ds[0]][1] if ds and ds[0] in info else None),
        }
    return out
