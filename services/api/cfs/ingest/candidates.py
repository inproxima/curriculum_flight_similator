"""Deterministic candidate extraction from page text (no model involved).

Recognizes common program-outline conventions: course headers ("ABCD 123: Title"), "Credits:",
"Year N, Term", "Prerequisite(s):", "Corequisite(s):", "X requires Y" statements, outcome lines
("KEY: statement (Program outcomes: PLO1 Introduced)"), and other course-code mentions.

Every candidate carries the page number and a verbatim quote that is later validated against the
stored page text before any evidence span is created. Nothing is inferred from general knowledge:
credits, terms, and requisites are only emitted when the text states them.
"""

from __future__ import annotations

import re
from typing import Any

from cfs.curriculum.rules import COURSE_CODE_RE, dump_expr, normalize_code, parse_requisite_text

EXTRACTOR_VERSION = "candidates-1.0"

HEADER_RE = re.compile(r"^\s*([A-Z]{2,5}\s?\d{3}[A-Z]?)\s*(?::|\s[-–—]\s)\s*(.{3,160}?)\s*$")
CREDITS_RE = re.compile(r"^\s*(?:Credits|Units)\s*:\s*(\d+(?:\.\d+)?)\s*$", re.I)
PLACE_RE = re.compile(r"^\s*Year\s+(\d)\s*,\s*(Fall|Winter|Spring|Summer|Term not stated)\b[^.]*\.\s*(.*)$", re.I)
PREREQ_RE = re.compile(r"^\s*Pre-?requisites?(?:\(s\))?\s*:\s*(.+?)\s*$", re.I)
COREQ_RE = re.compile(r"^\s*Co-?requisites?(?:\(s\))?\s*:\s*(.+?)\s*$", re.I)
ANTIREQ_RE = re.compile(r"^\s*Anti-?requisites?(?:\(s\))?\s*:\s*(.+?)\s*$", re.I)
DESC_RE = re.compile(r"^\s*Description\s*:\s*(.+)$", re.I)
OUTCOME_RE = re.compile(r"^\s*([A-Z0-9]{3,12}-CO\d+)\s*:\s*(.+?)(?:\s*\(Program outcomes:\s*([^)]*)\))?\s*$")
REQUIRES_RE = re.compile(r"([A-Z]{2,5}\s?\d{3}[A-Z]?)\s+requires\s+(.+?)\.(?:\s|$)")
ASSESS_RE = re.compile(
    r"^\s*([A-Z0-9]{3,12}-A\d+)\s*:\s*(.+?)\s*"
    r"\(([^,()]+),\s*(\d+(?:\.\d+)?)%\s*,\s*(?:week\s+(\d+)|timing not stated)\)\.?"
    r"(?:\s*Assesses:\s*([A-Z0-9, -]+?)\.?)?\s*$",
    re.I,
)
FOOTER_RE = re.compile(r"(?:^|\s+)Page\s+\d+(?:\s+of\s+\d+)?\s*$", re.I)
KEYED_START_RE = re.compile(r"^\s*[A-Z0-9]{3,12}-(?:CO|A)\d+\s*:")
LEVEL_WORDS = {"introduced": "introduce", "reinforced": "reinforce", "assessed": "assess"}


def _strip_non_code_parens(s: str) -> str:
    return re.sub(r"\([^()]*\)", lambda m: m.group(0) if COURSE_CODE_RE.search(m.group(0)) else "", s).strip()


def _joined_lines(text: str) -> list[str]:
    """Join soft-wrapped lines: a line that does not start a recognizable item continues the previous one
    when the previous line is unterminated, the line starts lowercase, or it is an "Assesses:" tail."""
    starters = [HEADER_RE, CREDITS_RE, PLACE_RE, PREREQ_RE, COREQ_RE, ANTIREQ_RE, DESC_RE, KEYED_START_RE]
    out: list[str] = []
    for raw in text.splitlines():
        line = FOOTER_RE.sub("", raw.rstrip())
        if not line.strip():
            continue
        stripped = line.strip()
        is_start = any(r.match(line) for r in starters) or stripped in {"Learning outcomes", "Assessments"}
        prev_open = bool(out) and not out[-1].rstrip().endswith((".", ")")) and not HEADER_RE.match(out[-1])
        if out and not is_start and (prev_open or stripped[:1].islower() or stripped.startswith("Assesses:")):
            out[-1] = out[-1] + " " + stripped
        else:
            out.append(line)
    return out


def extract_candidates(pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
    cands: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    headers: set[str] = set()

    for page_no, text in pages:
        for line in _joined_lines(text):
            if m := HEADER_RE.match(line):
                code = normalize_code(m.group(1))
                current = {
                    "kind": "course",
                    "code": code,
                    "title": m.group(2).strip(),
                    "page": page_no,
                    "quote": line.strip(),
                    "fields": {},
                }
                cands.append(current)
                headers.add(code)
                continue
            if current is not None:
                if m := CREDITS_RE.match(line):
                    current["fields"]["credits"] = {"value": float(m.group(1)), "page": page_no, "quote": line.strip()}
                    continue
                if m := PLACE_RE.match(line):
                    term = m.group(2).lower()
                    current["fields"]["year"] = {"value": int(m.group(1)), "page": page_no, "quote": line.strip()}
                    current["fields"]["term"] = {
                        "value": "unknown" if "not" in term else term,
                        "page": page_no,
                        "quote": line.strip(),
                    }
                    rest = m.group(3).lower()
                    cls = (
                        "pathway_required"
                        if "pathway" in rest
                        else "required"
                        if "required" in rest
                        else "elective"
                        if "elective" in rest
                        else "optional"
                        if "optional" in rest
                        else None
                    )
                    if cls:
                        current["fields"]["classification"] = {"value": cls, "page": page_no, "quote": line.strip()}
                    continue
                if m := DESC_RE.match(line):
                    current["fields"]["description"] = {
                        "value": m.group(1).strip(),
                        "page": page_no,
                        "quote": line.strip(),
                    }
                    continue
                for regex, kind in (
                    (PREREQ_RE, "prerequisite"),
                    (COREQ_RE, "corequisite"),
                    (ANTIREQ_RE, "antirequisite"),
                ):
                    if m := regex.match(line):
                        clause = m.group(1).strip()
                        expr = parse_requisite_text(_strip_non_code_parens(clause))
                        cands.append(
                            {
                                "kind": "requisite",
                                "rule_kind": kind,
                                "target": current["code"],
                                "text": clause,
                                "expression": dump_expr(expr),
                                "page": page_no,
                                "quote": line.strip(),
                            }
                        )
                        break
                else:
                    if m := ASSESS_RE.match(line):
                        cands.append(
                            {
                                "kind": "assessment",
                                "course": current["code"],
                                "key": m.group(1),
                                "title": m.group(2).strip(),
                                "format": m.group(3).strip(),
                                "weight": float(m.group(4)),
                                "week": int(m.group(5)) if m.group(5) else None,
                                "assesses": [x.strip() for x in (m.group(6) or "").split(",") if x.strip()],
                                "page": page_no,
                                "quote": line.strip(),
                            }
                        )
                        continue
                    if m := OUTCOME_RE.match(line):
                        contributes = []
                        for part in (m.group(3) or "").split(";"):
                            bits = part.strip().split()
                            if len(bits) == 2 and bits[1].lower() in LEVEL_WORDS:
                                contributes.append([bits[0], LEVEL_WORDS[bits[1].lower()]])
                        cands.append(
                            {
                                "kind": "outcome",
                                "course": current["code"],
                                "key": m.group(1),
                                "statement": m.group(2).strip(),
                                "contributes": contributes,
                                "page": page_no,
                                "quote": line.strip(),
                            }
                        )
                        continue
            for m in REQUIRES_RE.finditer(line):
                target = normalize_code(m.group(1))
                clause = m.group(2)
                expr = parse_requisite_text(_strip_non_code_parens(clause))
                cands.append(
                    {
                        "kind": "requisite",
                        "rule_kind": "prerequisite",
                        "target": target,
                        "text": clause,
                        "expression": dump_expr(expr),
                        "page": page_no,
                        "quote": m.group(0).strip(),
                    }
                )

    # Mentions of course codes that never appear as a header in this document (uncertain matches).
    seen: set[str] = set()
    for page_no, text in pages:
        for m in COURSE_CODE_RE.finditer(text):
            code = f"{m.group(1)} {m.group(2)}"
            if code in headers or code in seen:
                continue
            seen.add(code)
            start = max(0, text.rfind("\n", 0, m.start()) + 1)
            end = text.find("\n", m.end())
            cands.append(
                {
                    "kind": "mention",
                    "code": code,
                    "page": page_no,
                    "quote": text[start : end if end != -1 else len(text)].strip(),
                }
            )
    return cands
