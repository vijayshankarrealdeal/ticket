#!/usr/bin/env bash
# Dev harness — runs the API + background worker with auto-reload under one
# process tree. Both watch `app/**` via watchfiles, so any .py edit restarts
# the relevant process. Ctrl-C shuts both down.
#
# Assumes datastores are already up (`docker compose up -d db minio redis`
# from the repo root). If you're on the `environment.yml` conda env, activate
# it first; the `uvicorn` / `watchfiles` binaries both live on PATH there.
set -euo pipefail

cd "$(dirname "$0")"

export DATABASE_URL="${DATABASE_URL:-postgresql://workspacex:workspacex@localhost:5432/workspacex}"
export S3_ENDPOINT="${S3_ENDPOINT:-http://localhost:9000}"

pids=()
cleanup() { trap - INT TERM; kill "${pids[@]}" 2>/dev/null || true; wait || true; }
trap cleanup INT TERM EXIT

echo "▶ api:    uvicorn app.main:app --reload --port 8000"
uvicorn app.main:app --reload --reload-dir app --port 8000 &
pids+=($!)

echo "▶ worker: watchfiles 'python -m app.worker' app"
# watchfiles is bundled with uvicorn[standard]; if it's missing, fall back to
# a plain restart loop so the worker still runs (just without hot reload).
if command -v watchfiles >/dev/null 2>&1; then
  watchfiles --filter python "python -m app.worker" app &
else
  echo "  (watchfiles not found — running worker without hot reload)"
  python -m app.worker &
fi
pids+=($!)

wait
