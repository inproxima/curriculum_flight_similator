"""Scenario persistence: typed change stack, optimistic concurrency, analysis runs, compare, rebase, publish."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cfs.core.audit import audit
from cfs.core.auth import Principal
from cfs.core.errors import AppError, Conflict
from cfs.graph.snapshot import Snapshot, load_snapshot
from cfs.models import (
    AnalysisRun,
    CurriculumVersion,
    Finding,
    FindingEvidence,
    FindingPath,
    Scenario,
    ScenarioChange,
)
from cfs.models.base import utcnow
from cfs.models.enums import (
    ConsequenceClass,
    EvidenceBasis,
    JobStatus,
    Role,
    ScenarioState,
    Severity,
    VersionStatus,
)
from cfs.scenarios.engine import ENGINE_VERSION, analyze, input_hash
from cfs.scenarios.ops import OpError, apply_op, diff, parse_op, project


def check_revision(s: Scenario, expected: int | None) -> None:
    if expected is None:
        raise AppError(
            "If-Match header with the scenario revision is required for edits", code="precondition_required", status=428
        )
    if expected != s.revision:
        raise Conflict(
            f"Scenario was changed elsewhere (current revision {s.revision}, you sent {expected}). "
            "Reload and re-apply your edit.",
            code="stale_revision",
            details={"current_revision": s.revision},
        )


def _bump(s: Scenario) -> None:
    s.revision += 1
    s.updated_at = utcnow()


def all_changes(db: Session, s: Scenario) -> list[ScenarioChange]:
    return list(
        db.scalars(select(ScenarioChange).where(ScenarioChange.scenario_id == s.id).order_by(ScenarioChange.seq))
    )


def applied(db: Session, s: Scenario) -> list[tuple[int, dict]]:
    return [(c.seq, c.payload) for c in all_changes(db, s) if c.seq <= s.head]


def projection(db: Session, s: Scenario) -> tuple[Snapshot, Snapshot, list[dict], dict]:
    base = load_snapshot(db, s.base_version_id)
    proj, results = project(base, applied(db, s), str(s.id))
    return base, proj, results, diff(base, proj)


def create(db: Session, p: Principal, base_version_id: uuid.UUID, title: str, description: str | None) -> Scenario:
    p.require(Role.editor)
    v = db.get(CurriculumVersion, base_version_id)
    if v is None or v.organization_id != p.org_id:
        raise AppError("Base version not found", status=404, code="not_found")
    s = Scenario(
        organization_id=p.org_id,
        base_version_id=v.id,
        title=title,
        description=description,
        owner_id=p.user_id,
        revision=1,
        head=0,
        workload_assumptions={},
    )
    db.add(s)
    db.flush()
    audit(db, p, "scenario.created", "scenario", s.id, {"base_version_id": str(v.id)})
    return s


def add_change(db: Session, p: Principal, s: Scenario, payload: dict[str, Any]) -> ScenarioChange:
    p.require(Role.editor)
    if s.state == ScenarioState.published:
        raise Conflict("Published scenarios cannot be edited")
    try:
        op = parse_op(payload)
    except ValidationError as e:
        raise AppError(
            "Invalid scenario change",
            code="invalid_change",
            status=422,
            details=e.errors(include_url=False, include_context=False),
        ) from e
    _base, proj, _res, _d = projection(db, s)
    seq = s.head + 1
    try:
        res = apply_op(proj, op, str(s.id), seq)
    except OpError as e:
        raise AppError(str(e), code="change_not_applicable", status=422) from e
    # new edit discards the redo stack
    db.execute(delete(ScenarioChange).where(ScenarioChange.scenario_id == s.id, ScenarioChange.seq > s.head))
    ch = ScenarioChange(
        scenario_id=s.id,
        seq=seq,
        op_type=op.op,
        target_entity_id=_uuid_or_none(res.get("target")),
        payload=op.model_dump(mode="json"),
        before=res["before"],
        after={**(res["after"] or {}), "summary": res["summary"]}
        if res["after"] is not None
        else {"summary": res["summary"]},
        assumptions=op.assumptions,
        created_by=p.user_id,
    )
    db.add(ch)
    s.head = seq
    _bump(s)
    audit(db, p, "scenario.change_added", "scenario", s.id, {"seq": seq, "op": op.op})
    return ch


def _uuid_or_none(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        return None


def undo(db: Session, p: Principal, s: Scenario) -> None:
    p.require(Role.editor)
    if s.head <= 0:
        raise Conflict("Nothing to undo")
    s.head -= 1
    _bump(s)


def redo(db: Session, p: Principal, s: Scenario) -> None:
    p.require(Role.editor)
    total = len(all_changes(db, s))
    if s.head >= total:
        raise Conflict("Nothing to redo")
    s.head += 1
    _bump(s)


def current_hash(db: Session, s: Scenario, pathway: str | None = None) -> str:
    return input_hash(load_snapshot(db, s.base_version_id), applied(db, s), pathway)


def run_analysis(db: Session, p: Principal, s: Scenario, pathway: str | None = None) -> tuple[AnalysisRun, bool]:
    base, proj, results, d = projection(db, s)
    h = input_hash(base, applied(db, s), pathway)
    existing = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.scenario_id == s.id, AnalysisRun.input_hash == h, AnalysisRun.status == JobStatus.succeeded)
        .order_by(AnalysisRun.started_at.desc())
    )
    if existing and existing.scenario_revision == s.revision:
        return existing, True
    if existing:
        # Identical inputs at a different revision (e.g. after undo/redo): reuse the deterministic results
        # in a new run that records the revision it evaluated.
        run = AnalysisRun(
            organization_id=s.organization_id,
            curriculum_version_id=s.base_version_id,
            scenario_id=s.id,
            scenario_revision=s.revision,
            input_hash=h,
            algorithm_version=existing.algorithm_version,
            status=JobStatus.succeeded,
            summary={**(existing.summary or {}), "reused_from_run": str(existing.id)},
            finished_at=utcnow(),
        )
        db.add(run)
        db.flush()
        _copy_findings(db, existing.id, run.id)
        return run, True
    out = analyze(base, proj, d, results, pathway)
    run = AnalysisRun(
        organization_id=s.organization_id,
        curriculum_version_id=s.base_version_id,
        scenario_id=s.id,
        scenario_revision=s.revision,
        input_hash=h,
        algorithm_version=ENGINE_VERSION,
        model_config_=None,
        status=JobStatus.succeeded,
        summary=out["summary"],
        finished_at=utcnow(),
    )
    db.add(run)
    db.flush()
    persist_findings(db, run, out["findings"], base, proj)
    audit(db, p, "scenario.analyzed", "scenario", s.id, {"run_id": str(run.id), "input_hash": h})
    return run, False


def _copy_findings(db: Session, src_run: uuid.UUID, dst_run: uuid.UUID) -> None:
    for f in db.scalars(select(Finding).where(Finding.analysis_run_id == src_run).order_by(Finding.ordinal)):
        nf = Finding(
            analysis_run_id=dst_run,
            **{
                c: getattr(f, c)
                for c in (
                    "ordinal",
                    "rule_id",
                    "category",
                    "severity",
                    "consequence_class",
                    "evidence_basis",
                    "title",
                    "explanation",
                    "affected_entity_ids",
                    "assumptions",
                    "suggested_actions",
                )
            },
        )
        db.add(nf)
        db.flush()
        for pth in db.scalars(select(FindingPath).where(FindingPath.finding_id == f.id)):
            db.add(FindingPath(finding_id=nf.id, ordinal=pth.ordinal, entity_ids=pth.entity_ids, edges=pth.edges))
        for ev in db.scalars(select(FindingEvidence).where(FindingEvidence.finding_id == f.id)):
            db.add(
                FindingEvidence(
                    finding_id=nf.id,
                    evidence_span_id=ev.evidence_span_id,
                    relationship_revision_id=ev.relationship_revision_id,
                    requirement_rule_id=ev.requirement_rule_id,
                    note=ev.note,
                )
            )


def persist_findings(db: Session, run: AnalysisRun, findings: list[dict], base: Snapshot, proj: Snapshot) -> None:
    for i, f in enumerate(findings):
        row = Finding(
            analysis_run_id=run.id,
            ordinal=i,
            rule_id=f["rule_id"],
            category=f["category"],
            severity=Severity(f["severity"]),
            consequence_class=ConsequenceClass(f["consequence_class"]),
            evidence_basis=EvidenceBasis(
                f["evidence_basis"]
                if f["evidence_basis"] in ("explicit_statement", "interpretation", "assumption")
                else "interpretation"
            ),
            title=f["title"][:500],
            explanation=f["explanation"],
            affected_entity_ids=[_uuid_or_none(x) for x in f["affected_entity_ids"] if _uuid_or_none(x)],
            assumptions=f["assumptions"],
            suggested_actions=f["suggested_actions"],
        )
        db.add(row)
        db.flush()
        for j, path in enumerate(f.get("paths", [])):
            db.add(
                FindingPath(
                    finding_id=row.id, ordinal=j, entity_ids=path, edges=[{"key": k} for k in f.get("edge_keys", [])]
                )
            )
        for k in f.get("edge_keys", []):
            rel = base.relationships.get(k) or proj.relationships.get(k)
            if rel and rel.get("revision_id"):
                db.add(
                    FindingEvidence(
                        finding_id=row.id,
                        relationship_revision_id=uuid.UUID(rel["revision_id"]),
                        note=f"{rel['type']} ({rel['basis']})",
                    )
                )
                for sid in _rel_spans(db, rel["revision_id"]):
                    db.add(FindingEvidence(finding_id=row.id, evidence_span_id=sid, note="supporting source span"))
        for rid in f.get("rule_ids", []):
            rule = base.rules.get(rid) or proj.rules.get(rid)
            if rule and rule.get("evidence_span_id"):
                db.add(
                    FindingEvidence(
                        finding_id=row.id,
                        evidence_span_id=uuid.UUID(rule["evidence_span_id"]),
                        note="requisite statement",
                    )
                )


def _rel_spans(db: Session, revision_id: str) -> list[uuid.UUID]:
    from cfs.models import RelationshipEvidence

    return list(
        db.scalars(
            select(RelationshipEvidence.evidence_span_id)
            .where(RelationshipEvidence.relationship_revision_id == uuid.UUID(revision_id))
            .limit(3)
        )
    )


def run_view(
    db: Session, run: AnalysisRun, s: Scenario | None, *, include_findings: bool = True, current: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(run.id),
        "scenario_id": str(run.scenario_id) if run.scenario_id else None,
        "curriculum_version_id": str(run.curriculum_version_id),
        "scenario_revision": run.scenario_revision,
        "input_hash": run.input_hash,
        "algorithm_version": run.algorithm_version,
        "status": run.status.value,
        "summary": run.summary,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "stale": bool(
            s and (run.scenario_revision != s.revision or (current is not None and current != run.input_hash))
        ),
    }
    if include_findings:
        base = load_snapshot(db, run.curriculum_version_id)
        proj = None
        if s is not None:
            _b, proj, _r, _d = projection(db, s)
        rows = db.scalars(select(Finding).where(Finding.analysis_run_id == run.id).order_by(Finding.ordinal)).all()
        fs = []
        for f in rows:
            paths = db.scalars(
                select(FindingPath).where(FindingPath.finding_id == f.id).order_by(FindingPath.ordinal)
            ).all()
            evs = db.scalars(select(FindingEvidence).where(FindingEvidence.finding_id == f.id)).all()

            def lab(eid: str) -> str:
                e = (proj.entities.get(eid) if proj else None) or base.entities.get(eid)
                return e["key"] if e else "(removed)"

            def ent(eid: uuid.UUID) -> dict:
                e = (proj.entities.get(str(eid)) if proj else None) or base.entities.get(str(eid))
                return {
                    "id": str(eid),
                    "key": e["key"] if e else "?",
                    "type": e["type"] if e else "?",
                    "title": e["title"] if e else "",
                }

            fs.append(
                {
                    "id": str(f.id),
                    "ordinal": f.ordinal,
                    "rule_id": f.rule_id,
                    "category": f.category,
                    "severity": f.severity.value,
                    "consequence_class": f.consequence_class.value,
                    "evidence_basis": f.evidence_basis.value,
                    "title": f.title,
                    "explanation": f.explanation,
                    "affected_entity_ids": [str(x) for x in f.affected_entity_ids],
                    "affected": [ent(x) for x in f.affected_entity_ids],
                    "assumptions": f.assumptions,
                    "suggested_actions": f.suggested_actions,
                    "paths": [
                        {
                            "ordinal": p_.ordinal,
                            "entity_ids": p_.entity_ids,
                            "labels": [lab(x) for x in p_.entity_ids],
                            "edges": p_.edges,
                        }
                        for p_ in paths
                    ],
                    "evidence": [
                        {
                            "evidence_span_id": str(e.evidence_span_id) if e.evidence_span_id else None,
                            "relationship_revision_id": str(e.relationship_revision_id)
                            if e.relationship_revision_id
                            else None,
                            "requirement_rule_id": str(e.requirement_rule_id) if e.requirement_rule_id else None,
                            "note": e.note,
                        }
                        for e in evs
                    ],
                }
            )
        out["findings"] = fs
    return out


def scenario_view(db: Session, s: Scenario) -> dict[str, Any]:
    v = db.get(CurriculumVersion, s.base_version_id)
    n = len(all_changes(db, s))
    return {
        "id": str(s.id),
        "base_version_id": str(s.base_version_id),
        "base_version_label": v.label,
        "base_version_status": v.status.value,
        "title": s.title,
        "description": s.description,
        "state": s.state.value,
        "revision": s.revision,
        "head": s.head,
        "change_count": n,
        "can_undo": s.head > 0,
        "can_redo": s.head < n,
        "workload_assumptions": s.workload_assumptions,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def change_view(c: ScenarioChange, head: int) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "seq": c.seq,
        "op_type": c.op_type,
        "target_entity_id": str(c.target_entity_id) if c.target_entity_id else None,
        "payload": c.payload,
        "before": c.before,
        "after": c.after,
        "assumptions": c.assumptions,
        "applied": c.seq <= head,
        "summary": (c.after or {}).get("summary", c.op_type),
        "created_at": c.created_at,
    }


def compare(db: Session, a: Scenario, b: Scenario | None) -> dict[str, Any]:
    base_a, proj_a, _ra, da = projection(db, a)
    if b is None:
        return {
            "left": {"kind": "baseline", "label": base_a.version["label"]},
            "right": {"kind": "scenario", "id": str(a.id), "label": a.title, "revision": a.revision},
            "diff": _named_diff(base_a, proj_a, da),
        }
    if b.base_version_id != a.base_version_id:
        raise Conflict("Scenarios have different base versions; rebase one before comparing.", code="incompatible_base")
    _bb, proj_b, _rb, _db = projection(db, b)
    d = diff(proj_a, proj_b)
    return {
        "left": {"kind": "scenario", "id": str(a.id), "label": a.title, "revision": a.revision},
        "right": {"kind": "scenario", "id": str(b.id), "label": b.title, "revision": b.revision},
        "diff": _named_diff(proj_a, proj_b, d),
    }


def _named_diff(left: Snapshot, right: Snapshot, d: dict) -> dict[str, Any]:
    def ent(eid: str) -> dict:
        e = right.entities.get(eid) or left.entities.get(eid)
        pl_l, pl_r = left.primary_placement(eid), right.primary_placement(eid)
        return {
            "id": eid,
            "key": e["key"],
            "type": e["type"],
            "title": e["title"],
            "placement_before": pl_l,
            "placement_after": pl_r,
        }

    def rel(k: str) -> dict:
        r = right.relationships.get(k) or left.relationships.get(k)
        src = right.entities.get(r["source"]) or left.entities.get(r["source"]) or {}
        tgt = right.entities.get(r["target"]) or left.entities.get(r["target"]) or {}
        return {"key": k, "type": r["type"], "source": src.get("key"), "target": tgt.get("key"), "basis": r["basis"]}

    return {
        "entities": {
            s: sorted((ent(e) for e, st in d["entities"].items() if st == s), key=lambda x: (x["type"], x["key"]))
            for s in ("added", "removed", "modified")
        },
        "relationships": {
            s: sorted(
                (rel(k) for k, st in d["relationships"].items() if st == s),
                key=lambda x: (x["type"], x["source"] or "", x["target"] or ""),
            )
            for s in ("added", "removed")
        },
    }


def rebase(db: Session, p: Principal, s: Scenario, new_base: CurriculumVersion, resolution: str) -> dict[str, Any]:
    """Replay the applied change stack onto a new base. Conflicts abort unless resolution='drop_conflicting'."""
    p.require(Role.editor)
    if new_base.program_id != db.get(CurriculumVersion, s.base_version_id).program_id:
        raise Conflict("New base must belong to the same program")
    base = load_snapshot(db, new_base.id)
    snap = base.copy()
    conflicts, keep = [], []
    for ch in all_changes(db, s):
        if ch.seq > s.head:
            continue
        try:
            op = parse_op(ch.payload)
            res = apply_op(snap, op, str(s.id), ch.seq)
            if ch.before and res["before"] and _material(ch.before) != _material(res["before"]):
                conflicts.append(
                    {
                        "seq": ch.seq,
                        "op_type": ch.op_type,
                        "summary": (ch.after or {}).get("summary"),
                        "reason": f"Baseline value changed: was {_material(ch.before)}, now {_material(res['before'])}",
                    }
                )
            keep.append(ch)
        except OpError as e:
            conflicts.append(
                {"seq": ch.seq, "op_type": ch.op_type, "summary": (ch.after or {}).get("summary"), "reason": str(e)}
            )
    if conflicts and resolution != "drop_conflicting":
        raise Conflict("Rebase has conflicts; nothing was changed.", code="rebase_conflicts", details=conflicts)
    dropped = {c["seq"] for c in conflicts if "Baseline value changed" not in c["reason"]}
    old_base = s.base_version_id
    remaining = [c for c in all_changes(db, s) if c.seq <= s.head and c.seq not in dropped]
    db.execute(delete(ScenarioChange).where(ScenarioChange.scenario_id == s.id))
    db.flush()
    for i, c in enumerate(remaining, start=1):
        db.add(
            ScenarioChange(
                scenario_id=s.id,
                seq=i,
                op_type=c.op_type,
                target_entity_id=c.target_entity_id,
                payload=c.payload,
                before=c.before,
                after=c.after,
                assumptions=c.assumptions,
                created_by=c.created_by,
            )
        )
    s.base_version_id, s.head = new_base.id, len(remaining)
    _bump(s)
    audit(
        db,
        p,
        "scenario.rebased",
        "scenario",
        s.id,
        {"from": str(old_base), "to": str(new_base.id), "conflicts": conflicts},
    )
    return {"conflicts": conflicts, "dropped": sorted(dropped)}


def _material(v: dict | None) -> Any:
    if not v:
        return v
    return {
        k: v[k]
        for k in sorted(v)
        if k
        in ("year", "term", "timing_week", "weight_percent", "statement", "classification", "text", "delivery_format")
    }


def publish(db: Session, p: Principal, s: Scenario, label: str) -> CurriculumVersion:
    """Materialize the scenario projection as a new, published curriculum version. Base stays intact."""
    from cfs.scenarios.publish import materialize

    p.require(Role.editor)
    if s.state == ScenarioState.published:
        raise Conflict("Scenario already published")
    base_v = db.get(CurriculumVersion, s.base_version_id)
    base, proj, results, _d = projection(db, s)
    bad = [r for r in results if not r["ok"]]
    if bad:
        raise Conflict("Some changes no longer apply; rebase or fix them before publishing", details=bad)
    new_v = materialize(db, p, base_v, base, proj, label, s)
    new_v.status = VersionStatus.published
    new_v.published_at, new_v.published_by, new_v.published_from_scenario_id = utcnow(), p.user_id, s.id
    s.state = ScenarioState.published
    _bump(s)
    audit(
        db,
        p,
        "version.published",
        "curriculum_version",
        new_v.id,
        {"scenario_id": str(s.id), "base_version_id": str(base_v.id)},
    )
    return new_v
