# Curriculum Flight Simulator

Curriculum decision support. It turns program documents into an evidence-traced curriculum map, supports review
of extracted and inferred relationships, and lets curriculum leaders explore changes with deterministic,
explainable impact analysis.

**It doesn't** predict learning gains, certify accreditation compliance, or describe what individual students have
mastered.

> **Two kinds of data.** The bundled "Synthetic Biomedical Sciences (fixture)" program (course codes `SYN ###`) is
> invented test data, labelled throughout the UI and in exports. "Biomedical Sciences (BHSc)" is imported from real
> University of Calgary documents in `outputs/` (2025–26 outline, program webpage extraction, 2019 review).

Status: all six phases are implemented and tested locally:

1. Environment
2. Ingestion and evidence
3. Map and analysis
4. Scenarios
5. AI assistance
6. Security, accessibility, CI, and AWS infrastructure definitions

The AWS infrastructure is validated but has **not been applied**; nothing runs in AWS yet. See
[docs/implementation-report.md](docs/implementation-report.md), [docs/deployment.md](docs/deployment.md),
[docs/operations.md](docs/operations.md), and [docs/evaluation.md](docs/evaluation.md).

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
1. **Documents**: create a program and draft versions, then upload PDF (text or scanned), DOCX, text, or pasted
   text. **Run AI extraction** to propose courses, option rules, outcomes, and stated preparation from complex
   layouts. Publication date, retrieval date,
   academic year, and cohort are separate fields, and none are guessed. Progress streams live.
2. **Assign** each document version to a curriculum version with an authority (authoritative, supporting, historical,
   exploratory). Nothing is merged implicitly.
3. **Review inbox**: accept, edit, reject, or defer new courses, requisites, outcomes, conflicts (both sides kept),
   inferred mappings, duplicates, and missing metadata. Accepting changes draft versions only.
4. **Map**: year/term lanes, layers, expand courses (**+**), select nodes or edges, open evidence (📄) at the source
   page. The **Expected preparation** and **Future use** tabs highlight paths.
5. **Assistant** (map view, **Assistant** button): Explore, Investigate, or Simulate. Answers cite verified source
   passages, can highlight the map, and may include proposal cards (**Apply to scenario**, **Request critique**).
6. **Outcomes**: course × program-outcome matrix with a defined denominator, assessment alignment, potential issues.
7. **Scenarios**: create a scenario, edit from a course's **Scenario edit** tab (preview → apply), undo/redo, run the
   deterministic analysis, compare, rebase, export, or publish as a new version (editor role).
8. **Table**: keyboard-accessible alternative to the map, plus documented option rules.

## API keys and external AI
Put keys in `.env` (`CFS_OPENAI_API_KEY`, `CFS_ANTHROPIC_API_KEY`). They stay on the server and are never sent to
the browser. **A local deployment isn't offline AI**: each AI action sends selected excerpts of the assigned
documents to the provider shown for its route (see **AI & usage**).

- **Routes** ([ADR 0002](docs/adr/0002-ai-gateway.md)):
  - extraction, grounded questions, and tool use: `gpt-6-sol`
  - metadata: `gpt-6-luna`
  - cross-course synthesis: `gpt-6-astra`
  - drafting changes (Simulate): `claude-sonnet-5`
  - critique: `claude-opus-5-5`
  - embeddings: `text-embedding-3-small`

  The app works with only one provider configured: each route falls back to the other provider, or reports
  `provider_unconfigured`.
- **Controls:**
  - `CFS_AI_PROVIDER_ALLOWLIST` limits which providers any content may reach.
  - A per-document **AI policy** on the Documents page can restrict or forbid sending that document to a model.
  - Monthly and per-job budgets are set with `CFS_AI_MONTHLY_BUDGET_USD` and `CFS_AI_MAX_COST_PER_JOB_USD`.
- **What AI does here:**
  - Proposes extraction candidates and outcome alignments. These are quote-verified and always go to the review
    inbox.
  - Answers questions with verified citations.
  - Drafts scenario changes, which you apply explicitly.
  - Critiques proposals and explains deterministic analyses.

  It never writes curriculum data directly, runs the impact analysis, or publishes.
- **Audit:** every model call is recorded (route, returned model, tokens, cost, evidence IDs, tool calls) under
  **AI & usage**.

Without keys, the map, evidence, review, and deterministic analysis all still work, and AI actions report that the
provider is unconfigured.

## Sign-in and security
- **Local development** uses single-user mode (localhost only). With `CFS_ENV=production` the app refuses to
  start unless all of these hold:
  - OIDC is configured;
  - a strong `CFS_SECRET_KEY` is set;
  - storage is S3;
  - CORS has no localhost origins.
- **OIDC** (Cognito or an institutional IdP): the SPA signs in with Authorization Code + PKCE. The API verifies
  tokens against the issuer's keys. Groups `cfs-admin`, `cfs-editor`, `cfs-reviewer` and `cfs-viewer` map to
  roles, and users without one are denied.
- **Webpage import** (Documents page):
  - Only allowlisted public domains (`CFS_FETCH_ALLOWED_DOMAINS`).
  - Internal and metadata addresses are refused, and each redirect is re-checked.
  - Downloads are size- and time-limited, and HTML is stored as text.
- **Other protections:** security headers on every response, per-user rate limits for uploads and AI actions, and
  an audit trail for reviews, publishing, fetches, AI actions and role changes.

## AWS deployment
Terraform in [`infra/terraform`](infra/terraform) defines the AWS stack:

- **Web:** CloudFront serves the SPA from private S3 and routes `/api/*` to an ALB.
- **Compute:** ECS Fargate for the API, worker, and migration task.
- **Data:** RDS PostgreSQL 16 with pgvector, S3 for documents, SQS for jobs.
- **Platform:** Secrets Manager, optional Cognito, and CloudWatch alarms.

Follow [docs/deployment.md](docs/deployment.md), including the cost notes. `scripts/deploy.sh <env>` builds,
migrates, rolls out, and publishes.

## Tests
```bash
make test-api   # pytest (uses the cfs_test database; jobs inline)
make test-web   # vitest
make e2e        # Playwright against a running stack (make up + seed); includes axe WCAG 2.1 AA scans
cd services/api && uv run python -m cfs.evals.bhsc --retrieval   # extraction/retrieval eval on the real sources
```
Backend AI tests use fake adapters and never contact a provider. CI (`.github/workflows/ci.yml`) runs:

- backend lint and tests against pgvector;
- an OpenAPI drift check;
- the frontend type check, unit tests, and build;
- `terraform fmt` and `validate`;
- an image build.

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
