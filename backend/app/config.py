"""Runtime config.

Defaults match [docker-compose.yml](../docker-compose.yml) so the stack boots
without any `.env` editing. Override via environment variables in prod.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Load a `.env` file from the project root into `os.environ` if the key
    isn't already set. Tiny hand-rolled parser so we don't take on a
    python-dotenv dep; handles `KEY=value`, `KEY="value with spaces"`, and
    comments. Skips empty lines and any existing env vars (real env wins)."""
    for candidate in (BASE_DIR / ".env", BASE_DIR.parent / ".env"):
        if not candidate.exists():
            continue
        try:
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except Exception:
            pass


_load_dotenv()


def _env(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val is not None and val != "" else default


import re as _re_db
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# --- Database (Postgres 16 via asyncpg) ---
# Accept both asyncpg and SQLAlchemy-style DSNs; normalize to vanilla postgres://.
# Also rewrite `ssl=…` to `sslmode=…` in the query string — asyncpg parses
# sslmode as a connection option but treats raw `ssl` as a server_setting
# which Postgres rejects with `parameter "ssl" cannot be changed now`. Hits
# Azure Database for PostgreSQL Flexible Server which mandates TLS.
#
# Use a real URL parser instead of string `.replace`/regex so unrelated query
# params that happen to contain `ssl=` substrings (or end in the literal
# string "ssl") aren't munged. `application_name=ssl-app` would have been
# corrupted by the prior implementation.
_RAW_DB_URL = _env(
    "DATABASE_URL",
    "postgresql://workspacex:workspacex@localhost:5432/workspacex",
)
_NORMALIZED = (
    _RAW_DB_URL.replace("postgresql+asyncpg://", "postgresql://")
               .replace("postgres+asyncpg://", "postgres://")
)


def _normalize_pg_dsn(dsn: str) -> str:
    """Rewrite the `ssl` query parameter (key) to `sslmode` without touching
    other params. Returns `dsn` unchanged when there's no query string."""
    parts = urlsplit(dsn)
    if not parts.query:
        return dsn
    rebuilt = [
        ("sslmode" if k == "ssl" else k, v)
        for (k, v) in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(rebuilt)))


DATABASE_URL = _normalize_pg_dsn(_NORMALIZED)


# --- Redis (optional — in-proc broker falls back if unset) ---
REDIS_URL = _env("REDIS_URL", "")


# --- Object storage (MinIO, S3-compatible) ---
S3_ENDPOINT_URL = _env("S3_ENDPOINT", "http://localhost:9000")
S3_PUBLIC_URL = _env("S3_PUBLIC_URL", S3_ENDPOINT_URL)  # URL served to browsers
S3_BUCKET = _env("S3_BUCKET", "workspacex")
S3_REGION = _env("S3_REGION", "us-east-1")
S3_ACCESS_KEY = _env("S3_ACCESS_KEY", "workspacex")
S3_SECRET_KEY = _env("S3_SECRET_KEY", "workspacex-secret")
S3_PRESIGN_EXPIRES = int(_env("S3_PRESIGN_EXPIRES", "300"))  # seconds


# --- HTTP / auth ---
CORS_ORIGINS = _env(
    "CORS_ORIGINS",
    # Include the Langflow origin by default so the WorkspaceX rebrand
    # overlay (deploy/langflow/wx-rebrand.js) can call back to us from the
    # Langflow tab when the user clicks Export. The `credentials: 'include'`
    # path needs an explicit origin match, not `*`.
    "http://localhost:5173,http://localhost:3000,http://localhost:7860",
).split(",")
SESSION_COOKIE = "wx_session"
SESSION_TTL_DAYS = 30


# --- Lifecycle ---
# AUTO_MIGRATE: when true (default), startup runs `schema.sql`. Flip to "0" for
# prod where migrations run out-of-band.
# AUTO_SEED: defaults OFF — the demo seed (The Sleep Company workspace, sample
# customers/tickets) is dev-only. Set "1" explicitly in local docker-compose
# for a populated dev DB.
AUTO_MIGRATE = _env("AUTO_MIGRATE", "1") != "0"
AUTO_SEED = _env("AUTO_SEED", "0") != "0"
