# Implementation report — pass 1 (Phases 1–4)

Date: 2026-09-26. All curriculum content in this build is a **synthetic fixture**, not University of Calgary data.
The real BHSc sources (2019 review, 2025–26 outline, webpage PDF) were not available. Their extraction is untested.

## Implemented and tested
| Area | What exists | Tests |
|---|---|---|
| Environment | Docker Compose (pgvector PG16, Redis, API, Celery worker, Vite); `make up`, `seed-synthetic`, `reset`, `test`, `gen-client`; `.env.example`; locked deps (`uv.lock`, `pnpm-lock.yaml`); `/health`, `/ready` | `test_system.py`; stack verified manually (all containers healthy, worker processes jobs over Redis) |
| Schema | Full §6 schema in one Alembic migration; immutability triggers for revisions and published versions; FTS generated columns; HNSW vector index (Phase 5) | `test_graph.py::test_published_version_is_immutable_in_database` |
| Ingestion | Upload (file or paste), content sniffing, size limit, sha256 dedupe per org, content-addressed storage, durable idempotent stages, OCR fallback (Tesseract), low-quality detection, section chunks, deterministic candidate extraction (courses, credits, year/term, requisites, outcomes, assessments, "X requires Y"), quote validation → evidence spans with PDF line boxes, identity resolution, review items, retry, cancel, crash reclaim, SSE progress | `test_ingest.py` (dedupe, page citations, fabricated quotes, OCR/unreadable, crash + resume, duplicate delivery, conflicts, description ≠ outcomes, MIME, path traversal, DOCX/paste, prompt injection inert) |
| Versions and sources | Explicit source assignment with authority, applicability, exploratory flag; academic-year mismatch guard; clone version | `test_ingest.py::test_conflicting_sources_are_both_kept` |
| Review | Inbox for candidates, conflicts (both kept; choose interpretation), inferred mappings, duplicates, uncertain codes, missing year, unreadable pages; accept/edit/reject/defer; audit | `test_graph.py::test_accepting_inferred_keeps_interpretation_basis`, `test_review_cannot_modify_published_version` |
| Map | React Flow + ELK in a worker; year/term lanes; course cards with evidence completeness, classification, synthetic and scenario badges; layers; progressive disclosure (expand, collapse onto owner course with labelled derived edges); legend; minimap; fit/reset; saved positions (visual only); highlight modes; filters | `filters.test.ts` (vitest); Playwright `demo.spec.ts` |
| Evidence | Field-level evidence chips; PDF.js viewer at the right page with highlight boxes; text fallback for non-PDF | Playwright "inspect a course and open its evidence" |
| Prior/downstream | Typed traversals; formal vs inferred vs topic preparation; exposure classes (required, pathway, elective, unknown) under a visible pathway assumption; "expected to have encountered" language; alternative preparation; documented-path preference | `test_graph.py` (pathway, elective, overlap, documented paths) |
| Outcomes | Course × PLO matrix with defined denominator and documentation completeness; assessment alignment; issue detection (no documented assessment, single opportunity, elective-only, assessment before preparation, elective-dependent requisite, repeated introductory material flagged for review, cycles, undocumented outcomes) | `test_graph.py::test_outcome_matrix_defines_denominator_and_unknowns` |
| Scenarios | 17 typed operations; preview-then-apply UI; undo/redo; `If-Match` revisions (409/428); projection graph with added/removed overlay; compare vs baseline and vs another scenario (same base); rebase with conflict report or drop; publish to a new immutable version (editor role); HTML/Markdown export with sources, assumptions, changes, findings, and quotes | `test_scenarios.py` (15 tests) |
| Engine | Deterministic analysis: rule satisfaction (AND/OR/credits/consent/concurrency), alternative paths, typed propagation, PLO coverage/assessment deltas, sequencing violations, cycles, assessment timing, missing-evidence findings, workload known/estimated/unquantified, congestion (only when weeks exist); input-hash cache; stale detection | `test_rules.py`, `test_scenarios.py` (determinism, alternatives, overlap, workload, cycles) |
| Access | Roles admin/editor/reviewer/viewer enforced server-side; org isolation; local single-user mode refused when `CFS_ENV=production` | `test_access.py` |
| Storage | Local filesystem and S3 implementations of one interface | `test_storage.py` (S3 via moto) |

Totals: **56 backend tests** (pytest), **4 frontend unit tests** (vitest), and **6 end-to-end tests** (Playwright),
all passing. Backend lint (ruff) is clean. The frontend type check and production build pass.

## Required demonstration (spec §26)
| # | Step | Status |
|---|---|---|
| 1 | Upload or select sources | ✅ Upload works. Real BHSc sources not available; synthetic outline and review report used |
| 2 | Assign to historical/current contexts | ✅ |
| 3 | Extract course structure and candidate mappings | ✅ Deterministic extractor verified on synthetic PDFs only |
| 4 | Review an inferred relationship | ✅ |
| 5 | Four-year course map | ✅ |
| 6 | Select MDSC 407 | ⚠️ Not present in synthetic data. **SYN 407** is the labelled stand-in |
| 7 | Documented description and unknowns | ✅ |
| 8 | Preparation relationships with evidence labels | ✅ |
| 9 | Scenario changing a topic or outcome | ✅ |
| 10 | Deterministic analysis with dependency paths | ✅ |
| 11 | Missing evidence shown | ✅ |
| 12 | Grounded assistant explanation | ❌ Phase 5 (not implemented; AI endpoints return `provider_unconfigured`) |
| 13 | Save and reopen scenario | ✅ |
| 14 | Export a readable report | ✅ HTML and Markdown (PDF not implemented) |

## Implemented but not verified against real conditions
- The **extractor's coverage of real UCalgary documents** is unknown. Its patterns follow common outline conventions,
  and real layouts (tables, multi-column pages, webpage PDFs) may need new patterns or the Phase 5 model route.
- **OCR** works with the local Tesseract. The scanned-page test accepts either OCR text or an "unreadable page"
  review item.
- **S3** is tested only with moto. No AWS resources were created.
- **Celery on SQS** is not exercised. The design relies on PostgreSQL job state plus idempotent handlers.

## Not implemented (deferred by agreement)
- **Phase 5**: ModelGateway adapters (OpenAI Responses, Anthropic Messages), embeddings and hybrid retrieval,
  assistant panel and tools, structured proposals, cost and rate controls. The spec's model IDs (`gpt-6-luna`,
  `gpt-6-sol`, `gpt-6-astra`, `claude-sonnet-5`, `claude-opus-5-5`, `text-embedding-3-small`) have **not** been
  checked against live catalogs or account access.
- **Phase 6**: OIDC/Cognito, AWS infrastructure templates, backup automation, full accessibility audit.
- Controlled webpage import (URL fetcher), PDF export, entity field edits through review (field conflicts on
  credits and similar fields are recorded but can't be materialized), and a scenario projection cache.

## Known limitations
- Topics in the synthetic fixture are attached to courses without source spans. The UI marks them with an asterisk
  ("no source span") rather than hiding the gap.
- Workload analysis only has user assumptions to work with, because no documented hours exist in the fixture.
- Lane layout uses ELK partitions. Very dense graphs can route long edges across lanes; use layers, focus, or the
  table view.
