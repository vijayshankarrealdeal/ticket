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


# --- Database (Postgres 16 via asyncpg) ---
# Accept both asyncpg and SQLAlchemy-style DSNs; normalize to vanilla postgres://.
_RAW_DB_URL = _env(
    "DATABASE_URL",
    "postgresql://workspacex:workspacex@localhost:5432/workspacex",
)
DATABASE_URL = (
    _RAW_DB_URL.replace("postgresql+asyncpg://", "postgresql://")
               .replace("postgres+asyncpg://", "postgres://")
)


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
# When true (default), startup runs `schema.sql` and seeds a demo workspace if
# the DB is empty. Flip to "0" for prod where migrations run out-of-band.
AUTO_MIGRATE = _env("AUTO_MIGRATE", "1") != "0"
AUTO_SEED = _env("AUTO_SEED", "1") != "0"
