"""Scenario API: creation, typed changes with revision checks, undo/redo, projection views, analysis,
comparison, rebase, publish, and export."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.api.v1.catalog import MEANINGS, _course_unknowns
from cfs.core.auth import Principal, get_principal
from cfs.core.db import get_db
from cfs.core.errors import NotFound
from cfs.core.scope import get_scoped
from cfs.graph import queries
from cfs.graph.view import build_view
from cfs.models import AnalysisRun, CurriculumVersion, EntityFieldEvidence, Scenario
from cfs.scenarios import service
from cfs.scenarios.report import build_markdown, to_html

router = APIRouter(tags=["scenarios"])


class ScenarioCreate(BaseModel):
    base_version_id: uuid.UUID
    title: str
    description: str | None = None


class ScenarioPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    state: Literal["draft", "under_review", "approved", "archived"] | None = None


class RebaseIn(BaseModel):
    new_base_version_id: uuid.UUID
    resolution: Literal["abort", "drop_conflicting"] = "abort"


class PublishIn(BaseModel):
    label: str


def _get(db: Session, p: Principal, sid: uuid.UUID) -> Scenario:
    return get_scoped(db, Scenario, sid, p, "Scenario")


def _rev(if_match: str | None) -> int | None:
    if if_match is None:
        return None
    try:
        return int(if_match.strip('"W/ '))
    except ValueError:
        return -1


@router.get("/scenarios")
def list_scenarios(
    base_version_id: uuid.UUID | None = None, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
) -> list[dict[str, Any]]:
    q = select(Scenario).where(Scenario.organization_id == p.org_id).order_by(Scenario.updated_at.desc())
    if base_version_id:
        q = q.where(Scenario.base_version_id == base_version_id)
    return [service.scenario_view(db, s) for s in db.scalars(q)]


@router.post("/scenarios", status_code=201)
def create_scenario(body: ScenarioCreate, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    s = service.create(db, p, body.base_version_id, body.title, body.description)
    db.commit()
    return service.scenario_view(db, s)


@router.get("/scenarios/{sid}")
def get_scenario(sid: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    return service.scenario_view(db, _get(db, p, sid))


@router.patch("/scenarios/{sid}")
def patch_scenario(
    sid: uuid.UUID,
    body: ScenarioPatch,
    if_match: str | None = Header(default=None),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    s = _get(db, p, sid)
    service.check_revision(s, _rev(if_match))
    from cfs.models.enums import ScenarioState

    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, ScenarioState(v) if k == "state" else v)
    service._bump(s)
    db.commit()
    return service.scenario_view(db, s)


@router.get("/scenarios/{sid}/changes")
def list_changes(sid: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    s = _get(db, p, sid)
    return [service.change_view(c, s.head) for c in service.all_changes(db, s)]


@router.post("/scenarios/{sid}/changes", status_code=201)
def add_change(
    sid: uuid.UUID,
    change: dict[str, Any],
    if_match: str | None = Header(default=None),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    s = _get(db, p, sid)
    service.check_revision(s, _rev(if_match))
    service.add_change(db, p, s, change)
    db.commit()
    return service.scenario_view(db, s)


@router.post("/scenarios/{sid}/undo")
def undo(
    sid: uuid.UUID,
    if_match: str | None = Header(default=None),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    s = _get(db, p, sid)
    service.check_revision(s, _rev(if_match))
    service.undo(db, p, s)
    db.commit()
    return service.scenario_view(db, s)


@router.post("/scenarios/{sid}/redo")
def redo(
    sid: uuid.UUID,
    if_match: str | None = Header(default=None),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    s = _get(db, p, sid)
    service.check_revision(s, _rev(if_match))
    service.redo(db, p, s)
    db.commit()
    return service.scenario_view(db, s)


@router.get("/scenarios/{sid}/graph")
def scenario_graph(
    sid: uuid.UUID,
    layers: list[str] = Query(default=["prerequisites", "preparation"]),
    expand: list[str] = Query(default=[]),
    focus: str | None = None,
    compare: bool = True,
    node_budget: int = Query(default=300, ge=10, le=2000),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
) -> dict[str, Any]:
    s = _get(db, p, sid)
    base, proj, results, d = service.projection(db, s)
    view = build_view(
        proj,
        layers=layers,
        expand=expand,
        focus=focus,
        node_budget=node_budget,
        diff=d if compare else None,
        removed_snapshot=base if compare else None,
    )
    view["scenario"] = {"id": str(s.id), "title": s.title, "revision": s.revision}
    view["scenario_revision"] = s.revision
    view["change_errors"] = [r for r in results if not r["ok"]]
    return view


def _in(snap, eid: uuid.UUID) -> str:
    if str(eid) not in snap.entities:
        raise NotFound("Entity is not part of this scenario projection")
    return str(eid)


@router.get("/scenarios/{sid}/entities/{eid}")
def scenario_entity(
    sid: uuid.UUID, eid: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
) -> dict[str, Any]:
    s = _get(db, p, sid)
    base, proj, _r, d = service.projection(db, s)
    k = _in(proj, eid)
    e = proj.entities[k]
    fe: dict[str, list[dict]] = {}
    if e.get("revision_id"):
        for f, stance, span in db.execute(
            select(
                EntityFieldEvidence.field_name, EntityFieldEvidence.stance, EntityFieldEvidence.evidence_span_id
            ).where(EntityFieldEvidence.entity_revision_id == uuid.UUID(e["revision_id"]))
        ):
            fe.setdefault(f, []).append({"span_id": str(span), "stance": stance.value})
    changed_fields = []
    b = base.entities.get(k)
    if b and base.placements.get(k) != proj.placements.get(k):
        changed_fields += ["year", "term", "classification"]
    for f in changed_fields:
        fe.pop(f, None)  # baseline evidence no longer supports the scenario value
    res: dict[str, Any] = {
        "entity": e,
        "placements": proj.placements.get(k, []),
        "field_evidence": fe,
        "revisions": [],
        "relationships": [
            {**r, "other": proj.entities.get(r["target"] if r["source"] == k else r["source"], {}).get("key")}
            for r in proj.relationships.values()
            if k in (r["source"], r["target"])
        ],
        "curriculum_version": proj.version,
        "scenario_status": d["entities"].get(k, "unchanged"),
        "changed_fields": changed_fields,
    }
    if e["type"] == "course":
        res["contribution"] = queries.contribution(proj, k)
        res["requirement_rules"] = queries.evaluate_rules(proj, k)
        res["unknowns"] = _course_unknowns(proj, k, res)
    return res


@router.get("/scenarios/{sid}/entities/{eid}/prior-learning")
def scenario_prior(
    sid: uuid.UUID,
    eid: uuid.UUID,
    pathway: str | None = None,
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    s = _get(db, p, sid)
    _b, proj, _r, _d = service.projection(db, s)
    return queries.prior_learning(proj, _in(proj, eid), pathway=pathway)


@router.get("/scenarios/{sid}/entities/{eid}/downstream")
def scenario_downstream(
    sid: uuid.UUID, eid: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    s = _get(db, p, sid)
    _b, proj, _r, _d = service.projection(db, s)
    return queries.downstream(proj, _in(proj, eid))


@router.get("/scenarios/{sid}/relationships/{key}")
def scenario_relationship(
    sid: uuid.UUID, key: str, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    s = _get(db, p, sid)
    base, proj, _r, d = service.projection(db, s)
    r = proj.relationships.get(key) or base.relationships.get(key)
    if r is None:
        raise NotFound("Relationship not found")
    return {
        "relationship": r,
        "meaning": MEANINGS.get(r["type"], r["type"]),
        "status": d["relationships"].get(key, "unchanged"),
        "source": proj.entities.get(r["source"]) or base.entities.get(r["source"]),
        "target": proj.entities.get(r["target"]) or base.entities.get(r["target"]),
    }


@router.get("/scenarios/{sid}/outcome-matrix", tags=["analysis"])
def scenario_matrix(
    sid: uuid.UUID, pathway: str | None = None, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    from cfs.analysis.coverage import outcome_matrix

    s = _get(db, p, sid)
    _b, proj, _r, _d = service.projection(db, s)
    return outcome_matrix(proj, pathway)


@router.post("/scenarios/{sid}/analyses", status_code=201)
def analyze(
    sid: uuid.UUID, pathway: str | None = None, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    """Deterministic analysis. Runs synchronously (milliseconds at target scale) and is cached by input hash."""
    s = _get(db, p, sid)
    run, cached = service.run_analysis(db, p, s, pathway)
    db.commit()
    out = service.run_view(db, run, s)
    out["cached"] = cached
    return out


@router.get("/scenarios/{sid}/analyses")
def list_analyses(sid: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    s = _get(db, p, sid)
    cur = service.current_hash(db, s)
    runs = db.scalars(
        select(AnalysisRun).where(AnalysisRun.scenario_id == s.id).order_by(AnalysisRun.started_at.desc()).limit(50)
    )
    return [service.run_view(db, r, s, include_findings=False, current=cur) for r in runs]


@router.get("/analyses/{run_id}")
def get_analysis(run_id: uuid.UUID, db: Session = Depends(get_db), p: Principal = Depends(get_principal)):
    run = get_scoped(db, AnalysisRun, run_id, p, "Analysis run")
    s = db.get(Scenario, run.scenario_id) if run.scenario_id else None
    cur = service.current_hash(db, s) if s else None
    return service.run_view(db, run, s, current=cur)


@router.get("/scenarios/{sid}/compare")
def compare(
    sid: uuid.UUID, other: uuid.UUID | None = None, db: Session = Depends(get_db), p: Principal = Depends(get_principal)
):
    a = _get(db, p, sid)
    b = _get(db, p, other) if other else None
    out = service.compare(db, a, b)

    def latest(s: Scenario | None):
        if s is None:
            return None
        run = db.scalar(
            select(AnalysisRun).where(AnalysisRun.scenario_id == s.id).order_by(AnalysisRun.started_at.desc())
        )
        return service.run_view(db, run, s, current=service.current_hash(db, s)) if run else None

    out["left_analysis"] = latest(b) if b else None
    out["right_analysis"] = latest(a) if not b else latest(a)
    return out


@router.post("/scenarios/{sid}/rebase")
def rebase(
    sid: uuid.UUID,
    body: RebaseIn,
    if_match: str | None = Header(default=None),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    s = _get(db, p, sid)
    service.check_revision(s, _rev(if_match))
    nb = get_scoped(db, CurriculumVersion, body.new_base_version_id, p, "Curriculum version")
    res = service.rebase(db, p, s, nb, body.resolution)
    db.commit()
    return {"scenario": service.scenario_view(db, s), **res}


@router.post("/scenarios/{sid}/publish", status_code=201)
def publish(
    sid: uuid.UUID,
    body: PublishIn,
    if_match: str | None = Header(default=None),
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    """Explicit, permissioned action. Creates a new published version; the base snapshot remains available."""
    s = _get(db, p, sid)
    service.check_revision(s, _rev(if_match))
    v = service.publish(db, p, s, body.label)
    db.commit()
    return {"version_id": str(v.id), "label": v.label, "scenario": service.scenario_view(db, s)}


@router.get("/scenarios/{sid}/export")
def export(
    sid: uuid.UUID,
    format: Literal["md", "html"] = "html",
    db: Session = Depends(get_db),
    p: Principal = Depends(get_principal),
):
    s = _get(db, p, sid)
    run = db.scalar(select(AnalysisRun).where(AnalysisRun.scenario_id == s.id).order_by(AnalysisRun.started_at.desc()))
    base, _proj, _r, _d = service.projection(db, s)
    changes = [service.change_view(c, s.head) for c in service.all_changes(db, s)]
    rv = service.run_view(db, run, s, current=service.current_hash(db, s)) if run else None
    md = build_markdown(db, s, service.scenario_view(db, s), changes, rv, base.version)
    fname = "".join(ch if ch.isalnum() else "_" for ch in s.title)[:60] or "scenario"
    if format == "md":
        return PlainTextResponse(
            md, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{fname}.md"'}
        )
    return HTMLResponse(to_html(md, s.title), headers={"Content-Disposition": f'inline; filename="{fname}.html"'})
