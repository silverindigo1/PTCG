#!/bin/bash
# Prepare a Claude Code cloud session so `make test` and `make demo` run as-is.
#
# Runs only in cloud sessions (CLAUDE_CODE_REMOTE=true); on a laptop it exits
# immediately. Every step is idempotent, because the hook also runs on resume,
# when the database may already exist.
#
# Why a hook rather than a setup script: the environment cache keeps files but
# not running processes, so a database started at setup time is gone in the
# next session. Starting it here, every session, is the documented pattern:
# https://code.claude.com/docs/en/cloud-environments

set -u

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Set by Claude Code when it runs the hook; derived from the script's own
# location when someone runs the script by hand.
CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$CLAUDE_PROJECT_DIR" || exit 0
log() { echo "[pokearb setup] $*"; }

# Python dependencies. The core package supports 3.11 and later.
# The session VM is disposable, so installing into the system interpreter is
# acceptable; --break-system-packages is only tried when PEP 668 refuses.
pip_install() {
  python3 -m pip install -q "$@" 2>/dev/null \
    || python3 -m pip install -q --break-system-packages "$@"
}
if ! python3 -c "import fastapi, uvicorn, asyncpg, psycopg, httpx, pytest, pytest_asyncio" >/dev/null 2>&1; then
  log "installing Python dependencies"
  pip_install -r requirements-dev.txt || log "dependency install failed"
fi
python3 -c "import pokearb_core" >/dev/null 2>&1 \
  || pip_install -e packages/core >/dev/null 2>&1 \
  || log "core package install failed"

# PostgreSQL is pre-installed in cloud sessions but not running.
if ! pg_isready -q -h 127.0.0.1 -p 5432 2>/dev/null; then
  log "starting PostgreSQL"
  service postgresql start >/dev/null 2>&1 || log "could not start PostgreSQL"
  for _ in $(seq 1 20); do pg_isready -q -h 127.0.0.1 -p 5432 && break; sleep 1; done
fi

as_pg() { su postgres -c "$1"; }

as_pg "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='pokearb'\"" | grep -q 1 \
  || as_pg "psql -q -c \"CREATE ROLE pokearb LOGIN PASSWORD 'pokearb'\""
as_pg "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='pokearb'\"" | grep -q 1 \
  || as_pg "createdb -O pokearb pokearb"

export DATABASE_URL="postgresql://pokearb:pokearb@127.0.0.1:5432/pokearb"
export PGOPTIONS="--client-min-messages=warning"

# Migrations are tracked, so a database created by an earlier version of this
# hook picks up new migrations on the next session instead of silently staying
# behind. Seeds run once, on a fresh database only.
q() { psql "$DATABASE_URL" -tAc "$1"; }
fresh=false
q "SELECT to_regclass('public.source')" | grep -q source || fresh=true
psql "$DATABASE_URL" -q -c "CREATE TABLE IF NOT EXISTS schema_migration (
  version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
if [ "$fresh" = false ] && [ "$(q 'SELECT count(*) FROM schema_migration')" = "0" ]; then
  # Built before tracking existed, by a hook that always applied 0001-0003.
  for v in 0001 0002 0003; do
    q "INSERT INTO schema_migration (version) VALUES ('$v') ON CONFLICT DO NOTHING" >/dev/null
  done
fi
for f in migrations/versions/*.sql; do
  v=$(basename "$f" | cut -c1-4)
  if ! q "SELECT 1 FROM schema_migration WHERE version='$v'" | grep -q 1; then
    log "applying migration $(basename "$f")"
    if psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -f "$f" >/dev/null; then
      q "INSERT INTO schema_migration (version) VALUES ('$v')" >/dev/null
    else
      log "failed on $f; later migrations not applied"
      break
    fi
  fi
done
if [ "$fresh" = true ]; then
  log "loading seeds"
  for f in seed/sources.sql seed/policy.sql seed/condition_priors.sql seed/demo/sources.sql; do
    psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -f "$f" >/dev/null || {
      log "failed on $f"
      break
    }
  done
fi

# Hand the connection string to the commands Claude runs in this session.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export DATABASE_URL=\"$DATABASE_URL\"" >> "$CLAUDE_ENV_FILE"
fi
log "ready: DATABASE_URL=$DATABASE_URL"
exit 0
