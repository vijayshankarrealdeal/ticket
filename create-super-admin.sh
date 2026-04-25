#!/usr/bin/env bash
# Bootstrap (or reset) a WorkspaceX platform super-user — and optionally create
# a first organization in the same run.
#
# Works in three environments, picked automatically:
#   1. Inside the api container (we're already there → run python directly).
#   2. On the host with `docker compose` available + the api service up
#      → exec into the running container.
#   3. On the host without docker → run python via the venv/conda env that
#      can import `app.create_super_admin`.
#
# Examples:
#   ./create-super-admin.sh                                 # interactive
#   ./create-super-admin.sh --email me@x.com --password pw  # non-interactive
#   ./create-super-admin.sh --reset                         # interactive reset
#
set -euo pipefail

EMAIL=""
PASSWORD=""
ORG=""
NAME="Platform Admin"
RESET=0

# ---------- arg parsing ------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --email)    EMAIL="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --org)      ORG="$2"; shift 2 ;;
    --name)     NAME="$2"; shift 2 ;;
    --reset)    RESET=1; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown flag: $1" >&2
      exit 2
      ;;
  esac
done

# ---------- interactive prompts (only for fields not already provided) -------
# Treat the run as "interactive" if we have a TTY *and* the user didn't pass
# every required field on the CLI. Org is optional, so we only ask for it when
# the user hasn't supplied any of the other flags either.
INTERACTIVE=0
if [[ -t 0 && (-z "$EMAIL" || -z "$PASSWORD") ]]; then
  INTERACTIVE=1
fi

if [[ -z "$EMAIL" ]]; then
  read -rp "Super-user email: " EMAIL
fi
if [[ -z "$EMAIL" || "$EMAIL" != *"@"* ]]; then
  echo "A valid email is required." >&2
  exit 1
fi

if [[ "$INTERACTIVE" == "1" && -z "$ORG" ]]; then
  read -rp "Organization name (blank to skip): " ORG
fi

if [[ -z "$PASSWORD" ]]; then
  # `read -s` hides the input — do it twice and confirm.
  read -rsp "Password: " PASSWORD; echo
  read -rsp "Confirm:  " CONFIRM; echo
  if [[ "$PASSWORD" != "$CONFIRM" ]]; then
    echo "Passwords do not match." >&2
    exit 1
  fi
fi
if [[ ${#PASSWORD} -lt 8 ]]; then
  echo "Password must be at least 8 characters." >&2
  exit 1
fi

# ---------- assemble the python args ----------------------------------------
PY_ARGS=(--email "$EMAIL" --password "$PASSWORD" --name "$NAME")
[[ -n "$ORG" ]] && PY_ARGS+=(--org "$ORG")
[[ "$RESET" == "1" ]] && PY_ARGS+=(--reset)

# ---------- pick a runtime ---------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"

run_in_container() {
  # Detect compose v2 vs v1; return 1 if neither is installed.
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
  else
    return 1
  fi
  # If the api service isn't running, bail to the host fallback.
  if ! (cd "$SCRIPT_DIR" && "${DC[@]}" ps --status running --services 2>/dev/null | grep -qx api); then
    return 1
  fi
  echo "→ Running inside docker (service: api) ..."
  (cd "$SCRIPT_DIR" && "${DC[@]}" exec -T api python -m app.create_super_admin "${PY_ARGS[@]}")
}

run_on_host() {
  if [[ ! -d "$BACKEND_DIR/app" ]]; then
    echo "Cannot find backend/app/ next to this script." >&2
    exit 1
  fi
  echo "→ Running on host (cwd: backend) ..."
  (cd "$BACKEND_DIR" && python -m app.create_super_admin "${PY_ARGS[@]}")
}

# Already inside the api container? `app/create_super_admin.py` is on the
# default WORKDIR (/app or /code, depending on the Dockerfile); just run it.
if [[ -f "/app/app/create_super_admin.py" ]]; then
  echo "→ Detected in-container execution ..."
  cd /app && exec python -m app.create_super_admin "${PY_ARGS[@]}"
fi

if run_in_container; then
  exit 0
fi

run_on_host
