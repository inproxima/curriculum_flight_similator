# Implementation report: Phases 1–5

Pass 1 (Phases 1–4) and pass 2 (Phase 5, AI) were both completed on 2026-09-26. The workspace contains the labelled
**synthetic fixture** and a **real** program, "Biomedical Sciences (BHSc)", imported from the three University of
Calgary sources in `outputs/`.

## Phase 5: AI assistance (pass 2)
| Area | What exists | Verified by |
|---|---|---|
| Gateway | Route config with published prices; provider allowlist; per-document AI policy; fallback only under policy; per-job and monthly spend limits; concurrency caps; cache keyed by source hash, prompt version, and pipeline version; `model_runs` and `tool_calls` audit | `test_ai.py` (fallback, policy, spend limit, refusal, caching, cost), live runs |
| Adapters | OpenAI Responses (`store=False`, strict tools and JSON schema); Anthropic Messages (streamed, `output_config` format and effort, strict tools, no forced tool choice, no native citations mixed with JSON) | request-shape tests; live calls to all six models |
| Extraction | `gpt-6-sol` proposes courses, years, terms, credits, descriptions, option rules, outcomes, requisites, and stated preparation. The verifier drops unsupported items. Results merge with deterministic candidates across documents (higher authority wins; conflicts kept) and become review items | `test_ai.py`; [evaluation](evaluation.md) on the real documents |
| Layout | PDF text keeps columns (parser 1.1), so the outline grid and its rule sidebar stay separable | real outline |
| Mapping proposals | `gpt-6-astra` proposes course → program-outcome alignments from description quotes. They are review items only and always labelled inferred | live: 19 proposals, all quotes verified |
| Retrieval | Hybrid FTS + pgvector (RRF), scoped to the version's assigned sources and filtered by provider policy; chunks embedded at ingest | eval: 12/12 hit@3 |
| Assistant | Explore/Investigate (`gpt-6-sol`) and Simulate (`claude-sonnet-5`), with 12 typed, scoped tools (read-only; changes validated but never applied). Strict envelope, with server-side verification of citations, IDs, and proposals. Durable jobs with SSE progress. Critique (`claude-opus-5-5`) and analysis explanation (`gpt-6-astra`) | `test_ai.py` (fabricated IDs, citation matching, uncited warning, injection, routing); mocked Playwright UI test; live spot checks in `evaluation.md` |
| UI | Assistant panel (modes, suggestions, citations, highlight, proposal cards, critique); Documents (AI extraction, AI policy, program and version creation, alignment proposals); review inbox (AI badges, conflicts, editable sources, bulk decisions); AI & usage page with per-run audit; "Explain with AI" on analyses | Playwright (7 tests) and manual browser checks |

**Real-document results.** Deterministic extraction alone got nothing structured from the outline and no
outcomes from the review. With verified model extraction, the 2025–26 and 2026–27 versions each have 17 courses
(accepted in bulk during testing), 8 program outcomes (headings only), and 8–9 option rules. There are no formal
prerequisite edges, because no source states one. One AI-inferred preparation link (BIOL 211 → MDSC 351) was
accepted after a reviewer edit; the model had also proposed BIOL 213. Left open for faculty: the BCEM 393
required/elective conflict, 19 outcome-alignment proposals, the MDSC 308 preparation proposal, MDSC 402, and the
"BCEM 393 or 341" option item.

**Spend during development and testing:** $0.52 (extraction $0.21, mapping $0.09, Simulate $0.16, critique $0.06,
embeddings under $0.01).

**Not verified live:** the `classify` route (`gpt-6-luna`; metadata suggestions currently come from the extraction
run), vision fallback for image-only layouts (not implemented; Tesseract OCR is used), and provider outages. Fallback
is covered by tests, not by a real outage.

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

Totals: **70 backend tests** (pytest, including 16 AI tests with fake providers), **4 frontend unit tests** (vitest), and **7 end-to-end tests** (Playwright, one with a mocked assistant),
all passing. Backend lint (ruff) is clean. The frontend type check and production build pass.

## Required demonstration (spec §26)
| # | Step | Status |
|---|---|---|
| 1 | Upload or select sources | ✅ The three real BHSc sources are imported |
| 2 | Assign to historical/current contexts | ✅ |
| 3 | Extract course structure and candidate mappings | ✅ Deterministic and model-assisted extraction, scored in `evaluation.md` |
| 4 | Review an inferred relationship | ✅ |
| 5 | Four-year course map | ✅ |
| 6 | Select MDSC 407 | ✅ In both real versions (Year 3, required), with a documented description from the webpage |
| 7 | Documented description and unknowns | ✅ |
| 8 | Preparation relationships with evidence labels | ✅ |
| 9 | Scenario changing a topic or outcome | ✅ |
| 10 | Deterministic analysis with dependency paths | ✅ |
| 11 | Missing evidence shown | ✅ |
| 12 | Grounded assistant explanation | ✅ Verified citations; see [evaluation](evaluation.md) |
| 13 | Save and reopen scenario | ✅ |
| 14 | Export a readable report | ✅ HTML and Markdown (PDF not implemented) |

## Implemented but not verified against real conditions
- Extraction quality has been measured on three real documents only. Other layouts (calendar pages, course
  outlines with tables) are untested.
- **OCR** works with the local Tesseract. The scanned-page test accepts either OCR text or an "unreadable page"
  review item.
- **S3** is tested only with moto. No AWS resources were created.
- **Celery on SQS** is not exercised. The design relies on PostgreSQL job state plus idempotent handlers.

## Not implemented
- **Phase 6**: OIDC/Cognito, AWS infrastructure templates, backup automation, full accessibility audit.
- Controlled webpage import (URL fetcher), PDF export, entity field edits through review (field conflicts on
  credits and similar fields are recorded but can't be materialized), and a scenario projection cache.

## Known limitations
- Topics in the synthetic fixture are attached to courses without source spans. The UI marks them with an asterisk
  ("no source span") rather than hiding the gap.
- Workload analysis only has user assumptions to work with, because no documented hours exist in the fixture.
- Lane layout uses ELK partitions. Very dense graphs can route long edges across lanes; use layers, focus, or the
  table view.
