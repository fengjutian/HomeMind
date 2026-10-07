-- HomeMind schema v21: a per-family switch for external OCR.
--
-- Local recognition needs no permission: the pages never leave the
-- machine. Sending them to a third party does, and that decision has to
-- be the family's rather than a global default. The column defaults to
-- OFF so a deployment that upgrades cannot start shipping documents
-- outward because a new code path appeared.
--
-- ``homemind_family_external_requests`` already records every
-- authorised outbound call, so an allowed OCR run lands in the same
-- audit trail as a vision or embedding call.

ALTER TABLE homemind_family_privacy_settings
  ADD COLUMN allow_external_ocr INTEGER NOT NULL DEFAULT 0;

UPDATE _homemind_schema_version SET version = 21;