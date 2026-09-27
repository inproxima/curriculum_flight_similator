# ADR 0001 — Architecture of the Curriculum Flight Simulator

Status: accepted (2026-09-26) · Scope: Phases 1–4 (deterministic core)

## Context
The app has to turn program documents into an evidence-traced curriculum graph, support review of extracted and
inferred relationships, and run reproducible "what if" analyses. It has to run locally first, then on AWS without
re-architecture. AI assistance comes later (Phase 5) and must never be the source of curriculum facts.

## Decisions
1. **Modular monolith plus one worker process.** A single Python package `cfs` (FastAPI API, domain services,
   analysis) is imported by both the API and the Celery worker. No microservices, graph database, separate vector
   DB, or Kubernetes.
2. **PostgreSQL is the system of record** for curriculum data, evidence, jobs, and analysis results. The broker
   (Redis locally, SQS in AWS) only delivers "run job X". Job status, stage progress, cancellation, and
   idempotency records live in PostgreSQL, so SQS's lack of Redis-specific Celery features doesn't matter.
3. **The graph is a projection.** `cfs.graph.snapshot` loads an immutable, canonical in-memory snapshot of one
   curriculum version. Views, traversals, matrices, and the scenario engine all operate on snapshots. Coordinates
   live only in `saved_views` and never carry curriculum meaning.
4. **Immutable revisions and snapshots.** `entity_revisions` and `relationship_revisions` are append-only
   (database trigger). A curriculum version references exact revisions through membership rows. Published versions
   are immutable (trigger on memberships, placements, rules, and groups).
5. **Scenarios are typed change stacks over a published base.** They're replayed onto a copy of the base
   snapshot. The baseline is never mutated. Undo/redo moves a head pointer. Every edit bumps a revision checked
   with `If-Match` (optimistic concurrency).
6. **Deterministic analysis in Python (NetworkX plus explicit rule ASTs).** Requisites are stored as AND/OR/credits
   /exclusion/consent trees and evaluated three-valued plus "conditional". Results are cached by a SHA-256 input
   hash of (base snapshot, applied changes, pathway, engine version). `ENGINE_VERSION` must be bumped on any rule
   change.
7. **Deterministic extraction before models.** Ingestion stages are idempotent and keyed on (document version,
   stage, parser version). Candidates must quote text that is verified against stored page text before an evidence
   span is created. Nothing is written into a curriculum without an explicit review decision.
8. **Explicit source-to-version assignment.** `curriculum_sources` records authority and applicability. Documents
   are never merged into a version implicitly. Conflicts become review items with both sides kept.
9. **Object storage behind an interface.** `LocalFSStore` for development and `S3Store` for deployment. Keys are
   content-addressed (`orgs/<org>/originals/<sha[:2]>/<sha>.<ext>`), with no user-supplied path components.
10. **Frontend:** React + TypeScript + Vite; React Flow for the map; ELK (elk-api in its own web worker) with
    partition constraints for year/term lanes; TanStack Query; Zustand; PDF.js via react-pdf; Recharts.
    The TypeScript client types are generated from the API's OpenAPI document (`packages/contracts`).
11. **AI is behind a `ModelGateway` interface (Phase 5).** Until it's implemented, AI endpoints return
    `503 provider_unconfigured` and never fabricate output.

## Consequences
- Analysis is fast at the target scale (measured in `docs/performance.md`), so it runs synchronously. Ingestion
  runs in the worker.
- Scenario projections are recomputed per request (about 100 ms at 150 courses). A projection cache keyed by
  (scenario id, revision) is an easy later optimization.
- A course's identity (`entities`) is independent of its version-specific details (`entity_revisions`) and of
  its placement in a program (`course_placements`).
