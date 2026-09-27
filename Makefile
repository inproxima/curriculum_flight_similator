# Curriculum Flight Simulator — developer commands. See README.md.
COMPOSE = docker compose -f infra/docker-compose.yml
API = cd services/api && uv run

.PHONY: up down deps migrate seed-synthetic reset test test-api test-web e2e gen-client api worker web logs lint

up:            ## Start everything (db, redis, api, worker, web) in Docker
	@test -f .env || cp .env.example .env
	$(COMPOSE) up -d --build
	@echo "Web: http://localhost:5173   API: http://localhost:8010/docs"

down:
	$(COMPOSE) down

deps:          ## Install locked dependencies for host-side development
	cd services/api && uv sync
	cd apps/web && pnpm install --frozen-lockfile

migrate:
	$(API) alembic upgrade head

seed-synthetic:  ## Load the SYNTHETIC fixture program (not University of Calgary data)
	$(COMPOSE) exec api python -m cfs.fixtures.seed

reset:         ## Drop all application data, re-migrate, and re-seed synthetic data
	$(COMPOSE) exec api sh -c "alembic downgrade base && alembic upgrade head && rm -rf /data/objects/orgs && python -m cfs.fixtures.seed"

# Host-side (no Docker for api/worker/web; still needs `$(COMPOSE) up -d db redis`)
api:
	$(API) uvicorn cfs.main:app --reload --host 127.0.0.1 --port 8010
worker:
	cd services/api && PYTHONPATH=.:../worker uv run celery -A cfs_worker.app worker --loglevel=INFO
web:
	cd apps/web && pnpm dev

test: test-api test-web

test-api:      ## Backend tests against the cfs_test database
	cd services/api && CFS_ENV=test CFS_DATABASE_URL=postgresql+psycopg://cfs:cfs@localhost:5433/cfs_test \
	  CFS_TASK_ALWAYS_EAGER=true CFS_STORAGE_ROOT=/tmp/cfs-test-objects uv run pytest

test-web:
	cd apps/web && pnpm test

e2e:           ## Playwright end-to-end (requires `make up` + seed)
	cd apps/web && pnpm e2e

gen-client:    ## Regenerate OpenAPI snapshot and the typed TS client
	$(API) python -m cfs.openapi_dump ../../packages/contracts/openapi.json
	cd apps/web && pnpm gen:api

logs:
	$(COMPOSE) logs -f api worker

lint:
	$(API) ruff check cfs
