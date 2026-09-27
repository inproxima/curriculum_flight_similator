"""Requirement expressions: AND/OR/credits/consent/concurrency, never flattening OR."""

from cfs.curriculum.rules import (
    AndExpr,
    Availability,
    EvalContext,
    OrExpr,
    Sat,
    Unparsed,
    evaluate,
    leaf_edges,
    parse_requisite_text,
    render,
)

A = Availability


def ctx(**av):
    return EvalContext({k.replace("_", " "): v for k, v in av.items()}, {k.replace("_", " "): 3.0 for k in av})


def test_and_or_structure_preserved():
    e = parse_requisite_text("SYN 201 and (SYN 211 or SYN 212)")
    assert isinstance(e, AndExpr) and isinstance(e.items[1], OrExpr)
    leaves = leaf_edges(e)
    alt = {ref.code: in_or for ref, _p, in_or in leaves}
    assert alt == {"SYN 201": False, "SYN 211": True, "SYN 212": True}  # OR members flagged as alternatives


def test_or_not_flattened_into_mandatory():
    e = parse_requisite_text("SYN 211 or SYN 212")
    # only one alternative available → still satisfied
    assert evaluate(e, ctx(SYN_211=A.REQUIRED_BEFORE, SYN_212=A.ABSENT)).sat == Sat.ALL
    and_e = parse_requisite_text("SYN 211 and SYN 212")
    assert evaluate(and_e, ctx(SYN_211=A.REQUIRED_BEFORE, SYN_212=A.ABSENT)).sat == Sat.NONE


def test_elective_only_is_conditional():
    e = parse_requisite_text("one of SYN 211, SYN 212")
    assert evaluate(e, ctx(SYN_211=A.ELECTIVE_BEFORE, SYN_212=A.ELECTIVE_BEFORE)).sat == Sat.SOME


def test_concurrency_only_when_documented():
    plain = parse_requisite_text("SYN 230")
    conc = parse_requisite_text("SYN 230, which may be taken concurrently")
    c = ctx(SYN_230=A.CONCURRENT)
    assert evaluate(plain, c).sat == Sat.NONE
    assert evaluate(conc, c).sat == Sat.ALL


def test_consent_and_unparsed_are_unknown_not_guessed():
    assert evaluate(parse_requisite_text("Consent of the program director"), ctx()).sat == Sat.UNKNOWN
    u = parse_requisite_text("completion of first-year requirements with a GPA of 3.0")
    assert isinstance(u, Unparsed)
    assert evaluate(u, ctx()).sat == Sat.UNKNOWN


def test_credit_minimum_with_unresolved_scope_is_unknown():
    e = parse_requisite_text("6 units in Synthetic Biology and SYN 101")
    assert "6 credits from Synthetic Biology" in render(e)
    assert evaluate(e, ctx(SYN_101=A.REQUIRED_BEFORE)).sat == Sat.UNKNOWN
