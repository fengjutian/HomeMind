-- HomeMind schema v7: FTS5 full-text search over family memories.
--
-- SQLite-only optimisation: a contentless FTS5 virtual table that
-- tokenizes memory content for fast LIKE-free search. Triggers keep
-- it in sync with the canonical ``homemind_family_memories`` table.
-- PostgreSQL deployments get the equivalent GIN expression index from
-- ``007_memory_fts5.pg.sql`` and ``FamilyContextRepo.search_memories_fts``
-- dispatches to the right backend so callers see the same
-- ``FamilyMemoryRow`` shape on both dialects.

CREATE VIRTUAL TABLE IF NOT EXISTS homemind_family_memory_fts USING fts5(
  memory_id UNINDEXED,
  family_id UNINDEXED,
  content,
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS homemind_family_memory_fts_ai
  AFTER INSERT ON homemind_family_memories
  BEGIN
    INSERT INTO homemind_family_memory_fts(memory_id, family_id, content)
    VALUES (new.memory_id, new.family_id, new.content);
  END;

CREATE TRIGGER IF NOT EXISTS homemind_family_memory_fts_ad
  AFTER DELETE ON homemind_family_memories
  BEGIN
    DELETE FROM homemind_family_memory_fts
    WHERE memory_id = old.memory_id;
  END;

CREATE TRIGGER IF NOT EXISTS homemind_family_memory_fts_au
  AFTER UPDATE OF content ON homemind_family_memories
  BEGIN
    DELETE FROM homemind_family_memory_fts
    WHERE memory_id = old.memory_id;
    INSERT INTO homemind_family_memory_fts(memory_id, family_id, content)
    VALUES (new.memory_id, new.family_id, new.content);
  END;

-- Backfill existing memories into the FTS table so search works on
-- databases that already have rows before this migration ran.
INSERT INTO homemind_family_memory_fts(memory_id, family_id, content)
SELECT memory_id, family_id, content FROM homemind_family_memories
WHERE NOT EXISTS (
  SELECT 1 FROM homemind_family_memory_fts fts
  WHERE fts.memory_id = homemind_family_memories.memory_id
);

UPDATE _homemind_schema_version SET version = 7;
