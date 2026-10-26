#!/usr/bin/env bash
# Copy the local workspace (database + original files) into an EMPTY hosted PostgreSQL, such as the Render
# database for the public prototype (docs/hosting-render.md). Run from the repository root:
#
#   1. Copy the database's External Database URL (Render dashboard → database → Connect → External).
#   2. scripts/load_into_render.sh            (reads the URL from the clipboard, or asks for it)
#
# It refuses to touch a database that already has tables, so it cannot overwrite a hosted workspace.
set -euo pipefail
cd "$(dirname "$0")/.."

PG16=/opt/homebrew/opt/postgresql@16/bin
LOCAL_DB=curriculum-flight-simulator-db-1
[ -x "$PG16/pg_restore" ] || { echo "Needs PostgreSQL 16 client tools: brew install postgresql@16"; exit 1; }
docker exec "$LOCAL_DB" pg_isready -U cfs -q || { echo "Start the local database first: ./run.sh (or docker compose -f infra/docker-compose.yml up -d db)"; exit 1; }

URL="${CFS_TARGET_URL:-}"
if [ -z "$URL" ] && command -v pbpaste >/dev/null; then
  CLIP="$(pbpaste | tr -d '[:space:]')"
  case "$CLIP" in postgres://*|postgresql://*) URL="$CLIP"; echo "Using the database URL from the clipboard." ;; esac
fi
if [ -z "$URL" ]; then
  read -rsp "Paste the External Database URL (hidden): " URL; echo
fi
case "$URL" in postgres://*|postgresql://*) ;; *) echo "That is not a postgres:// URL."; exit 1 ;; esac
TARGET="${URL##*@}"; echo "Target: ${TARGET%%\?*}"   # host/db only; the password is never printed

if ! TABLES=$("$PG16/psql" "$URL" -Atqc "select count(*) from pg_tables where schemaname = 'public'" 2>&1); then
  echo "Could not connect: $TABLES"
  echo "On Render, add this computer's IP under the database's Networking → Inbound IP rules, then retry."
  exit 1
fi
[ "$TABLES" = "0" ] || { echo "The target already has $TABLES tables. Nothing was changed."; exit 1; }

DUMP="$(mktemp -t cfs-dump)"; trap 'rm -f "$DUMP"' EXIT
echo "==> Dumping the local database"
docker exec "$LOCAL_DB" pg_dump -U cfs -Fc cfs > "$DUMP"
echo "==> Restoring into the target ($(du -h "$DUMP" | cut -f1))"
"$PG16/pg_restore" --no-owner --no-privileges -d "$URL" "$DUMP" || echo "   (pg_restore reported the warnings above; checking the result)"

echo "==> Bringing the schema up to date and copying original files"
(cd services/api && CFS_DATABASE_URL="$URL" CFS_ENV=development uv run alembic upgrade head)
(cd services/api && CFS_DATABASE_URL="$URL" CFS_ENV=development uv run python ../../scripts/copy_objects_to_db.py ../../data/objects)

echo "==> Comparing row counts (local → target)"
Q="select (select count(*) from programs)||' programs, '||(select count(*) from entities)||' entities, '||
   (select count(*) from relationship_revisions)||' links, '||(select count(*) from document_versions)||' documents'"
LOCAL=$(docker exec "$LOCAL_DB" psql -U cfs -d cfs -Atqc "$Q")
REMOTE=$("$PG16/psql" "$URL" -Atqc "$Q")
echo "   local:  $LOCAL"; echo "   target: $REMOTE"
[ "$LOCAL" = "$REMOTE" ] || { echo "Counts differ. Check the messages above."; exit 1; }
echo "Done. The hosted database now holds the local workspace."
