-- HomeMind schema v17: parsed family documents and their chunks
-- (PostgreSQL).
--
-- Mirror of the SQLite migration. Documents and memories stay separate
-- on purpose: a document is cited, a memory is asserted.

CREATE TABLE IF NOT EXISTS homemind_knowledge_documents (
  id              BIGSERIAL PRIMARY KEY,
  document_id     TEXT NOT NULL UNIQUE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  asset_id        TEXT NOT NULL REFERENCES homemind_family_assets(asset_id) ON DELETE CASCADE,
  content_hash    TEXT NOT NULL,
  parser_version  TEXT NOT NULL,
  mime_type       TEXT,
  name            TEXT,
  status          TEXT NOT NULL DEFAULT 'INDEXED',
  error           TEXT,
  chunk_count     INTEGER NOT NULL DEFAULT 0,
  indexed_at      BIGINT NOT NULL,
  created_at      BIGINT NOT NULL,
  updated_at      BIGINT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_knowledge_documents_asset
  ON homemind_knowledge_documents(asset_id);

CREATE INDEX IF NOT EXISTS idx_homemind_knowledge_documents_family
  ON homemind_knowledge_documents(family_id, status, indexed_at);

CREATE TABLE IF NOT EXISTS homemind_knowledge_chunks (
  id              BIGSERIAL PRIMARY KEY,
  chunk_id        TEXT NOT NULL UNIQUE,
  document_id     TEXT NOT NULL REFERENCES homemind_knowledge_documents(document_id) ON DELETE CASCADE,
  family_id       TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  asset_id        TEXT NOT NULL,
  ordinal         INTEGER NOT NULL,
  text            TEXT NOT NULL,
  locator         TEXT,
  page_number     INTEGER,
  content_hash    TEXT NOT NULL,
  embedding       TEXT,
  embedding_model TEXT,
  embedding_dimensions INTEGER,
  embedding_version INTEGER,
  created_at      BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_knowledge_chunks_document
  ON homemind_knowledge_chunks(document_id, ordinal);

CREATE INDEX IF NOT EXISTS idx_homemind_knowledge_chunks_family
  ON homemind_knowledge_chunks(family_id);

UPDATE _homemind_schema_version SET version = 17;