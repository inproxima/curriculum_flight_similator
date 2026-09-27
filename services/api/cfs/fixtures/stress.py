"""SYNTHETIC stress program for performance measurement (not real data; no documents).

    python -m cfs.fixtures.stress            # create program SYN-STRESS (idempotent)
    python -m cfs.fixtures.stress --bench    # run backend timings and print a Markdown table

Shape: 150 courses over 4 years, 3 outcomes and 2 assessments per course, 200 topics, 12 program
outcomes, AND/OR prerequisite rules, inferred preparation and topic-preparation edges.
Generated deterministically (fixed RNG seed).
"""

from __future__ import annotations

import random
import statistics
import sys
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from cfs.core.auth import ensure_local_principal
from cfs.core.db import get_sessionmaker
from cfs.curriculum.rules import dump_expr, leaf_edges, parse_requisite_text
from cfs.curriculum.service import (
    create_relationship,
    create_revision,
    get_or_create_entity,
    place_course,
    set_membership,
)
from cfs.models import CurriculumVersion, Program, RequirementRule
from cfs.models.enums import (
    ContributionLevel,
    EntityType,
    EvidenceBasis,
    Origin,
    PlacementClass,
    RelType,
    ReviewState,
    RuleKind,
    Term,
    VersionStatus,
)

CODE = "SYN-STRESS"
N_COURSES, N_TOPICS, N_PLOS = 150, 200, 12


def build(db: Session) -> uuid.UUID:
    p = ensure_local_principal(db)
    existing = db.scalar(select(Program).where(Program.organization_id == p.org_id, Program.code == CODE))
    if existing:
        return db.scalar(select(CurriculumVersion.id).where(CurriculumVersion.program_id == existing.id))
    rng = random.Random(20260926)
    prog = Program(
        organization_id=p.org_id,
        code=CODE,
        name="Synthetic Stress Program (performance fixture)",
        institution="Synthetic (not a real institution)",
        is_synthetic=True,
    )
    db.add(prog)
    db.flush()
    v = CurriculumVersion(organization_id=p.org_id, program_id=prog.id, label="Stress (synthetic)", is_synthetic=True)
    db.add(v)
    db.flush()
    F, X = Origin.synthetic_fixture, EvidenceBasis.explicit_statement

    def ent(t, key, title, details=None):
        e = get_or_create_entity(db, p.org_id, t, key)
        r = create_revision(db, e, title=title, origin=F, is_synthetic=True, details=details)
        set_membership(db, v.id, e, r)
        return e, r

    plos = [ent(EntityType.program_outcome, f"XPLO{i}", f"XPLO{i}: stress outcome {i}")[0] for i in range(N_PLOS)]
    topics = [
        ent(EntityType.topic, f"xtopic-{i}", f"Stress topic {i}", {"level": "intermediate"})[0] for i in range(N_TOPICS)
    ]
    for i in range(1, N_TOPICS):
        if rng.random() < 0.5:
            create_relationship(
                db,
                p.org_id,
                v.id,
                topics[rng.randrange(i)].id,
                topics[i].id,
                RelType.prepares_for,
                origin=F,
                basis=X,
                review=ReviewState.accepted,
                is_synthetic=True,
            )
    courses = []
    for i in range(N_COURSES):
        year = 1 + i * 4 // N_COURSES
        code = f"XS{year}{i:03d}"
        e, r = ent(EntityType.course, code, f"Stress course {i}", {"credits": 3})
        term = Term.fall if i % 2 == 0 else Term.winter
        cls = PlacementClass.elective if rng.random() < 0.25 else PlacementClass.required
        place_course(db, v.id, e, r, year=year, term=term, classification=cls)
        courses.append((e, year, term))
        for j in range(3):
            o, _ = ent(EntityType.course_outcome, f"{code}-CO{j}", f"{code} outcome {j}")
            create_relationship(
                db,
                p.org_id,
                v.id,
                e.id,
                o.id,
                RelType.has_outcome,
                origin=F,
                basis=X,
                review=ReviewState.accepted,
                is_synthetic=True,
            )
            create_relationship(
                db,
                p.org_id,
                v.id,
                o.id,
                plos[rng.randrange(N_PLOS)].id,
                RelType.contributes_to,
                origin=F,
                basis=X,
                review=ReviewState.accepted,
                level=rng.choice(list(ContributionLevel)),
                is_synthetic=True,
            )
            if rng.random() < 0.4:
                create_relationship(
                    db,
                    p.org_id,
                    v.id,
                    topics[rng.randrange(N_TOPICS)].id,
                    o.id,
                    RelType.prepares_for,
                    origin=F,
                    basis=X,
                    review=ReviewState.accepted,
                    is_synthetic=True,
                )
            if j < 2:
                a, _ = ent(
                    EntityType.assessment,
                    f"{code}-A{j}",
                    f"{code} assessment {j}",
                    {"weight_percent": 30, "timing_week": rng.randint(3, 13), "format": "exam"},
                )
                create_relationship(
                    db,
                    p.org_id,
                    v.id,
                    e.id,
                    a.id,
                    RelType.has_assessment,
                    origin=F,
                    basis=X,
                    review=ReviewState.accepted,
                    is_synthetic=True,
                )
                create_relationship(
                    db,
                    p.org_id,
                    v.id,
                    a.id,
                    o.id,
                    RelType.assesses,
                    origin=F,
                    basis=X,
                    review=ReviewState.accepted,
                    is_synthetic=True,
                )
        for t in rng.sample(topics, 2):
            create_relationship(
                db,
                p.org_id,
                v.id,
                e.id,
                t.id,
                RelType.covers_topic,
                origin=F,
                basis=X,
                review=ReviewState.accepted,
                is_synthetic=True,
            )
    for idx, (e, year, _term) in enumerate(courses):
        earlier = [c for c in courses[:idx] if c[1] < year]
        if not earlier or rng.random() < 0.2:
            continue
        picks = rng.sample(earlier, min(len(earlier), 3))
        text = (
            f"{picks[0][0].stable_key} and ({picks[1][0].stable_key} or {picks[2][0].stable_key})"
            if len(picks) == 3
            else picks[0][0].stable_key
        )
        expr = parse_requisite_text(text)
        rule = RequirementRule(
            organization_id=p.org_id,
            curriculum_version_id=v.id,
            target_entity_id=e.id,
            rule_kind=RuleKind.prerequisite,
            expression=dump_expr(expr),
            source_text=text,
            origin=F,
            evidence_basis=X,
            review_state=ReviewState.accepted,
        )
        db.add(rule)
        db.flush()
        by_key = {c[0].stable_key: c[0] for c in courses}
        for ref, _p, _alt in leaf_edges(expr):
            create_relationship(
                db,
                p.org_id,
                v.id,
                by_key[ref.code].id,
                e.id,
                RelType.formal_prerequisite,
                origin=F,
                basis=X,
                review=ReviewState.accepted,
                rule_id=rule.id,
                is_synthetic=True,
            )
        if rng.random() < 0.3:
            src = rng.choice(earlier)[0]
            create_relationship(
                db,
                p.org_id,
                v.id,
                src.id,
                e.id,
                RelType.inferred_preparation,
                origin=F,
                basis=EvidenceBasis.interpretation,
                review=ReviewState.proposed,
                is_synthetic=True,
            )
    v.status = VersionStatus.published
    db.commit()
    return v.id


def _t(fn, n=5):
    xs = []
    out = None
    for _ in range(n):
        t0 = time.perf_counter()
        out = fn()
        xs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(xs), max(xs), out


def bench(db: Session, vid: uuid.UUID) -> str:
    from cfs.analysis.coverage import detect_issues, outcome_matrix
    from cfs.graph import queries
    from cfs.graph.snapshot import load_snapshot
    from cfs.graph.view import build_view
    from cfs.scenarios.engine import analyze
    from cfs.scenarios.ops import diff, project

    rows = []
    med, mx, snap = _t(lambda: load_snapshot(db, vid))
    rows.append(("Load snapshot from PostgreSQL", med, mx))
    n_rel = len(snap.relationships)
    med, mx, view = _t(lambda: build_view(snap, layers=["prerequisites", "preparation"]))
    rows.append((f"Graph view, overview ({view['stats']['nodes']} nodes, {view['stats']['edges']} edges)", med, mx))
    med, mx, view2 = _t(
        lambda: build_view(snap, layers=["prerequisites", "preparation", "plo_alignment", "topics", "assessment"])
    )
    rows.append((f"Graph view, all layers ({view2['stats']['nodes']} nodes, {view2['stats']['edges']} edges)", med, mx))
    late = sorted((c for c in snap.courses()), key=lambda c: c["key"])[-1]["id"]
    early = sorted((c for c in snap.courses()), key=lambda c: c["key"])[0]["id"]
    rows.append(("Prior learning, final-year course", *_t(lambda: queries.prior_learning(snap, late))[:2]))
    rows.append(("Downstream, first-year course", *_t(lambda: queries.downstream(snap, early))[:2]))
    rows.append(("Outcome matrix", *_t(lambda: outcome_matrix(snap))[:2]))
    rows.append(("Issue detection", *_t(lambda: detect_issues(snap), n=3)[:2]))
    sid = str(uuid.uuid4())
    targets = [c["id"] for c in sorted(snap.courses(), key=lambda c: c["key"])[10:13]]
    changes = [(i + 1, {"op": "remove_course", "entity_id": t}) for i, t in enumerate(targets)]

    def scen():
        proj, res = project(snap, changes, sid)
        return analyze(snap, proj, diff(snap, proj), res)

    med, mx, out = _t(scen, n=3)
    rows.append((f"Scenario analysis, remove 3 courses ({len(out['findings'])} findings)", med, mx))
    ents = len(snap.entities)
    lines = [
        f"Entities: {ents} · relationships: {n_rel} · courses: {len(snap.courses())}",
        "",
        "| Operation | median ms | max ms |",
        "|---|---:|---:|",
    ]
    lines += [f"| {name} | {m:.1f} | {x:.1f} |" for name, m, x in rows]
    return "\n".join(lines)


if __name__ == "__main__":
    s = get_sessionmaker()()
    try:
        vid = build(s)
        print(f"stress version {vid}")
        if "--bench" in sys.argv:
            print(bench(s, vid))
    finally:
        s.close()
