"""Plan items: what the import can do, each with its evidence, reason, and default selection.

    add        a new outcome / assessment / topic from the outline
    replace    the outline's newer wording or details for an existing element (same element: key and links persist)
    keep       unchanged; optionally adds the outline as a citation
    retire     an existing element the newer outline no longer lists (removed from this draft with its links)
    detach     the same for a topic shared with other courses: only its connection to this course goes
    set_field  course description / credits stated by the outline
    link       assesses / supports / contributes_to, "stated" in the outline or an AI interpretation

Only the model's matches, skips and links come from the agent; everything else is decided here and in policy.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy.orm import Session

from cfs.curriculum.service import allocate_key
from cfs.graph.queries import owner_courses
from cfs.ingest.pipeline import make_span
from cfs.models.enums import EntityType
from cfs.outlines import policy
from cfs.outlines.context import SECTION_LABEL, SECTION_OF, Element, ImportContext, still_in_outline
from cfs.outlines.edition import compare, newest
from cfs.outlines.verify import _num, canon, verify_links, verify_matches

SECTION_CODE = {"outcomes": "o", "assessments": "a", "topics": "t"}
VERB = {"assesses": "assesses", "supports": "supports", "contributes_to": "contributes to"}
FIELD_OF = {"course_outcome": "statement", "assessment": "title", "topic": "title"}
STRUCTURAL = {"has_outcome", "has_assessment", "covers_topic"}
UNFILLABLE_WHY = {
    "course_no_prerequisite": "Prerequisites come from the calendar entry: import it on the Documents page and "
    "review the requisite in the Review inbox.",
    "course_no_year": "A course outline doesn't set where the course sits in this program.",
    "course_no_term": "The term an outline is offered in isn't the course's placement in this program.",
    "course_pair_no_link": "Needs links to another course: use “Propose missing links (AI)” for the pair.",
}


def _ref(el: Element) -> dict[str, Any]:
    return {"ref": el.ref, "entity_id": el.id, "key": el.key, "type": el.type, "label": el.text}


def _short(end: dict[str, Any]) -> str:
    if end.get("key"):
        return end["key"]
    label = end["label"] or ""
    return label if len(label) <= 60 else label[:57] + "…"


def _item(iid: str, section: str, action: str, title: str, d: policy.Decision, **kw: Any) -> dict[str, Any]:
    noop = bool(kw.get("noop"))
    return {
        "id": iid,
        "section": section,
        "action": action,
        "title": title,
        "noop": noop,
        "selectable": d.selectable and not noop,
        "default_selected": d.default and d.selectable and not noop,
        "reason": d.reason,
        "blocked_reason": d.blocked,
        "target": kw.get("target"),
        "proposed": kw.get("proposed"),
        "key_preview": kw.get("key_preview"),
        "diff": kw.get("diff") or [],
        "evidence": kw.get("evidence") or [],
        "link": kw.get("link"),
        "depends_on": kw.get("depends_on") or [],
        "excludes": kw.get("excludes") or [],
        "fills": kw.get("fills") or [],
        "carries_links": kw.get("carries_links", 0),
        "drops_links": kw.get("drops_links", 0),
        "agent": kw.get("agent"),
    }


def _outline_ev(el: Element) -> list[dict[str, Any]]:
    if not el.quote:
        return []
    return [
        {"span_id": el.span_id, "page": el.page, "quote": el.quote, "source": "outline", "field": FIELD_OF[el.type]}
    ]


def proposed_for(el: Element) -> dict[str, Any]:
    text = el.text.strip()
    if el.type == "course_outcome":
        return {
            "type": el.type,
            "title": text[:500],
            "details": {"statement_verbatim": text, "normalized_label": text[:300]},
        }
    if el.type == "assessment":
        d = el.details
        return {
            "type": el.type,
            "title": text[:500],
            "details": {
                "format": (d.get("format") or None) and str(d["format"])[:120],
                "weight_percent": d.get("weight_percent"),
                "timing_week": d.get("timing_week"),
                "timing_text": (d.get("timing_text") or None) and str(d["timing_text"])[:200],
            },
        }
    return {"type": el.type, "title": text[:500], "details": {}}


def key_prefix(code: str, el_type: str, text: str) -> tuple[str, bool]:
    """(prefix, numbered) for a new element's stable key, following the extractor's conventions."""
    base = code.replace(" ", "")
    if el_type == "course_outcome":
        return f"{base}-CO", True
    slug = re.sub(r"[^a-z0-9]+", "-", canon(text)).strip("-")[:80] or "item"
    return (f"{base}-A-{slug}" if el_type == "assessment" else f"{base}-T-{slug}"), False


def _key_previews(db: Session, ctx: ImportContext, adds: list[Element]) -> dict[str, str]:
    """The keys Apply will use: the same allocate_key call, in the same order, with the same keys already taken."""
    org = ctx.dv.organization_id
    version = uuid.UUID(ctx.snap.version["id"])
    taken: set[str] = set()
    out: dict[str, str] = {}
    for el in adds:
        prefix, numbered = key_prefix(ctx.code, el.type, el.text)
        key, _reuse = allocate_key(
            db,
            org,
            EntityType(el.type),
            prefix=prefix,
            numbered=numbered,
            text=el.text,
            version_id=version,
            taken=taken,
        )
        taken.add(key)
        out[el.ref] = key
    return out


def _sections(ctx: ImportContext, raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    flags = raw.get("section_complete") or {}
    dropped_kinds = {"course_outcome": "outcomes", "assessment": "assessments", "topic": "topics"}
    dropped = {dropped_kinds.get(d.get("kind"), "") for d in ctx.extraction_dropped}
    out = {}
    for etype, sec in SECTION_OF.items():
        ex = ctx.elements_of(etype, "existing")
        new = ctx.elements_of(etype, "extracted")
        present = bool(new)
        complete = present and sec not in dropped and flags.get(sec) is True  # "can't tell" is not complete
        ed = newest([e.edition for e in ex if e.edition])
        if not present:
            reason = f"The outline lists no {sec}; the program's {len(ex)} stay as they are."
        elif complete:
            reason = f"The outline lists the course's {sec}."
        else:
            reason = f"The outline may not list every item here, so {sec} it doesn't mention aren't retired by default."
        out[sec] = {
            "key": sec,
            "label": SECTION_LABEL[sec],
            "status": "present" if present else "absent",
            "complete": complete if present else None,
            "reason": reason,
            "existing": len(ex),
            "extracted": len(new),
            "existing_edition": ed.label if ed else None,
            "compare": compare(ctx.outline_edition, ed),
        }
    return out


def _link_fills(link: dict[str, Any], missing: set[str]) -> list[str]:
    s, t, rt = link["source"], link["target"], link["rel_type"]
    cands: list[str] = []
    if rt == "assesses":
        cands = [f"gap:assessment_no_clo:{s['entity_id']}", f"gap:clo_unassessed:{t['entity_id']}"]
    elif rt == "supports":
        cands = [f"gap:topic_no_clo:{s['entity_id']}"]
    elif rt == "contributes_to":
        cands = [f"gap:clo_no_plo:{s['entity_id']}", f"gap:plo_no_clo:{t['entity_id']}"]
    return [c for c in cands if c in missing]


def _warnings(ctx: ImportContext, raw: dict[str, Any], options: dict[str, Any]) -> list[str]:
    w: list[str] = []
    oy, vy = ctx.outline.get("academic_year"), ctx.snap.version.get("academic_year")
    if ctx.fit == "older":
        w.append(
            f"This outline is for {oy}, but this draft is {vy}: it can only fill empty sections, not replace content."
        )
    elif ctx.fit == "later":
        w.append(
            f"This outline is for {oy}, later than this draft ({vy}). Replacements aren't pre-selected here; "
            f"a newer outline normally goes into a draft for {oy}."
        )
    elif ctx.fit == "unknown":
        w.append(
            "The outline's academic year is unknown. Enter it so a newer outline can replace older content "
            "automatically; until then nothing is replaced by default."
        )
    det = ctx.outline.get("detected") or {}
    if ctx.outline.get("source") == "entered" and det.get("start_year") not in (None, ctx.outline.get("start_year")):
        w.append(
            f"You entered {options.get('academic_year')}, but the document itself says {det.get('academic_year')} "
            f"({det.get('source')}{': “' + str(det.get('quote')) + '”' if det.get('quote') else ''}). "
            "Check which academic year this outline belongs to; the year decides what it may replace."
        )
    cc = raw.get("course_check") or {}
    if cc and cc.get("describes_course") is False:
        w.append(
            f"The agent doubts this document is the outline of {ctx.code}: {cc.get('reason') or 'no reason given'}"
        )
    if ctx.extraction_dropped:
        w.append(
            f"{len(ctx.extraction_dropped)} item(s) for {ctx.code} could not be verified against the outline's text "
            "and were left out."
        )
    return w


def build_plan(
    db: Session,
    ctx: ImportContext,
    raw: dict[str, Any],
    *,
    options: dict[str, Any],
    raw_pdf: bytes | None,
    run_id: str | None,
    model: str | None,
) -> dict[str, Any]:
    matches, match_drops = verify_matches(ctx, raw)
    skipped = {
        s["new_ref"]: (s.get("reason") or "").strip() or "not course content"
        for s in raw.get("skip", [])
        if isinstance(s, dict) and s.get("new_ref") in ctx.extracted and matches[s["new_ref"]]["relation"] == "new"
    }
    links, link_drops = verify_links(ctx, raw, matches, set())
    sections = _sections(ctx, raw)
    missing_ids = {g["id"] for g in ctx.missing["items"]} | {g["id"] for g in ctx.missing["program"]}
    cid = ctx.course_id
    snap = ctx.snap
    items: list[dict[str, Any]] = []
    add_id: dict[str, str] = {}
    retire_id: dict[str, str] = {}
    matched = {m["existing_ref"] for m in matches.values() if m["existing_ref"]}

    def links_touching(eid: str) -> int:
        return sum(
            1
            for r in snap.relationships.values()
            if eid in (r["source"], r["target"]) and r["type"] not in STRUCTURAL and r["review_state"] != "rejected"
        )

    # course description / credits the outline states (verified by the extraction step)
    for f in ("description", "credits"):
        fv = ctx.course_fields.get(f)
        if not fv:
            continue
        cur = ctx.course.get("description") if f == "description" else ctx.course["details"].get("credits")
        same = canon(cur) == canon(fv["value"]) if f == "description" else _num(cur) == _num(fv["value"])
        d = policy.for_field(ctx, cur, same)
        items.append(
            _item(
                f"c:{f}",
                "course",
                "set_field",
                "Course description" if f == "description" else f"Credits: {_num(fv['value'])}",
                d,
                noop=same,
                target={"ref": None, "entity_id": cid, "key": ctx.code, "type": "course", "label": ctx.course["title"]},
                proposed={"field": f, "value": fv["value"]},
                diff=[{"field": f, "before": cur, "after": fv["value"]}],
                evidence=[
                    {
                        "span_id": fv.get("span_id"),
                        "page": fv["page"],
                        "quote": fv["quote"],
                        "source": "outline",
                        "field": f,
                    }
                ],
                fills=[g for g in (f"gap:course_no_{f}:{cid}",) if g in missing_ids],
            )
        )

    adds = [n for n in ctx.extracted.values() if matches[n.ref]["relation"] == "new"]
    keys = _key_previews(db, ctx, adds)
    for etype, sec in SECTION_OF.items():
        code = SECTION_CODE[sec]
        existing = ctx.elements_of(etype, "existing")
        section_cmp = sections[sec]["compare"]
        for n in ctx.elements_of(etype, "extracted"):
            m = matches[n.ref]
            agent = {"decision": m["relation"], "reason": m.get("reason"), "decided_by": m.get("decided_by")}
            if m["existing_ref"]:
                e = ctx.existing[m["existing_ref"]]
                same_file = str(ctx.dv.id) in e.doc_ids
                if m["relation"] == "same":
                    noop = same_file
                    d = (
                        policy.Decision(False, False, "Unchanged; already cites this outline.")
                        if noop
                        else policy.for_keep(ctx, e)
                    )
                    items.append(
                        _item(
                            f"{code}:{n.ref}",
                            sec,
                            "keep",
                            f"{e.key} unchanged",
                            d,
                            noop=noop,
                            target=_ref(e),
                            evidence=_outline_ev(n),
                            agent=agent,
                        )
                    )
                else:
                    d = policy.for_replace(ctx, e, same_file)
                    carried = links_touching(e.id)
                    if d.default and m.get("decided_by") == "agent" and carried and m.get("overlap", 1.0) < 0.5:
                        d = policy.Decision(
                            True,
                            False,
                            f"{d.reason} The wording changed a lot: confirm it is the same element before its "
                            f"{carried} link(s) move to the new wording.",
                        )
                    items.append(
                        _item(
                            f"{code}:{n.ref}",
                            sec,
                            "replace",
                            f"Update {e.key}",
                            d,
                            target=_ref(e),
                            proposed=proposed_for(n),
                            diff=m["changes"],
                            evidence=_outline_ev(n),
                            carries_links=carried,
                            agent=agent,
                        )
                    )
            else:
                d = policy.for_add(ctx, len(existing), section_cmp, skipped.get(n.ref))
                iid = f"{code}:{n.ref}"
                add_id[n.ref] = iid
                items.append(
                    _item(
                        iid,
                        sec,
                        "add",
                        f"New {'outcome' if etype == 'course_outcome' else etype}: {n.text[:90]}",
                        d,
                        proposed=proposed_for(n),
                        key_preview=keys.get(n.ref),
                        evidence=_outline_ev(n),
                        fills=[g for g in (f"gap:course_no_{sec}:{cid}",) if g in missing_ids],
                        agent={**agent, "skip": skipped.get(n.ref)},
                    )
                )
        if sections[sec]["status"] != "present":
            continue
        for e in existing:
            if e.ref in matched:
                continue
            shared = any(o != cid for o in owner_courses(snap, e.id))  # another course also contains it
            page, certain = still_in_outline(ctx, e.text)
            d = policy.for_retire(ctx, e, complete=bool(sections[sec]["complete"]), still_on_page=page, certain=certain)
            iid = f"{code}:{e.ref}"
            retire_id[e.ref] = iid
            items.append(
                _item(
                    iid,
                    sec,
                    "detach" if shared else "retire",
                    f"{'Detach' if shared else 'Retire'} {e.key}: {e.text[:80]}",
                    d,
                    target=_ref(e),
                    drops_links=links_touching(e.id),
                    evidence=[],
                )
            )

    by_id = {i["id"]: i for i in items}
    dv = ctx.dv
    for L in links:
        ends = (L["source"], L["target"])
        depends = [add_id[x["ref"]] for x in ends if x["new"] and x["ref"] in add_id]
        excludes = [retire_id[x["ref"]] for x in ends if not x["new"] and x["ref"] in retire_id]
        d = policy.for_link(L["basis"] == "stated", L["model_strength"])
        if blocked := [x for x in depends if not by_id[x]["selectable"]]:
            d = policy.Decision.block(f"Needs “{by_id[blocked[0]]['title']}”, which can't be applied here.")
        elif any(not by_id[x]["default_selected"] for x in depends) or any(
            by_id[x]["default_selected"] for x in excludes
        ):
            d = policy.Decision(d.selectable, False, d.reason)
        span_id = None
        if L["quote_source"] == "outline" and L["page"]:
            sp = make_span(db, dv, L["page"], L["quote"], raw=raw_pdf)
            span_id = str(sp.id) if sp else None
        iid = f"l:{L['source']['ref']}>{L['target']['ref']}:{L['rel_type']}"
        link = {k: L[k] for k in ("source", "target", "rel_type", "basis", "level", "strength", "rationale")}
        link["model_strength"] = L["model_strength"]
        items.append(
            _item(
                iid,
                "links",
                "link",
                f"{_short(L['source'])} {VERB[L['rel_type']]} {_short(L['target'])}",
                d
                if not L.get("note")
                else policy.Decision(d.selectable, d.default, f"{d.reason} {L['note']}", d.blocked),
                link=link,
                evidence=[
                    {
                        "span_id": span_id,
                        "page": L["page"],
                        "quote": L["quote"],
                        "source": L["quote_source"],
                        "field": None,
                    }
                ],
                depends_on=depends,
                excludes=excludes,
                fills=_link_fills(L, missing_ids),
                agent={"decision": L["basis"], "reason": L["rationale"], "decided_by": "agent"},
            )
        )
        by_id[iid] = items[-1]

    # excludes are symmetric: a retire item excludes the links that need its element
    for it in items:
        for x in it["excludes"]:
            if it["id"] not in by_id[x]["excludes"]:
                by_id[x]["excludes"].append(it["id"])

    filled = {g for it in items for g in it["fills"]}
    unfillable = []
    for g in ctx.missing["items"]:
        if g["id"] in filled:
            continue
        why = UNFILLABLE_WHY.get(g["kind"]) or "This outline doesn't provide it."
        unfillable.append({**g, "why": why})

    dropped = [
        *({"what": f"{d['kind']}: {d['item']}", "reason": d["reason"]} for d in ctx.extraction_dropped),
        *match_drops,
        *link_drops,
    ]
    return {
        "course": {"id": cid, "key": ctx.code, "title": ctx.course["title"]},
        "document": {
            "id": str(dv.id),
            "filename": dv.original_filename,
            "mime_type": dv.mime_type,
            "academic_year": dv.academic_year,
        },
        "edition": {
            "outline": ctx.outline,
            "version": {
                "academic_year": ctx.snap.version.get("academic_year"),
                "start_year": ctx.version_year,
                "label": ctx.snap.version.get("label"),
            },
            "fit": ctx.fit,
        },
        "sections": [sections[s] for s in ("outcomes", "assessments", "topics")],
        "items": items,
        "missing_before": ctx.missing["items"],
        "program_missing": ctx.missing["program"],
        "unfillable": unfillable,
        "warnings": _warnings(ctx, raw, options),
        "dropped": dropped,
        "notes": [str(n) for n in (raw.get("notes") or [])][:10],
        "agent": {"model_run_id": run_id, "model": model},
    }


def plan_hash(plan: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(plan["items"], sort_keys=True, default=str).encode()).hexdigest()


def etag(state_hash: str | None, p_hash: str | None) -> str:
    return hashlib.sha256(f"{state_hash}:{p_hash}".encode()).hexdigest()[:32]


def default_selection(plan: dict[str, Any]) -> list[str]:
    return [i["id"] for i in plan["items"] if i["default_selected"]]
