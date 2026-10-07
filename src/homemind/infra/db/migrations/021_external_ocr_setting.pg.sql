-- HomeMind schema v21: a per-family switch for external OCR
-- (PostgreSQL).
--
-- Mirror of the SQLite migration. Defaults to OFF so upgrading cannot
-- start shipping documents outward.

ALTER TABLE homemind_family_privacy_settings
  ADD COLUMN allow_external_ocr INTEGER NOT NULL DEFAULT 0;

UPDATE _homemind_schema_version SET version = 21;