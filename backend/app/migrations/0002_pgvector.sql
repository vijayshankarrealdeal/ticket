-- Opt-in pgvector promotion (spec §7, §9).
--
-- Run manually once the pgvector extension is installed on the target database
-- (usually requires superuser). The embedding columns are `bytea` by default
-- so `/tickets/similar` + `kb_search` still rank sensibly on a stock Postgres;
-- this migration promotes them to real `vector(1536)` with an HNSW index for
-- production-scale cosine search.
--
-- The `embedded_at` columns are preserved so the indexer can tell whether a
-- promoted row needs re-embedding through the new column type.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- Null out existing sketch bytes — they're float32-packed hashes, not
-- 1536-dim vectors; the embedding indexer will repopulate.
UPDATE kb_chunks SET embedded_at = NULL WHERE embedding IS NOT NULL;
UPDATE tickets   SET embedded_at = NULL WHERE subject_embedding IS NOT NULL;

ALTER TABLE kb_chunks
  DROP COLUMN IF EXISTS embedding,
  ADD  COLUMN IF NOT EXISTS embedding vector(1536);
ALTER TABLE tickets
  DROP COLUMN IF EXISTS subject_embedding,
  ADD  COLUMN IF NOT EXISTS subject_embedding vector(1536);

-- HNSW cosine index — covers the orchestrator's `kb_search` + `similar_tickets`
-- tools at the scale the spec calls for (100 k chunks, 1 M tickets).
CREATE INDEX IF NOT EXISTS kb_chunks_embedding_hnsw_idx
  ON kb_chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS tickets_subject_embedding_hnsw_idx
  ON tickets USING hnsw (subject_embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
