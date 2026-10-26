"""Course outline import: pure rules (editions, replacement policy, verification helpers, state hash).

Everything here is deterministic; the model never decides precedence.
"""

from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

import pytest

from cfs.outlines import policy
from cfs.outlines.context import Element
from cfs.outlines.edition import Edition, compare, newest, outline_edition, parse_academic_year, year_label
from cfs.outlines.verify import outcome_numbers, similarity


@pytest.mark.parametrize(
    "text,start",
    [
        ("2026-27", 2026),
        ("2026–27", 2026),
        ("2026 - 2027", 2026),
        ("2026/27", 2026),
        ("COURSE OUTLINE - Fall 2026", 2026),
        ("Winter 2027", 2026),
        ("Spring 2027 session", 2026),
        ("Summer term, 2027", 2026),
        ("1999-00", 1999),
        ("2026-28", None),  # not one academic year
        ("CHEM 351 outline", None),
        (None, None),
    ],
)
def test_parse_academic_year(text, start):
    assert parse_academic_year(text) == start


def test_year_label_and_compare():
    assert year_label(2026) == "2026-27" and year_label(1999) == "1999-00" and year_label(None) is None
    a, b = Edition(2026), Edition(2025)
    assert compare(a, b) == 1 and compare(b, a) == -1 and compare(a, Edition(2026)) == 0
    assert compare(a, Edition(None)) is None and compare(None, a) is None
    # same year: publication date breaks the tie only when both are known
    assert compare(Edition(2026, date(2026, 8, 1)), Edition(2026, date(2026, 5, 1))) == 1
    assert compare(Edition(2026, date(2026, 8, 1)), Edition(2026)) == 0
    assert newest([Edition(None), Edition(2024), Edition(2025, date(2025, 1, 1))]) == Edition(2025, date(2025, 1, 1))
    assert newest([Edition(None)]) is None


def test_outline_edition_prefers_entered_then_document_then_text():
    dv = SimpleNamespace(academic_year=None, publication_date=None, original_filename="outline.pdf")
    first = "CHEM 351 - Organic Chemistry I\nCOURSE OUTLINE - Fall 2026"
    ed = outline_edition(
        dv, entered=None, extracted={"academic_year": None, "unverified": True}, first_page=first, title="x"
    )
    assert ed["start_year"] == 2026 and ed["source"] == "page 1" and ed["quote"] == "Fall 2026"
    ed = outline_edition(dv, entered="2025-26", extracted=None, first_page=first, title=None)
    assert ed["start_year"] == 2025 and ed["source"] == "entered"
    dv.academic_year = "2027-28"
    assert outline_edition(dv, entered=None, extracted=None, first_page=first, title=None)["source"] == "document"
    # an unverified model year is never used
    dv.academic_year = None
    ed = outline_edition(
        dv, entered=None, extracted={"academic_year": "2030-31", "unverified": True}, first_page="", title=""
    )
    assert ed["start_year"] is None and ed["source"] is None


def test_outcome_numbers_and_similarity():
    assert outcome_numbers("Midterm (25%): assesses outcomes 1–3") == {"1", "2", "3"}
    assert outcome_numbers("Lab reports assess LO4 and CLO 5") == {"4", "5"}
    assert outcome_numbers("Outcomes 1, 2 and 4") == {"1", "2", "4"}
    assert "25" not in outcome_numbers("Midterm 25% outcomes 1")
    assert outcome_numbers("Final exam, week 14, 35%") == set()  # no outcome marker, no numbers taken
    r, o = similarity(
        "Describe regulation of core metabolic pathways.",
        "Describe the regulation of core metabolic pathways and their integration.",
    )
    assert r > 0.6 and o > 0.5
    r, o = similarity("Interpret enzyme kinetics data.", "Communicate findings to lay audiences.")
    assert r < 0.5 and o < 0.3


@dataclass
class Ctx:
    """The parts of ImportContext the policy reads."""

    outline_year: int | None
    version_year: int | None
    snap: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(version={"academic_year": "2025-26"}))

    @property
    def outline(self):
        return {"start_year": self.outline_year, "academic_year": year_label(self.outline_year)}

    @property
    def fit(self):
        if self.outline_year is None or self.version_year is None:
            return "unknown"
        return (
            "match"
            if self.outline_year == self.version_year
            else ("older" if self.outline_year < self.version_year else "later")
        )

    def compare_item(self, el):
        return compare(Edition(self.outline_year), el.edition)


def el(year=None, origin="extracted"):
    return Element(
        ref="e1",
        type="course_outcome",
        text="x",
        id="1",
        key="K-CO1",
        origin=origin,
        edition=Edition(year) if year else None,
    )


def test_replace_policy_table():
    match = Ctx(2025, 2025)
    assert policy.for_replace(match, el(2024), same_file=False).default is True  # newer outline replaces
    assert policy.for_replace(match, el(None), same_file=False).default is True  # undated replaced by dated
    same = policy.for_replace(match, el(2025), same_file=False)
    assert same.selectable and not same.default  # same year, other document: a person chooses
    assert policy.for_replace(match, el(2025), same_file=True).default is True  # same file re-read: correction
    newer = policy.for_replace(match, el(2026), same_file=False)
    assert not newer.selectable and "newer source" in newer.blocked
    older = policy.for_replace(Ctx(2023, 2025), el(2022), same_file=False)
    assert not older.selectable and "only fill empty sections" in older.blocked
    later = policy.for_replace(Ctx(2026, 2025), el(2024), same_file=False)
    assert later.selectable and not later.default
    unknown = policy.for_replace(Ctx(None, 2025), el(2024), same_file=False)
    assert unknown.selectable and not unknown.default


def test_retire_policy_table():
    match = Ctx(2025, 2025)
    assert policy.for_retire(match, el(2024), complete=True, still_on_page=None).default is True
    assert not policy.for_retire(match, el(2024), complete=False, still_on_page=None).default  # partial list
    assert not policy.for_retire(match, el(2024, origin="manual"), complete=True, still_on_page=None).default
    assert not policy.for_retire(match, el(2025), complete=True, still_on_page=None).default  # same edition
    blocked = policy.for_retire(match, el(2024), complete=True, still_on_page=2)
    assert not blocked.selectable and "page 2" in blocked.blocked  # wording still in the outline
    assert not policy.for_retire(match, el(2026), complete=True, still_on_page=None).selectable
    assert not policy.for_retire(Ctx(2023, 2025), el(2022), complete=True, still_on_page=None).selectable


def test_add_field_and_link_policy():
    match, older = Ctx(2025, 2025), Ctx(2023, 2025)
    assert policy.for_add(match, 3, 1, None).default is True
    assert policy.for_add(older, 0, None, None).default is True  # older outline may fill an empty section
    assert not policy.for_add(older, 2, None, None).selectable
    skipped = policy.for_add(match, 2, 1, "grading rule, not an assessment")
    assert skipped.selectable and not skipped.default and "grading rule" in skipped.reason
    assert not policy.for_add(match, 2, -1, None).default  # the section's content comes from a newer source
    assert policy.for_field(match, None, False).default is True
    assert not policy.for_field(match, "old text", False).default
    assert not policy.for_field(older, "old text", False).selectable
    assert not policy.for_field(match, "same", True).selectable
    assert policy.for_link(True, None).default is True
    assert policy.for_link(False, 0.6).default is True and not policy.for_link(False, 0.59).default


def test_course_state_hash_is_scoped_to_the_course(client, v1, ent):
    from cfs.core.db import get_sessionmaker
    from cfs.graph.snapshot import load_snapshot
    from cfs.outlines.state import course_state_hash

    db = get_sessionmaker()()
    try:
        snap = load_snapshot(db, v1)
        c = ent("SYN 202")
        h = course_state_hash(snap, c)
        assert h == course_state_hash(load_snapshot(db, v1), c)  # stable
        other = snap.copy()
        k = next(
            k
            for k, r in other.relationships.items()
            if r["source"] == ent("SYN101-CO1") and r["type"] == "contributes_to"
        )
        other.relationships[k] = {**other.relationships[k], "review_state": "rejected"}
        assert course_state_hash(other, c) == h  # another course's link: not this plan's concern
        mine = snap.copy()
        k = next(
            k
            for k, r in mine.relationships.items()
            if r["source"] == ent("SYN202-CO1") and r["type"] == "contributes_to"
        )
        mine.relationships[k] = {**mine.relationships[k], "revision_id": "changed"}
        assert course_state_hash(mine, c) != h
    finally:
        db.close()


def test_verification_is_not_fooled_by_boilerplate_or_stray_numbers():
    """Probe cases from code review: stated links need numbers tied to an outcome word; keys match whole words;
    shared boilerplate doesn't make two outcomes the same element."""
    from cfs.outlines.verify import _key_in, same_element

    assert outcome_numbers("Quiz 3 (10%) covers learning outcomes 1 and 2") == {"1", "2"}
    assert outcome_numbers("Lab report 4 (week 6) - see course learning outcomes") == set()
    assert outcome_numbers("Measure CO2 uptake in the reaction vessel (week 3)") == set()
    assert not _key_in("PLO1", "PLO10 and PLO12 are addressed") and _key_in("PLO1", "supports PLO1.")
    assert not same_element(
        "Students will be able to describe the structure of the cell membrane.",
        "Students will be able to analyze thermodynamic data from calorimetry experiments.",
    )[0]
    assert not same_element("Explain the laws of thermodynamics", "Explain the principles of kinetics")[0]
    assert same_element("Midterm", "Midterm Exam")[0]
    assert same_element(
        "Describe regulation of core metabolic pathways.",
        "Describe the regulation of core metabolic pathways and their integration.",
    )[0]
