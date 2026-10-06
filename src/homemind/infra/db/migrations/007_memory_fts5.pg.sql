-- HomeMind schema v7: full-text search over family memories (PostgreSQL).
--
-- SQLite ships a contentless FTS5 virtual table plus triggers in
-- ``007_memory_fts5.sql``; PostgreSQL has no FTS5, so this migration
-- builds an expression-based GIN index on ``content`` instead.
--
-- The ``simple`` text-search configuration keeps every CJK character
-- as its own token without installing extra dictionaries (such as
-- ``zhparser`` or ``ngram``), which gives reasonable cross-language
-- coverage out of the box. Search uses ``websearch_to_tsquery`` (which
-- accepts user-friendly operators such as quoted phrases and ``OR``)
-- and is ranked by ``ts_rank``; ``FamilyContextRepo.search_memories_fts``
-- dispatches to this query on PostgreSQL and the FTS5 path on SQLite,
-- so callers see the same ``FamilyMemoryRow`` shape on both dialects.

CREATE INDEX IF NOT EXISTS idx_homemind_family_memories_search
  ON homemind_family_memories
  USING GIN (
    to_tsvector('simple', coalesce(content, ''))
  );

UPDATE _homemind_schema_version SET version = 7;
