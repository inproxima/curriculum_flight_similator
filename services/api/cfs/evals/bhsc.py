"""Score extraction (latest model output, after deterministic verification) and retrieval against the
human-built gold standard in fixtures/eval/bhsc_gold.yaml.   python -m cfs.evals.bhsc [--retrieval]

Extraction scoring reuses the stored model output of the most recent successful `document_extraction` run for
each document (no new model calls). Retrieval scoring runs the real hybrid search (one embedding call per query).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml
from sqlalchemy import select

from cfs.ai.extraction import validate_extraction
from cfs.core.db import get_sessionmaker
from cfs.curriculum.rules import normalize_code
from cfs.models import CurriculumSource, DocumentPage, DocumentVersion, ModelRun


def _gold() -> dict:
    here = Path(__file__).resolve()
    for p in [here, *here.parents]:
        f = p / "fixtures" / "eval" / "bhsc_gold.yaml"
        if f.exists():
            return yaml.safe_load(f.read_text())
    raise FileNotFoundError("fixtures/eval/bhsc_gold.yaml")


def prf(pred: set, gold: set) -> tuple[float | None, float | None, list, list]:
    tp = len(pred & gold)
    p = tp / len(pred) if pred else None
    r = tp / len(gold) if gold else None
    return p, r, sorted(pred - gold), sorted(gold - pred)


def fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def extraction_report(db) -> list[str]:
    g = _gold()
    lines = ["| Document | Task | Precision | Recall | False positives | Missed |", "|---|---|---:|---:|---|---|"]
    for key, doc in g["documents"].items():
        dv = db.scalar(select(DocumentVersion).where(DocumentVersion.original_filename == doc["filename"])
                       .order_by(DocumentVersion.created_at.desc()))
        if dv is None:
            lines.append(f"| {key} | — | document not imported | | | |")
            continue
        run = db.scalar(select(ModelRun).where(ModelRun.purpose == "document_extraction", ModelRun.status == "ok",
                                               ModelRun.evidence_ids.is_not(None), ModelRun.organization_id == dv.organization_id,
                                               ModelRun.cache_key.is_not(None))
                        .where(ModelRun.output["parsed"]["document"].is_not(None))
                        .order_by(ModelRun.created_at.desc())
                        .filter(ModelRun.id.in_(_runs_for(db, dv))))
        if run is None:
            lines.append(f"| {key} | — | no extraction run | | | |")
            continue
        pages = {p.page_number: p.text for p in db.scalars(select(DocumentPage).where(
            DocumentPage.document_version_id == dv.id))}
        cands, report = validate_extraction(run.output["parsed"], pages)
        courses = [c for c in cands if c["kind"] == "course"]
        pc = {c["code"] for c in courses}
        gc = {normalize_code(k) for k in doc["courses"]}
        lines.append(_row(key, "Courses (code)", *prf(pc, gc)))
        py = {(c["code"], c["fields"]["year"]["value"]) for c in courses if "year" in c["fields"]}
        gy = {(normalize_code(k), v) for k, v in doc["courses"].items()}
        lines.append(_row(key, "Course + year", *prf(py, gy)))
        pf = {c["code"] for c in courses if c["fields"].get("term", {}).get("value") == "full_year"}
        lines.append(_row(key, "Full-year term", *prf(pf, set(doc["full_year"]))))
        pcr = {(c["code"], c["fields"]["credits"]["value"]) for c in courses if "credits" in c["fields"]}
        lines.append(_row(key, "Credits", *prf(pcr, {(k, float(v)) for k, v in doc["credits"].items()})))
        po = {f"{c['name'].title()}|{c['year']}": c.get("slot_count") for c in cands if c["kind"] == "option_group"}
        go = doc["option_slots"] or {}
        lines.append(_row(key, "Option rule (name+year)", *prf(set(po), set(go))))
        slots_ok = sum(1 for k, v in go.items() if po.get(k) == v)
        lines.append(f"| {key} | Option slot counts | | {slots_ok}/{len(go)} correct | | |" if go else
                     f"| {key} | Option slot counts | n/a | n/a | | |")
        pp = {c["label"].lower() for c in cands if c["kind"] == "program_outcome"}
        lines.append(_row(key, "Program outcome labels", *prf(pp, {x.lower() for x in doc["program_outcomes"]})))
        lines.append(f"| {key} | Verifier drops | | | {len(report['dropped'])} model items rejected | |")
    return lines


def _runs_for(db, dv) -> list:
    from cfs.ai.extraction import EXTRACT_PROMPT_VERSION, EXTRACT_SCHEMA_VERSION
    from cfs.ai.gateway import cache_key
    from cfs.ingest.pipeline import PIPELINE_VERSION

    key = cache_key("extract", EXTRACT_PROMPT_VERSION, EXTRACT_SCHEMA_VERSION, dv.sha256, PIPELINE_VERSION)
    return list(db.scalars(select(ModelRun.id).where(ModelRun.cache_key == key)))


def _row(doc, task, p, r, fp, fn) -> str:
    return f"| {doc} | {task} | {fmt(p)} | {fmt(r)} | {', '.join(map(str, fp)) or '—'} | {', '.join(map(str, fn)) or '—'} |"


def retrieval_report(db) -> list[str]:
    from cfs.ai.retrieval import search

    g = _gold()
    dv_names = {dv.id: dv.original_filename for dv in db.scalars(select(DocumentVersion))}
    versions = {s.curriculum_version_id for s in db.scalars(select(CurriculumSource)) if
                dv_names.get(s.document_version_id, "").startswith(("2025-2026", "Biomedical_Sciences", "Faculty"))}
    lines = ["| Query | Version | Mode | Hit@3 | Hit@5 | First relevant rank |", "|---|---|---|---:|---:|---:|"]
    hits3 = hits5 = total = 0
    for v in sorted(versions, key=str):
        org = db.scalar(select(DocumentVersion.organization_id).limit(1))
        for item in g["retrieval"]:
            res = search(db, org, v, item["q"], limit=5)
            ranks = [i + 1 for i, r in enumerate(res["results"])
                     for (prefix, page) in item["expect"]
                     if dv_names[__import__("uuid").UUID(r["document_version_id"])].startswith(prefix)
                     and r["pages"][0] <= page <= r["pages"][1]]
            first = min(ranks) if ranks else None
            relevant_here = any(dv_names[s.document_version_id].startswith(p) for s in db.scalars(select(
                CurriculumSource).where(CurriculumSource.curriculum_version_id == v)) for p, _ in item["expect"])
            if not relevant_here:
                continue
            total += 1
            hits3 += bool(first and first <= 3)
            hits5 += bool(first and first <= 5)
            lines.append(f"| {item['q']} | {str(v)[:8]} | {res['mode']} | {'✓' if first and first <= 3 else '✗'} | "
                         f"{'✓' if first and first <= 5 else '✗'} | {first or '—'} |")
    lines.append(f"| **Total** | | | **{hits3}/{total}** | **{hits5}/{total}** | |")
    return lines


if __name__ == "__main__":
    s = get_sessionmaker()()
    try:
        print("\n".join(extraction_report(s)))
        if "--retrieval" in sys.argv:
            print()
            print("\n".join(retrieval_report(s)))
    finally:
        s.close()
