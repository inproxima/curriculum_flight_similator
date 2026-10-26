"""Deterministic checks of the reconcile agent's output. Pure functions over the ImportContext.

Matches
- An extracted item and an existing item of the same type with the same normalized wording always match (the model
  cannot override this). Assessments that match by title but differ in format / weight / timing become updates.
- A model match must stay within one type, use each existing item at most once, and ("reworded") be textually
  similar enough (sequence ratio ≥ 0.5 or token overlap ≥ 0.3); otherwise the item is treated as new, so a wrong
  match can never move an old element's links onto different content.
Links
- Only assesses (assessment → course outcome), supports (topic → course outcome) and contributes_to (course outcome →
  program outcome), between this course's elements (existing or new) and the program's outcomes.
- No duplicate of an existing link (documented or proposed) and never a link a reviewer rejected.
- The quote must be found in the outline or in either element's own text. "Stated" additionally needs an outline
  quote that names both ends (outcome numbers such as "LO 1–3" count); otherwise it is downgraded to inferred.
- Strength is clamped to 0–1; level only for contributes_to; per-element and total caps.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from cfs.ai.alignment import existing_link_reason
from cfs.ai.extraction import _locate, _words_in
from cfs.ingest.text import find_span, normalize
from cfs.outlines.context import ImportContext

PAIRS = {
    "assesses": ("assessment", "course_outcome"),
    "supports": ("topic", "course_outcome"),
    "contributes_to": ("course_outcome", "program_outcome"),
}
CAPS = {"assesses": 6, "supports": 4, "contributes_to": 3}
MAX_LINKS = 150
ASSESSMENT_FIELDS = ("format", "weight_percent", "timing_week", "timing_text")


def canon(text: str | None) -> str:
    return normalize(text or "").lower().strip(" .;:,")


# Words that say nothing about what an outcome is about (outline boilerplate and generic outcome verbs).
STOPWORDS = set(
    """a an and are as at be been by can course end for from in into is it its of on or our so students student
    that the their them these this those through to upon use using will with within able successful successfully
    completion complete completing upon describe explain identify apply applying analyse analyze evaluate demonstrate
    understand recognize recognise discuss compare interpret employ define list summarize summarise develop discuss
    appropriate appropriately basic various key main relevant including include includes""".split()
)


def content_tokens(text: str | None) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]{3,}", canon(text)):
        if w in STOPWORDS:
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def similarity(a: str, b: str) -> tuple[float, float]:
    """(character ratio, overlap of content words): boilerplate like "students will be able to describe" doesn't
    make two different outcomes look alike."""
    ratio = difflib.SequenceMatcher(None, canon(a), canon(b)).ratio()
    ta, tb = content_tokens(a), content_tokens(b)
    overlap = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    return ratio, overlap


def same_element(a: str, b: str) -> tuple[bool, float]:
    """Whether a model-proposed "reworded" match is textually plausible (both checks, not either)."""
    ratio, overlap = similarity(a, b)
    return (overlap >= 0.34 and ratio >= 0.45) or overlap >= 0.6, round(overlap, 2)


def _num(v: Any) -> Any:
    if v is None:
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return v


def field_changes(existing: Any, new: Any) -> list[dict[str, Any]]:
    """Differences the outline states (a value the outline doesn't give is not a change)."""
    out = []
    if canon(existing.text) != canon(new.text):
        out.append(
            {
                "field": "statement" if new.type == "course_outcome" else "title",
                "before": existing.text,
                "after": new.text,
            }
        )
    if new.type == "assessment":
        for f in ASSESSMENT_FIELDS:
            nv = new.details.get(f)
            if nv is None:
                continue
            ev = existing.details.get(f)
            same = canon(str(ev)) == canon(str(nv)) if f in ("format", "timing_text") else _num(ev) == _num(nv)
            if not same:
                out.append({"field": f, "before": ev, "after": nv})
    return out


def verify_matches(ctx: ImportContext, raw: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """new ref → {existing_ref | None, relation same|changed|new, changes, reason, decided_by}; plus drop notes."""
    result: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    drops: list[dict[str, str]] = []
    for n in ctx.extracted.values():
        for e in ctx.existing.values():
            if e.type == n.type and e.ref not in used and canon(e.text) == canon(n.text):
                ch = field_changes(e, n)
                result[n.ref] = {
                    "existing_ref": e.ref,
                    "relation": "changed" if ch else "same",
                    "changes": ch,
                    "reason": "Same wording as the program's record"
                    + ("; the outline states different details" if ch else ""),
                    "decided_by": "code",
                }
                used.add(e.ref)
                break
    agent = {m.get("new_ref"): m for m in raw.get("matches", []) if isinstance(m, dict)}
    for n in ctx.extracted.values():
        if n.ref in result:
            continue
        m = agent.get(n.ref) or {}
        new = {"existing_ref": None, "relation": "new", "changes": [], "decided_by": "agent" if m else "code"}
        new["reason"] = m.get("reason") or "No existing element corresponds to it."
        e = ctx.existing.get(m.get("existing_ref") or "")
        if m.get("relation") in ("same", "reworded") and m.get("existing_ref"):
            if e is None:
                drops.append(
                    {"what": n.ref, "reason": f"the agent matched it to an unknown element {m['existing_ref']}"}
                )
            elif e.type != n.type:
                drops.append({"what": n.ref, "reason": f"the agent matched a {n.type} to a {e.type}"})
            elif e.ref in used:
                drops.append({"what": n.ref, "reason": f"{e.key} is already matched to another outline item"})
            else:
                ok, overlap = same_element(e.text, n.text)
                if ok:
                    ch = field_changes(e, n)
                    result[n.ref] = {
                        "existing_ref": e.ref,
                        "relation": "changed" if ch else "same",
                        "changes": ch,
                        "reason": m.get("reason") or "",
                        "decided_by": "agent",
                        "overlap": overlap,
                    }
                    used.add(e.ref)
                    continue
                drops.append(
                    {
                        "what": n.ref,
                        "reason": f"the agent matched it to {e.key}, but the two share too little content "
                        f"(overlap {overlap:.2f}); it is treated as a new item",
                    }
                )
        result[n.ref] = new
    return result, drops


# "outcomes 1, 2 and 4", "learning outcomes 1–3", "objectives: 2, 5" — numbers only in a list after an outcome word
_OUTCOME_LIST = re.compile(
    r"\b(?:(?:learning|course)\s+)?(?:outcomes?|objectives?|c?los)\b\s*(?:#|nos?\.?|:)?\s*"
    r"((?:\d{1,2}(?:\s*-\s*\d{1,2})?(?:\s*(?:,|and|&|or|/)\s*)?)+)",
    re.I,
)
_OUTCOME_TOKEN = re.compile(r"\b(?:C?LO)\s*[-#]?\s*(\d{1,2})\b")  # "LO3", "CLO 2" (upper case; "CO2" is chemistry)


def outcome_numbers(quote: str) -> set[str]:
    """Outcome numbers a quote names explicitly. Numbers elsewhere (an assessment's own number, weights, weeks,
    chemical formulas) are not counted."""
    q = normalize(quote)
    nums = {str(int(x)) for x in _OUTCOME_TOKEN.findall(q)}
    for m in _OUTCOME_LIST.finditer(q):
        seq = m.group(1)
        for a, b in re.findall(r"(\d{1,2})\s*-\s*(\d{1,2})", seq):
            lo, hi = int(a), int(b)
            if lo <= hi <= lo + 20:
                nums |= {str(i) for i in range(lo, hi + 1)}
        nums |= {str(int(x)) for x in re.findall(r"\d{1,2}", seq)}
    return nums


def _key_in(key: str | None, quote: str) -> bool:
    """A key such as "PLO1" named in a quote as a whole word ("PLO10" is not "PLO1")."""
    m = re.fullmatch(r"([A-Za-z]+)\s*(\d+)", (key or "").strip())
    if not m:
        return False
    return re.search(rf"\b{re.escape(m.group(1))}\s*{m.group(2)}\b", quote, re.I) is not None


def _names(end: dict[str, Any], quote: str) -> bool:
    """Whether a quote names this end of a link."""
    if end["type"] == "course_outcome":
        num = str(end.get("number") or "").strip()
        if num.isdigit() and str(int(num)) in outcome_numbers(quote):
            return True
        words = re.findall(r"[a-z]{4,}", canon(end["label"]))
        text = set(re.findall(r"[a-z]{4,}", canon(quote)))
        return bool(words) and sum(w in text for w in words) / len(words) >= 0.6
    if end["type"] == "program_outcome":
        return _key_in(end.get("key"), quote) or _words_in(end.get("short") or end["label"], quote)
    return _words_in(end["label"], quote)


def endpoint(
    ctx: ImportContext, ref: str, matches: dict[str, dict[str, Any]], skipped: set[str]
) -> dict[str, Any] | None:
    """Resolve a ref to a link end: an existing element, a program outcome, or a new element from the outline."""
    if ref in ctx.existing:
        e = ctx.existing[ref]
        num = next((ctx.extracted[n].number for n, m in matches.items() if m["existing_ref"] == ref), None)
        return {
            "ref": ref,
            "entity_id": e.id,
            "type": e.type,
            "key": e.key,
            "label": e.text,
            "number": num,
            "new": False,
        }
    if ref in ctx.plos:
        p = ctx.plos[ref]
        return {
            "ref": ref,
            "entity_id": p.id,
            "type": "program_outcome",
            "key": p.key,
            "label": p.text,
            "short": p.details.get("label"),
            "new": False,
        }
    if ref in ctx.extracted and ref not in skipped:
        n = ctx.extracted[ref]
        m = matches.get(ref) or {}
        if m.get("existing_ref"):
            return endpoint(ctx, m["existing_ref"], matches, skipped)
        return {
            "ref": ref,
            "entity_id": None,
            "type": n.type,
            "key": None,
            "label": n.text,
            "number": n.number,
            "new": True,
        }
    return None


def verify_links(
    ctx: ImportContext, raw: dict[str, Any], matches: dict[str, dict[str, Any]], skipped: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    out: list[dict[str, Any]] = []
    drops: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    counts: dict[tuple[str, str], int] = {}
    links = [x for x in raw.get("links", []) if isinstance(x, dict)]
    # stated links first, then by strength: caps keep the best-supported links
    links.sort(key=lambda x: (x.get("basis") != "stated", -float(x.get("strength") or 0)))
    for link in links:
        rt = link.get("rel_type")
        label = f"{link.get('source_ref')} {rt} {link.get('target_ref')}"
        if rt not in PAIRS:
            drops.append({"what": label, "reason": "not a link type an outline import can add"})
            continue
        s = endpoint(ctx, str(link.get("source_ref")), matches, skipped)
        t = endpoint(ctx, str(link.get("target_ref")), matches, skipped)
        if s is None or t is None:
            drops.append({"what": label, "reason": "unknown or skipped element ref"})
            continue
        if (s["type"], t["type"]) != PAIRS[rt]:
            drops.append({"what": label, "reason": f"{rt} cannot connect a {s['type']} to a {t['type']}"})
            continue
        sk, tk = s["entity_id"] or s["ref"], t["entity_id"] or t["ref"]
        if sk == tk:
            drops.append({"what": label, "reason": "self link"})
            continue
        if (
            not s["new"]
            and not t["new"]
            and (why := existing_link_reason(ctx.snap, rt, s["entity_id"], t["entity_id"]))
        ):
            drops.append({"what": label, "reason": why})
            continue
        if (sk, tk, rt) in seen:
            continue
        quote = (link.get("quote") or "").strip()
        loc = _locate(ctx.pages, link.get("page"), quote) if quote else None
        in_element = bool(quote) and bool(find_span(s["label"], quote) or find_span(t["label"], quote))
        if not loc and not in_element:
            drops.append({"what": label, "reason": "quote not found in the outline or in either element"})
            continue
        basis = "stated" if link.get("basis") == "stated" else "inferred"
        note = None
        if basis == "stated":
            exact = ctx.pages[loc[0]][loc[1] : loc[2]] if loc else ""
            if not (loc and _names(s, exact) and _names(t, exact)):
                basis, note = (
                    "inferred",
                    "The quote does not state this connection explicitly, so it is an interpretation.",
                )
        try:
            strength = round(min(1.0, max(0.0, float(link.get("strength")))), 3)
        except (TypeError, ValueError):
            drops.append({"what": label, "reason": "strength is not a number"})
            continue
        cap_key = (sk, rt)
        if counts.get(cap_key, 0) >= CAPS[rt] or len(out) >= MAX_LINKS:
            drops.append({"what": label, "reason": "too many links proposed for one element (cap)"})
            continue
        counts[cap_key] = counts.get(cap_key, 0) + 1
        seen.add((sk, tk, rt))
        level = (
            link.get("level")
            if rt == "contributes_to" and link.get("level") in ("introduce", "reinforce", "assess")
            else None
        )
        if level and basis == "stated" and level[:5] not in canon(ctx.pages[loc[0]][loc[1] : loc[2]]):
            level = None  # a documented link only carries a level the source states
        out.append(
            {
                "source": s,
                "target": t,
                "rel_type": rt,
                "basis": basis,
                "level": level,
                "strength": None if basis == "stated" else strength,
                "model_strength": strength,
                "rationale": (link.get("rationale") or "").strip(),
                "quote": ctx.pages[loc[0]][loc[1] : loc[2]] if loc else quote,
                "page": loc[0] if loc else None,
                "quote_source": "outline" if loc else "element",
                "note": note,
            }
        )
    return out, drops
