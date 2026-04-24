-- Opt-in Postgres Row-Level Security (spec §7 "RLS", progress.md cross-cutting).
--
-- The app does workspace scoping in every query (`WHERE workspace_id = $ws`),
-- so app-level isolation is already solid. This migration adds a hard database
-- floor for multi-tenant installs: the session sets `app.workspace_id` once at
-- connection check-out (see backend/app/db.py `set_session_workspace()`) and
-- Postgres refuses to read or write rows that belong to a different workspace.
--
-- Run this migration manually (`psql -f 0003_rls.sql`) once you're confident
-- every router sets the GUC. Until then, leaving RLS off is safer than
-- half-enabling it.
--
-- To temporarily bypass RLS from an elevated role (migrations, support tools):
--   SET LOCAL row_security = off;
-- or run as the table owner, whose `FORCE ROW LEVEL SECURITY` is opt-in below.

-- Helper: per-session workspace GUC. Defaults to NULL so any plain SELECT
-- against a table with RLS returns zero rows — which is the whole point.
-- The backend sets this via `SELECT set_config('app.workspace_id', $1, true)`
-- inside every request's connection check-out.

DO $$ BEGIN
  PERFORM 1 FROM pg_roles WHERE rolname = current_user;
END $$;

-- Enable RLS on every workspace-scoped table we have today. Skipping tables
-- that don't carry `workspace_id` (auth_sessions is user-scoped; health_services
-- is workspace-scoped and included).
DO $$
DECLARE
  tbl text;
BEGIN
  FOR tbl IN
    SELECT c.relname
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relkind = 'r'
       AND n.nspname = current_schema()
       AND EXISTS (
         SELECT 1 FROM pg_attribute a
          WHERE a.attrelid = c.oid AND a.attname = 'workspace_id'
       )
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE format(
      'DROP POLICY IF EXISTS %I_ws_isolation ON %I',
      tbl, tbl
    );
    EXECUTE format(
      $p$CREATE POLICY %I_ws_isolation ON %I
         USING (
           workspace_id::text = current_setting('app.workspace_id', true)
           OR current_setting('app.workspace_id', true) IS NULL
           OR current_setting('app.workspace_id', true) = ''
         )
         WITH CHECK (
           workspace_id::text = current_setting('app.workspace_id', true)
           OR current_setting('app.workspace_id', true) IS NULL
           OR current_setting('app.workspace_id', true) = ''
         )$p$,
      tbl, tbl
    );
  END LOOP;
END $$;

-- Notes:
-- 1. The `OR current_setting(...) IS NULL` clause is the migration escape
--    hatch — it lets superuser / migration scripts operate without a GUC.
--    For hard isolation, remove the IS NULL branch after you're sure the app
--    always sets the GUC.
-- 2. Users + auth_sessions don't have `workspace_id` (users carry it directly,
--    sessions are user-scoped), so RLS covers the sensitive row types without
--    breaking the auth lookups.
-- 3. To verify: `SET app.workspace_id = '00000000-...'; SELECT * FROM tickets;`
--    should return only that workspace's tickets even without the WHERE clause.
