"""Requirement-rule expressions (prerequisites, co-requisites, exclusions).

Rules are explicit trees. An OR requirement is never flattened into several mandatory edges.
Evaluation is three-valued plus a "conditional" state for pathway- or elective-dependent satisfaction:

    NONE < UNKNOWN < SOME < ALL

  ALL      every student on the evaluated path satisfies it through required, earlier courses
  SOME     satisfiable, but only via electives / pathway-specific courses (not universal preparation)
  UNKNOWN  cannot be determined from documented data (unknown timing, consent clauses, unparsed text)
  NONE     cannot be satisfied (referenced course absent, or only offered at/after the dependent course)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


class CourseRef(BaseModel):
    op: Literal["course"] = "course"
    code: str
    entity_id: uuid.UUID | None = None
    concurrent_allowed: bool = False  # "may be taken concurrently" (a permitted co-requisite)


class AndExpr(BaseModel):
    op: Literal["and"] = "and"
    items: list[Expr]


class OrExpr(BaseModel):
    op: Literal["or"] = "or"
    items: list[Expr]


class MinCredits(BaseModel):
    op: Literal["min_credits"] = "min_credits"
    credits: float
    from_codes: list[str] = Field(default_factory=list)  # empty = any course
    from_group: str | None = None


class NotExpr(BaseModel):
    """Exclusion / antirequisite: satisfied when the item is NOT completed."""

    op: Literal["not"] = "not"
    item: Expr


class ConsentExpr(BaseModel):
    op: Literal["consent"] = "consent"
    text: str = "Consent of the instructor"


class Unparsed(BaseModel):
    op: Literal["unparsed"] = "unparsed"
    text: str


Expr = Annotated[
    Union[CourseRef, AndExpr, OrExpr, MinCredits, NotExpr, ConsentExpr, Unparsed], Field(discriminator="op")
]
AndExpr.model_rebuild()
OrExpr.model_rebuild()
NotExpr.model_rebuild()
ExprAdapter: TypeAdapter[Expr] = TypeAdapter(Expr)


def parse_expr(data: dict) -> Expr:
    return ExprAdapter.validate_python(data)


def dump_expr(e: Expr) -> dict:
    return ExprAdapter.dump_python(e, mode="json", exclude_none=True)


# ───────────────────────────── text parsing ─────────────────────────────

COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,5})\s?(\d{3}[A-Z]?)\b")


def normalize_code(s: str) -> str:
    m = COURSE_CODE_RE.search(s.upper())
    return f"{m.group(1)} {m.group(2)}" if m else s.strip().upper()


def parse_requisite_text(text: str) -> Expr:
    """Parse a documented requisite clause into an expression.

    Supports: codes joined by "and"/"or"/","/";", parentheses, "one of X, Y", "N units/credits",
    "consent of", "may be taken concurrently". Anything else becomes Unparsed (never guessed).
    """
    raw = text.strip().rstrip(".")
    if not raw:
        return Unparsed(text=text)
    try:
        tokens = _tokenize(raw)
        parser = _Parser(tokens)
        expr = parser.parse_or()
        if parser.pos != len(tokens):
            return Unparsed(text=raw)
        if re.search(r"concurrent", raw, re.I):
            _mark_concurrent(expr)
        return expr
    except _ParseError:
        return Unparsed(text=raw)


class _ParseError(Exception):
    pass


_TOKEN_RE = re.compile(
    r"\s*(?:(?P<conc>[,;]?\s*(?:either\s+of\s+)?(?:which\s+)?(?:may|can) be taken concurrently)"
    r"|(?P<code>[A-Z]{2,5}\s?\d{3}[A-Z]?)|(?P<lp>\()|(?P<rp>\))|(?P<and>\band\b|;)|(?P<or>\bor\b)"
    r"|(?P<comma>,)|(?P<oneof>\bone of\b)"
    r"|(?P<credits>(?P<n>\d+(?:\.\d+)?)\s+(?:units|credits)(?:\s+(?:in|of|from)\s+(?P<scope>[\w\s-]+?))?"
    r"(?=$|[,;)]|\s+(?:and|or)\b))"
    r"|(?P<consent>(?:the\s+)?consent of [\w\s]+)|(?P<filler>\b(?:both|either)\b))",
    re.I,
)


def _tokenize(s: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(s):
        if s[pos:].strip() == "":
            break
        m = _TOKEN_RE.match(s, pos)
        if not m or m.end() == pos:
            raise _ParseError(s[pos:])
        kind = m.lastgroup
        if kind in ("n", "scope"):
            kind = "credits"
        val = m.group(kind) if kind else ""
        if kind == "code":
            val = normalize_code(val)
        if kind == "credits":
            val = m.group("n") + "|" + (m.group("scope") or "").strip()
        if kind not in ("conc", "filler"):
            out.append((kind, val))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.t = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.t[self.pos][0] if self.pos < len(self.t) else None

    def take(self) -> tuple[str, str]:
        tok = self.t[self.pos]
        self.pos += 1
        return tok

    def parse_or(self) -> Expr:
        items = [self.parse_and()]
        while self.peek() == "or":
            self.take()
            items.append(self.parse_and())
        return items[0] if len(items) == 1 else OrExpr(items=items)

    def parse_and(self) -> Expr:
        items = [self.parse_atom()]
        while self.peek() in ("and", "comma"):
            self.take()
            if self.peek() == "or":  # "A, B, or C" → handled by caller as OR list
                return self._comma_or(items)
            items.append(self.parse_atom())
        return items[0] if len(items) == 1 else AndExpr(items=items)

    def _comma_or(self, items: list[Expr]) -> Expr:
        self.take()  # or
        items.append(self.parse_atom())
        return OrExpr(items=items)

    def parse_atom(self) -> Expr:
        kind = self.peek()
        if kind == "code":
            return CourseRef(code=self.take()[1])
        if kind == "lp":
            self.take()
            e = self.parse_or()
            if self.peek() != "rp":
                raise _ParseError("missing )")
            self.take()
            return e
        if kind == "oneof":
            self.take()
            items = [self.parse_atom()]
            while self.peek() in ("comma", "or"):
                self.take()
                if self.peek() == "or":
                    self.take()
                items.append(self.parse_atom())
            return OrExpr(items=items)
        if kind == "credits":
            n, scope = self.take()[1].split("|", 1)
            return MinCredits(credits=float(n), from_group=scope or None)
        if kind == "consent":
            return ConsentExpr(text=self.take()[1].strip())
        raise _ParseError(f"unexpected {kind}")


def _mark_concurrent(e: Expr) -> None:
    # Documented phrase "may be taken concurrently" applies to the course refs in the clause.
    for ref in iter_course_refs(e):
        ref.concurrent_allowed = True


def iter_course_refs(e: Expr, _negated: bool = False):
    if isinstance(e, CourseRef):
        yield e
    elif isinstance(e, (AndExpr, OrExpr)):
        for i in e.items:
            yield from iter_course_refs(i)
    elif isinstance(e, NotExpr):
        yield from iter_course_refs(e.item)


def leaf_edges(e: Expr, path: str = "r") -> list[tuple[CourseRef, str, bool]]:
    """(course ref, group path, alternatives_exist) for each positive leaf.

    group path identifies the OR/AND structure so the UI can draw OR-alternatives distinctly.
    """
    out: list[tuple[CourseRef, str, bool]] = []

    def walk(x: Expr, p: str, in_or: bool) -> None:
        if isinstance(x, CourseRef):
            out.append((x, p, in_or))
        elif isinstance(x, AndExpr):
            for i, it in enumerate(x.items):
                walk(it, f"{p}.and{i}", in_or)
        elif isinstance(x, OrExpr):
            for i, it in enumerate(x.items):
                walk(it, f"{p}.or{i}", True)

    walk(e, path, False)
    return out


def render(e: Expr) -> str:
    if isinstance(e, CourseRef):
        return e.code + (" (concurrent allowed)" if e.concurrent_allowed else "")
    if isinstance(e, AndExpr):
        return "(" + " AND ".join(render(i) for i in e.items) + ")"
    if isinstance(e, OrExpr):
        return "(" + " OR ".join(render(i) for i in e.items) + ")"
    if isinstance(e, MinCredits):
        scope = (
            f" from {', '.join(e.from_codes)}" if e.from_codes else (f" from {e.from_group}" if e.from_group else "")
        )
        return f"{e.credits:g} credits{scope}"
    if isinstance(e, NotExpr):
        return f"NOT {render(e.item)}"
    if isinstance(e, ConsentExpr):
        return e.text
    return f"[unparsed: {e.text}]"


# ───────────────────────────── evaluation ─────────────────────────────


class Sat(IntEnum):
    NONE = 0
    UNKNOWN = 1
    SOME = 2
    ALL = 3


class Availability(IntEnum):
    ABSENT = 0  # not in the curriculum (or removed in scenario)
    AFTER = 1  # offered only after the dependent course
    UNKNOWN_TIMING = 2
    CONCURRENT = 3  # same term
    ELECTIVE_BEFORE = 4  # earlier, but elective / pathway-specific
    REQUIRED_BEFORE = 5  # earlier and required on the evaluated path


@dataclass
class EvalContext:
    availability: dict[str, Availability]  # by course code
    credits: dict[str, float] = field(default_factory=dict)


@dataclass
class EvalResult:
    sat: Sat
    reasons: list[str]


def evaluate(e: Expr, ctx: EvalContext) -> EvalResult:
    if isinstance(e, CourseRef):
        a = ctx.availability.get(e.code, Availability.ABSENT)
        if a == Availability.REQUIRED_BEFORE:
            return EvalResult(Sat.ALL, [f"{e.code} is a required earlier course"])
        if a == Availability.ELECTIVE_BEFORE:
            return EvalResult(Sat.SOME, [f"{e.code} is earlier but elective or pathway-specific"])
        if a == Availability.CONCURRENT:
            if e.concurrent_allowed:
                return EvalResult(Sat.ALL, [f"{e.code} is concurrent, which the rule permits"])
            return EvalResult(
                Sat.NONE, [f"{e.code} is scheduled in the same term but the rule does not permit concurrency"]
            )
        if a == Availability.UNKNOWN_TIMING:
            return EvalResult(Sat.UNKNOWN, [f"{e.code} timing is not documented"])
        if a == Availability.AFTER:
            return EvalResult(Sat.NONE, [f"{e.code} is scheduled after the dependent course"])
        return EvalResult(Sat.NONE, [f"{e.code} is not in this curriculum"])
    if isinstance(e, AndExpr):
        parts = [evaluate(i, ctx) for i in e.items]
        return EvalResult(Sat(min(p.sat for p in parts)), [r for p in parts for r in p.reasons])
    if isinstance(e, OrExpr):
        parts = [evaluate(i, ctx) for i in e.items]
        best = max(p.sat for p in parts)
        return EvalResult(Sat(best), [r for p in parts for r in p.reasons])
    if isinstance(e, MinCredits):
        if e.from_group and not e.from_codes:
            return EvalResult(Sat.UNKNOWN, [f"credit scope '{e.from_group}' is not resolved to specific courses"])
        pool = e.from_codes or list(ctx.availability.keys())
        sure = sum(ctx.credits.get(c, 0) for c in pool if ctx.availability.get(c) == Availability.REQUIRED_BEFORE)
        maybe = sure + sum(
            ctx.credits.get(c, 0) for c in pool if ctx.availability.get(c) == Availability.ELECTIVE_BEFORE
        )
        missing_credit_data = any(c not in ctx.credits for c in pool if ctx.availability.get(c, 0) >= 4)
        if sure >= e.credits:
            return EvalResult(Sat.ALL, [f"{sure:g} documented credits available from required courses"])
        if missing_credit_data:
            return EvalResult(Sat.UNKNOWN, ["credit values are not documented for some courses"])
        if maybe >= e.credits:
            return EvalResult(Sat.SOME, ["credit minimum reachable only with electives"])
        return EvalResult(Sat.NONE, [f"only {maybe:g} of {e.credits:g} credits available"])
    if isinstance(e, NotExpr):
        inner = evaluate(e.item, ctx)
        # An exclusion restricts enrolment; it is not preparation. Unknown unless the item is universal.
        if inner.sat == Sat.ALL:
            return EvalResult(Sat.NONE, [f"excluded requirement is met by all students: {render(e.item)}"])
        return EvalResult(Sat.ALL, [f"exclusion {render(e.item)} does not block the required path"])
    if isinstance(e, ConsentExpr):
        return EvalResult(Sat.UNKNOWN, [f"'{e.text}' cannot be evaluated from documents"])
    return EvalResult(Sat.UNKNOWN, [f"unparsed requirement: {e.text}"])
