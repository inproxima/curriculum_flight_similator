#!/usr/bin/env bash
# Curriculum Flight Simulator — local launcher.
#
#   ./run.sh            start everything (db, redis, api, worker, web) and open the app
#   ./run.sh stop       stop the stack (data is kept)
#   ./run.sh restart    restart the stack
#   ./run.sh status     container status and readiness checks
#   ./run.sh logs       follow api + worker logs
#   ./run.sh seed       load the SYNTHETIC fixture program (idempotent)
#   ./run.sh test       backend + frontend tests (end-to-end: ./run.sh e2e)
#   ./run.sh e2e        Playwright end-to-end + accessibility tests (stack must be running)
#   ./run.sh reset      DELETE all app data, re-migrate, re-seed synthetic data (asks first)
#
# Ports (host): web 5173, API 8010, PostgreSQL 5433, Redis 6380.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=(docker compose -f "$ROOT/infra/docker-compose.yml")
WEB_URL="http://localhost:5173"
API_URL="http://localhost:8010"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
fail() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

require_docker() {
  command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop first."
  docker info >/dev/null 2>&1 || fail "Docker is not running. Start Docker Desktop and try again."
}

ensure_env() {
  if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    info "Created .env from .env.example (AI features stay off until you add API keys to .env)."
  fi
  mkdir -p "$ROOT/data/objects"
}

check_ports() {
  local busy=()
  for p in 5173 8010 5433 6380; do
    # A port held by this stack's own containers is fine.
    if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1 && ! "${COMPOSE[@]}" ps --format '{{.Ports}}' 2>/dev/null | grep -q ":$p->"; then
      busy+=("$p")
    fi
  done
  [ ${#busy[@]} -eq 0 ] || fail "Port(s) ${busy[*]} are in use by another program. Stop it or change the ports in infra/docker-compose.yml."
}

wait_for() {
  local url="$1" name="$2" tries="${3:-90}"
  printf '  Waiting for %s' "$name"
  for _ in $(seq 1 "$tries"); do
    if curl -sf -o /dev/null "$url"; then printf ' ✓\n'; return 0; fi
    printf '.'; sleep 2
  done
  printf '\n'; fail "$name did not become ready. Run './run.sh logs' to see why."
}

open_browser() {
  [ -n "${CFS_NO_BROWSER:-}" ] && return 0
  if command -v open >/dev/null 2>&1; then open "$WEB_URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$WEB_URL" >/dev/null 2>&1 || true
  fi
}

start() {
  require_docker
  ensure_env
  check_ports
  bold "Starting Curriculum Flight Simulator…"
  "${COMPOSE[@]}" up -d --build
  wait_for "$API_URL/api/v1/health" "API"
  wait_for "$WEB_URL/" "web app"
  if ! curl -sf "$API_URL/api/v1/programs" | grep -q '"id"'; then
    info "No programs yet — loading the SYNTHETIC fixture program…"
    "${COMPOSE[@]}" exec -T api python -m cfs.fixtures.seed
  fi
  echo
  bold "Ready"
  info "App:      $WEB_URL"
  info "API docs: $API_URL/docs"
  info "Stop with './run.sh stop'. Data is kept in Docker volume 'pgdata' and ./data/objects."
  if grep -qE '^CFS_(OPENAI|ANTHROPIC)_API_KEY=.+' "$ROOT/.env"; then
    info "AI: provider key(s) found in .env — AI actions send selected document excerpts to those providers."
  else
    info "AI: no provider keys in .env — AI features are off; everything else works."
  fi
  open_browser
}

status() {
  require_docker
  "${COMPOSE[@]}" ps
  echo
  if curl -sf "$API_URL/api/v1/ready" -o /tmp/cfs-ready.json 2>/dev/null; then
    python3 -c "import json;d=json.load(open('/tmp/cfs-ready.json'));print('Ready:',d['ready']);[print(f'  {k}: {\"ok\" if v.get(\"ok\") else \"NOT OK\"} {({x:y for x,y in v.items() if x!=\"ok\"})}') for k,v in d['checks'].items()]"
  else
    info "API is not responding at $API_URL."
  fi
}

case "${1:-start}" in
  start|up) start ;;
  stop|down) require_docker; "${COMPOSE[@]}" down; bold "Stopped (data kept)." ;;
  restart) require_docker; "${COMPOSE[@]}" down; start ;;
  status) status ;;
  logs) require_docker; "${COMPOSE[@]}" logs -f api worker ;;
  seed) require_docker; "${COMPOSE[@]}" exec -T api python -m cfs.fixtures.seed ;;
  test)
    require_docker
    "${COMPOSE[@]}" up -d db redis >/dev/null
    make -C "$ROOT" test-api test-web
    ;;
  e2e) (cd "$ROOT/apps/web" && npx playwright test) ;;
  reset)
    require_docker
    printf 'This deletes ALL application data (programs, documents, reviews, scenarios). Type "reset" to continue: '
    read -r answer
    [ "$answer" = "reset" ] || { info "Cancelled."; exit 0; }
    make -C "$ROOT" reset
    ;;
  -h|--help|help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) fail "Unknown command '$1'. Run './run.sh help'." ;;
esac
