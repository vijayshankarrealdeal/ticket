#!/bin/sh
# Postgres init script — runs once on first boot (the official postgres image
# executes every .sh / .sql in /docker-entrypoint-initdb.d/ when the data
# directory is empty).
#
# We create a second database `langflow` so the langflow container's
# LANGFLOW_DATABASE_URL can connect. Without this the Langflow container
# crash-loops with `FATAL: database "langflow" does not exist`.
#
# Idempotent: `CREATE DATABASE IF NOT EXISTS` doesn't exist in Postgres, so we
# use a DO block guarded by pg_database lookup.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  SELECT 'CREATE DATABASE langflow OWNER "$POSTGRES_USER"'
   WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'langflow')\gexec
EOSQL
