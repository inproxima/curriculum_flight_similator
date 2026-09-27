# Curriculum Flight Simulator

Curriculum decision support. It turns program documents into an evidence-traced curriculum map, supports review
of extracted and inferred relationships, and lets curriculum leaders explore changes with deterministic,
explainable impact analysis.

**It doesn't** predict learning gains, certify accreditation compliance, or describe what individual students have
mastered.

> **Current data is SYNTHETIC.** The bundled program ("Synthetic Biomedical Sciences (fixture)", course codes
> `SYN ###`) is invented test data, labelled throughout the UI and in exports. It is **not** University of Calgary
> data. Import real sources through the Documents page when they're available.

Status: Phases 1–4 (environment, ingestion and evidence, map and analysis, scenarios) are implemented and tested.
AI assistance (Phase 5) and AWS deployment (Phase 6) are **not implemented**. See
[docs/implementation-report.md](docs/implementation-report.md).

## Quick start (Docker)
Requirements: Docker Desktop. On macOS, the repository folder must be shared with Docker (Settings → Resources →
File sharing) if it lives on an external volume.

```bash
make up              # builds and starts db, redis, api, worker, web
make seed-synthetic  # loads the SYNTHETIC fixture (idempotent)
```
- Web: http://localhost:5173
- API docs: http://localhost:8010/docs

Host ports: PostgreSQL `5433`, Redis `6380`, API `8010`, web `5173`. They're chosen to avoid clashing with other
local services.

`make reset` drops all application data, re-runs migrations, clears stored originals, and re-seeds the synthetic
fixture. `make down` stops the stack. Data persists in the `pgdata` volume and in `./data/objects`.

## Host-side development (faster reloads)
```bash
docker compose -f infra/docker-compose.yml up -d db redis
make deps
make migrate
cd services/api && uv run python -m cfs.fixtures.seed
make api     # FastAPI on :8010 (reload)
make worker  # Celery worker
make web     # Vite on :5173 (proxies /api to :8010)
```
Set `CFS_TASK_ALWAYS_EAGER=true` to run jobs inline without a worker.

## Using the app
1. **Documents**: upload PDF (text or scanned), DOCX, text, or pasted text. Publication date, retrieval date,
   academic year, and cohort are separate fields, and none are guessed. Progress streams live.
2. **Assign** each document version to a curriculum version with an authority (authoritative, supporting, historical,
   exploratory). Nothing is merged implicitly.
3. **Review inbox**: accept, edit, reject, or defer new courses, requisites, outcomes, conflicts (both sides kept),
   inferred mappings, duplicates, and missing metadata. Accepting changes draft versions only.
4. **Map**: year/term lanes, layers, expand courses (**+**), select nodes or edges, open evidence (📄) at the source
   page. The **Expected preparation** and **Future use** tabs highlight paths.
5. **Outcomes**: course × program-outcome matrix with a defined denominator, assessment alignment, potential issues.
6. **Scenarios**: create a scenario, edit from a course's **Scenario edit** tab (preview → apply), undo/redo, run the
   deterministic analysis, compare, rebase, export, or publish as a new version (editor role).
7. **Table**: keyboard-accessible alternative to the map.

## API keys and external AI
AI features aren't implemented in this build. The app runs fully without credentials, and AI endpoints respond
`503 provider_unconfigured`. When Phase 5 is added, keys go in `.env` (`CFS_OPENAI_API_KEY`,
`CFS_ANTHROPIC_API_KEY`) and stay on the server. **A local deployment isn't offline AI**: configured model calls
would send selected document excerpts to that provider.

## Tests
```bash
make test-api   # pytest (uses the cfs_test database; jobs inline)
make test-web   # vitest
make e2e        # Playwright against a running stack (make up + seed)
```

## Backups
```bash
docker compose -f infra/docker-compose.yml exec db pg_dump -U cfs -Fc cfs > backup-$(date +%F).dump
tar czf objects-$(date +%F).tgz data/objects
```
Restore:
```bash
docker compose -f infra/docker-compose.yml exec -T db pg_restore -U cfs -d cfs --clean < backup-YYYY-MM-DD.dump
```
Then extract the objects archive into `data/objects`. Originals are content-addressed by sha256 and referenced
from `document_versions.object_key`.

## Troubleshooting
- **Port in use**: another service holds 5433, 6380, 8010, or 5173. Stop it, or change the host port in
  `infra/docker-compose.yml` (and the matching setting in `.env` or `vite.config.ts`).
- **Jobs stay queued**: the worker isn't running (`make logs`). For host-side development, run `make worker` or set
  `CFS_TASK_ALWAYS_EAGER=true`.
- **Scanned pages unreadable**: Tesseract isn't installed on the host (it's included in the Docker image). The
  page is flagged in the review inbox.
- **PDF viewer blank**: the original is missing from `data/objects` (for example after deleting the folder).
  Re-upload the file or run `make reset`.
- **Analysis looks stale**: runs are cached by input hash. If you changed engine rules, bump
  `ENGINE_VERSION` in `services/api/cfs/scenarios/engine.py`.

## Repository layout
```
apps/web            React + TypeScript (Vite, React Flow, ELK, TanStack Query, Zustand, PDF.js, Recharts)
services/api/cfs    FastAPI app and domain: models, ingest, curriculum, graph, analysis, scenarios, storage, ai (stub)
services/worker     Celery entry point (imports cfs)
packages/contracts  OpenAPI snapshot (source of the generated TS client)
infra               Docker Compose, Dockerfile, DB init
fixtures/synthetic  SYNTHETIC program definition (not real data)
tests/api           Backend tests
docs                ADR, domain model, performance, implementation report
```
