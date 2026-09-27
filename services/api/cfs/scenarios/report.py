"""Readable scenario report (Markdown, or HTML rendered from it). Sources, assumptions, changes, findings."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.models import CurriculumSource, Document, DocumentVersion, EvidenceSpan, Scenario

CLASS_TITLE = {
    "direct_documented": "Direct documented consequences",
    "indirect_potential": "Indirect potential consequences",
    "uncertain_inferred": "Uncertain consequences (rely on inferred links)",
    "judgment_needed": "Recommendations and items requiring judgment",
    "missing_evidence": "Where precise impact cannot be established (missing evidence)",
}


def build_markdown(
    db: Session, s: Scenario, scenario: dict, changes: list[dict], run: dict | None, version: dict
) -> str:
    L: list[str] = []
    L.append(f"# Scenario report: {s.title}")
    if version.get("is_synthetic"):
        L.append(
            "\n> **SYNTHETIC FIXTURE DATA** — this report is based on invented test data, not University of "
            "Calgary curriculum data.\n"
        )
    L.append(f"- Base curriculum version: **{version['label']}** ({version['status']}) — {version['program_name']}")
    L.append(f"- Scenario revision: {s.revision} · state: {s.state.value}")
    if s.description:
        L.append(f"- Description: {s.description}")
    L.append(
        "- Scope: curriculum decision support. This report does not predict learning gains, certify accreditation "
        "compliance, or describe individual students' mastery."
    )
    L.append("\n## Changes (in order)\n")
    for c in changes:
        mark = "" if c["applied"] else " *(undone — not applied)*"
        L.append(f"{c['seq']}. **{c['summary']}**{mark}")
        if c["before"] is not None:
            L.append(f"   - Before: `{_short(c['before'])}`")
        if c["after"] is not None:
            L.append(f"   - After: `{_short({k: v for k, v in c['after'].items() if k != 'summary'})}`")
        for a in c["assumptions"]:
            L.append(f"   - Assumption: {a}")
    if not changes:
        L.append("_No changes._")
    L.append("\n## Analysis\n")
    if run is None:
        L.append("_No analysis has been run for this scenario._")
    else:
        if run["stale"]:
            L.append(
                "> **STALE**: this analysis evaluated an earlier scenario revision. Re-run before relying on it.\n"
            )
        L.append(
            f"- Engine: `{run['algorithm_version']}` · evaluated revision {run['scenario_revision']} · "
            f"input hash `{run['input_hash'][:16]}…`"
        )
        cov = (run["summary"] or {}).get("coverage", {})
        if cov:
            b, sc = cov["baseline"], cov["scenario"]
            L.append(
                f"- Program-outcome coverage (required path): baseline {b['numerator']}/{b['denominator']}, "
                f"scenario {sc['numerator']}/{sc['denominator']}. Definition: {b['definition']}"
            )
        wl = (run["summary"] or {}).get("workload")
        if wl:
            L.append(
                f"- Workload: {wl['note']} Scenario: known {wl['scenario']['known_hours']:g} h; estimated "
                f"{wl['scenario']['estimated']['low']:g}–{wl['scenario']['estimated']['high']:g} h; "
                f"{wl['scenario']['unquantified']} components unquantified."
            )
        cg = (run["summary"] or {}).get("congestion")
        if cg:
            L.append(f"- Assessment congestion: {cg['note']}")
        span_ids = {e["evidence_span_id"] for f in run["findings"] for e in f["evidence"] if e["evidence_span_id"]}
        spans = _spans(db, span_ids)
        for cls, title in CLASS_TITLE.items():
            fs = [f for f in run["findings"] if f["consequence_class"] == cls]
            if not fs:
                continue
            L.append(f"\n### {title} ({len(fs)})\n")
            for f in fs:
                L.append(f"- **[{f['severity']}] {f['title']}** — {f['explanation']}")
                for pth in f["paths"]:
                    L.append(f"  - Path: {' → '.join(pth['labels'])}")
                for a in f["assumptions"]:
                    L.append(f"  - Assumption: {a}")
                for a in f["suggested_actions"]:
                    L.append(f"  - Suggested: {a}")
                for e in f["evidence"]:
                    sp = spans.get(e["evidence_span_id"]) if e["evidence_span_id"] else None
                    if sp:
                        L.append(f"  - Source: “{sp['quote']}” — {sp['title']}, p. {sp['page']}")
                basis = {"explicit_statement": "documented", "interpretation": "inferred", "assumption": "assumption"}
                L.append(f"  - Basis: {basis.get(f['evidence_basis'], f['evidence_basis'])}")
    L.append("\n## Sources assigned to the base version\n")
    rows = db.execute(
        select(CurriculumSource, DocumentVersion, Document)
        .join(DocumentVersion, DocumentVersion.id == CurriculumSource.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(CurriculumSource.curriculum_version_id == s.base_version_id)
    ).all()
    for src, dv, doc in rows:
        L.append(
            f"- {doc.title} ({doc.document_type.value}; {src.authority.value}) — published "
            f"{dv.publication_date or 'unknown'}, retrieved {dv.retrieval_date or 'unknown'}, academic year "
            f"{dv.academic_year or 'not recorded'}{' — SYNTHETIC' if dv.is_synthetic else ''}"
        )
    if not rows:
        L.append("_No sources assigned._")
    return "\n".join(L) + "\n"


def _short(v: Any) -> str:
    s = str(v)
    return s if len(s) < 200 else s[:197] + "…"


def _spans(db: Session, ids: set[str]) -> dict[str, dict]:
    import uuid

    out = {}
    if not ids:
        return out
    for sp, _dv, doc in db.execute(
        select(EvidenceSpan, DocumentVersion, Document)
        .join(DocumentVersion, DocumentVersion.id == EvidenceSpan.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(EvidenceSpan.id.in_([uuid.UUID(i) for i in ids]))
    ):
        out[str(sp.id)] = {"quote": sp.quote, "page": sp.page_number, "title": doc.title}
    return out


def to_html(md: str, title: str) -> str:
    import html

    import markdown

    body = markdown.markdown(html.escape(md, quote=False).replace("&gt; ", "> "), extensions=["extra"])
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:2em auto;line-height:1.45;color:#1d2433}"
        "blockquote{background:#fff4e0;border-left:4px solid #d69a00;margin:0;padding:.4em 1em}"
        "code{background:#f1f4f8;padding:0 3px}</style></head><body>" + body + "</body></html>"
    )
