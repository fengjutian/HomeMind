-- HomeMind schema v17: parsed family documents and their chunks.
--
-- A household's own files -- a school notice, a warranty, a recipe typed
-- into a note -- are worth searching, but they are *documents*, not
-- facts. Keeping them in a separate structure from `homemind_family_
-- memories` is the point: an agent that reads a document must cite it,
-- and must never silently promote its contents into a family memory.
--
-- Three tables:
--   * ``..._documents`` — one row per source file, recording which parser
--                          and which version read it, so a parser upgrade
--                          can invalidate stale rows deliberately;
--   * ``..._chunks``    — the retrievable units, each carrying a locator
--                          (page / paragraph) so a search hit can point
--                          back into the original;
--   * embedding provenance lives on the chunk, never on the document, so
--                          re-chunking cannot mix vector sizes.

CREATE TABLE IF NOT EXISTS homemind_knowledge_documents (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
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
  indexed_at      INTEGER NOT NULL,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);

-- One document per asset: re-indexing replaces the row rather than
-- accumulating a second copy of the same file.
CREATE UNIQUE INDEX IF NOT EXISTS uq_homemind_knowledge_documents_asset
  ON homemind_knowledge_documents(asset_id);

CREATE INDEX IF NOT EXISTS idx_homemind_knowledge_documents_family
  ON homemind_knowledge_documents(family_id, status, indexed_at);

CREATE TABLE IF NOT EXISTS homemind_knowledge_chunks (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
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
  created_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_knowledge_chunks_document
  ON homemind_knowledge_chunks(document_id, ordinal);

CREATE INDEX IF NOT EXISTS idx_homemind_knowledge_chunks_family
  ON homemind_knowledge_chunks(family_id);

UPDATE _homemind_schema_version SET version = 17;