-- HomeMind schema v11: unified family search index + embedding metadata.
--
-- Stage 7 replaces "load everything and score in Python" with an index
-- that the database filters. Two things follow from that:
--
--   1. Permission predicates live in the SQL WHERE clause, so a
--      private-space row never even reaches the scoring layer.
--   2. Pagination becomes a cursor over ``(score, ts, kind, id)``
--      instead of an OFFSET, so page 2 cannot repeat or skip rows when
--      the underlying set changes between requests.
--
-- ``homemind_search_documents`` is one flat row per searchable entity
-- (member / event / memory / asset / album / object / scene /
-- location). FTS5 on SQLite and ``tsvector`` on PostgreSQL both index
-- the same ``text`` column, so the two dialects return the same
-- document set and only the ranking function differs.
--
-- The embedding columns live on the same row: ``embedding_model``,
-- ``embedding_dimensions`` and ``embedding_version`` must all match
-- before two vectors may be compared, so a model switch is detected
-- instead of silently producing nonsense similarities.

CREATE TABLE IF NOT EXISTS homemind_search_documents (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id       TEXT NOT NULL UNIQUE,
  family_id         TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  kind              TEXT NOT NULL,
  entity_id         TEXT NOT NULL,
  title             TEXT NOT NULL DEFAULT '',
  text              TEXT NOT NULL DEFAULT '',
  -- Denormalised permission columns. Keeping them on the index row is
  -- what lets the WHERE clause filter private content in SQL rather
  -- than after the fact.
  space_id          TEXT,
  owner_member_id   TEXT,
  visibility        TEXT NOT NULL DEFAULT 'FAMILY',
  status            TEXT NOT NULL DEFAULT 'ACTIVE',
  importance        DOUBLE PRECISION NOT NULL DEFAULT 0.5,
  captured_at       INTEGER,
  updated_at        INTEGER NOT NULL,
  -- Embedding provenance. A comparison is only valid when all three
  -- match the query vector's provenance.
  embedding_model        TEXT,
  embedding_dimensions   INTEGER,
  embedding_version      INTEGER,
  embedding_content_hash TEXT,
  embedding              BLOB
);

CREATE INDEX IF NOT EXISTS idx_homemind_search_docs_scope
  ON homemind_search_documents(family_id, kind, visibility, status);

CREATE INDEX IF NOT EXISTS idx_homemind_search_docs_entity
  ON homemind_search_documents(family_id, entity_id);

-- Cursor pagination walks (score desc, updated_at desc, kind, id); the
-- index supports the filter columns above.
CREATE INDEX IF NOT EXISTS idx_homemind_search_docs_recent
  ON homemind_search_documents(family_id, updated_at DESC, id DESC);

-- Only documents that actually carry a vector are candidates for
-- semantic comparison, so the provenance check stays cheap.
CREATE INDEX IF NOT EXISTS idx_homemind_search_docs_embedding
  ON homemind_search_documents(embedding_model, embedding_version, embedding_dimensions);

CREATE TABLE IF NOT EXISTS homemind_search_cursors (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  cursor_key    TEXT NOT NULL UNIQUE,
  family_id     TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  created_by    INTEGER NOT NULL,
  created_at    INTEGER NOT NULL,
  expires_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_search_cursors_expiry
  ON homemind_search_cursors(expires_at);

-- SQLite-only keyword index.
--
-- The tokenizer is ``trigram``, not ``unicode61``. ``unicode61`` treats a
-- run of CJK characters as a single token, so a Chinese substring query
-- never matches — "清淡" cannot find "妈妈喜欢清淡饮食". ``trigram``
-- indexes character triples and therefore handles Chinese, Japanese,
-- filenames, and free text the same way. Its one limitation is that a
-- query shorter than 3 characters cannot use the index; the repo falls
-- back to a LIKE scan for those, which is correct just slower.
--
-- PostgreSQL gets the equivalent ``pg_trgm`` GIN index in
-- ``011_search_index.pg.sql``.
CREATE VIRTUAL TABLE IF NOT EXISTS homemind_search_fts USING fts5(
  document_id UNINDEXED,
  family_id UNINDEXED,
  text,
  tokenize = 'trigram'
);

CREATE TRIGGER IF NOT EXISTS homemind_search_fts_ai
  AFTER INSERT ON homemind_search_documents
  BEGIN
    INSERT INTO homemind_search_fts(document_id, family_id, text)
    VALUES (new.document_id, new.family_id, new.text);
  END;

CREATE TRIGGER IF NOT EXISTS homemind_search_fts_ad
  AFTER DELETE ON homemind_search_documents
  BEGIN
    DELETE FROM homemind_search_fts WHERE document_id = old.document_id;
  END;

CREATE TRIGGER IF NOT EXISTS homemind_search_fts_au
  AFTER UPDATE OF text ON homemind_search_documents
  BEGIN
    DELETE FROM homemind_search_fts WHERE document_id = old.document_id;
    INSERT INTO homemind_search_fts(document_id, family_id, text)
    VALUES (new.document_id, new.family_id, new.text);
  END;

UPDATE _homemind_schema_version SET version = 11;
