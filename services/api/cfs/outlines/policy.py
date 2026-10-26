"""What each plan item may do, and whether it is pre-selected ("newer replaces older", ADR 0004).

Two comparisons, both deterministic:
- fit: the outline's academic year against the draft version's (match | older | later | unknown);
- per element: the outline's edition against the newest dated source behind the element (newer | same | older |
  undated).

Defaults (add · replace · retire):
- outline older than the version: add only into an empty section · replace blocked · retire blocked
- outline later than the version, or unknown: add pre-selected · replace selectable · retire selectable
- fit match, element older or undated: add pre-selected · replace pre-selected · retire pre-selected if the section
  is complete and the element wasn't entered manually
- fit match, same edition: add pre-selected · replace pre-selected only if the element already cites this file ·
  retire selectable
- element newer than the outline: add selectable · replace blocked · retire blocked

An element whose wording still appears in the outline is never retired. Stated links are pre-selected; inferred
links when their strength is at least 0.6. Course description and credits are pre-selected only to fill a blank.
"""

from __future__ import annotations

from dataclasses import dataclass

from cfs.outlines.context import Element, ImportContext

INFERRED_DEFAULT = 0.6


@dataclass
class Decision:
    selectable: bool
    default: bool
    reason: str
    blocked: str | None = None

    @staticmethod
    def block(why: str) -> Decision:
        return Decision(False, False, "", why)


def _older_msg(ctx: ImportContext) -> str:
    return (
        f"This outline is for {ctx.outline.get('academic_year')}, older than the version's "
        f"{ctx.snap.version.get('academic_year')}: it can only fill empty sections."
    )


def for_add(ctx: ImportContext, section_count: int, section_newest: int | None, skipped_reason: str | None) -> Decision:
    """section_newest: compare(outline, newest existing element of the section) (None if undated/empty)."""
    if ctx.fit == "older":
        if section_count == 0:
            return Decision(True, skipped_reason is None, "Fills an empty section.")
        return Decision.block(_older_msg(ctx))
    if skipped_reason:
        return Decision(True, False, f"The agent suggests leaving this out: {skipped_reason}")
    if section_newest == -1:
        return Decision(True, False, "The program's current items come from a newer source; check before adding.")
    return Decision(True, True, "New in this outline." if section_count else "Fills an empty section.")


def for_replace(ctx: ImportContext, el: Element, same_file: bool) -> Decision:
    if ctx.fit == "older":
        return Decision.block(_older_msg(ctx))
    c = ctx.compare_item(el)
    if c == -1:
        return Decision.block(f"{el.key} comes from a newer source ({el.edition.label if el.edition else '?'}).")
    if ctx.fit != "match":
        return Decision(True, False, "The outline's academic year differs from the version's (or is unknown).")
    if c is None:
        return Decision(True, True, "The recorded wording has no dated source; the dated outline replaces it.")
    if c == 1:
        return Decision(True, True, f"Newer outline ({ctx.outline.get('academic_year')}) replaces the older wording.")
    if same_file:
        return Decision(True, True, "Same outline, read again: corrects the recorded wording.")
    return Decision(True, False, "Same academic year from a different document: choose which wording applies.")


def for_keep(ctx: ImportContext, el: Element) -> Decision:
    if ctx.compare_item(el) == -1:
        return Decision(True, False, "Adds this outline as a citation (the element also has a newer source).")
    return Decision(True, ctx.fit == "match", "Unchanged; adds this outline as its citation.")


def for_retire(
    ctx: ImportContext, el: Element, *, complete: bool, still_on_page: int | None, certain: bool = True
) -> Decision:
    if still_on_page is not None and certain:
        return Decision.block(f"Its wording still appears in the outline (page {still_on_page}).")
    if ctx.fit == "older":
        return Decision.block(_older_msg(ctx))
    c = ctx.compare_item(el)
    if c == -1:
        return Decision.block(f"{el.key} comes from a newer source ({el.edition.label if el.edition else '?'}).")
    if ctx.fit != "match":
        return Decision(True, False, "The outline's academic year differs from the version's (or is unknown).")
    if not complete:
        return Decision(True, False, "The outline may not list every item in this section.")
    if still_on_page is not None:
        return Decision(True, False, f"“{el.text}” still appears in the outline (page {still_on_page}); check first.")
    if el.origin == "manual":
        return Decision(True, False, "Entered manually; not listed in this outline.")
    if c == 0:
        return Decision(True, False, "Same academic year; not listed in this outline.")
    return Decision(True, True, f"Not in the {ctx.outline.get('academic_year') or 'newer'} outline's list.")


def for_field(ctx: ImportContext, current: object, same: bool) -> Decision:
    if same:
        return Decision(False, False, "Already matches the outline.")
    if current in (None, ""):
        return Decision(True, True, "Fills a blank.")
    if ctx.fit == "older":
        return Decision.block(_older_msg(ctx))
    return Decision(True, False, "Replaces the recorded value.")


def for_link(stated: bool, strength: float | None) -> Decision:
    if stated:
        return Decision(True, True, "Stated in the outline.")
    s = strength or 0
    if s >= INFERRED_DEFAULT:
        return Decision(True, True, "AI interpretation of the outline.")
    return Decision(True, False, "AI interpretation with low strength; review before adding.")
