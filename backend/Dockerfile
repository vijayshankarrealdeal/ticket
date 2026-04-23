# syntax=docker/dockerfile:1.7

# WorkspaceX API + workers. Same image, role picked at runtime with APP_ROLE.
# Stack: FastAPI + Uvicorn (api), same codebase run as worker (spec §11).

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# System deps: libpq for psycopg, build tools for any wheels that need compiling,
# curl for healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      libpq-dev \
      curl \
    && rm -rf /var/lib/apt/lists/*

# ---- deps layer ----
FROM base AS deps
COPY requirements.txt* pyproject.toml* poetry.lock* ./
RUN if [ -f requirements.txt ]; then \
        pip install -r requirements.txt ; \
    else \
        pip install \
          "fastapi[standard]" \
          "uvicorn[standard]" \
          "pydantic>=2" \
          "pydantic-ai" \
          "sqlalchemy>=2" \
          "asyncpg" \
          "psycopg[binary]" \
          "alembic" \
          "redis>=5" \
          "httpx" \
          "python-jose[cryptography]" \
          "passlib[argon2]" \
          "python-multipart" \
          "boto3" \
          "openai" ; \
    fi

# ---- runtime ----
FROM base AS runner
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

RUN addgroup --system app && adduser --system --ingroup app app
COPY . .
RUN chown -R app:app /app
USER app

ENV PORT=8000 \
    APP_ROLE=api \
    APP_MODULE=app.main:app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD curl -fsS http://localhost:${PORT}/healthz || exit 1

# APP_ROLE=api   -> uvicorn serves FastAPI
# APP_ROLE=worker -> runs the background worker entrypoint (spec §11)
CMD ["sh", "-c", "\
if [ \"$APP_ROLE\" = \"worker\" ]; then \
  python -m app.worker ; \
else \
  uvicorn $APP_MODULE --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*' ; \
fi"]
