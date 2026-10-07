-- HomeMind schema v15: record the runtime version a device reports, and
-- persist the non-sensitive configuration a persistent asset job needs.
--
-- Stage 5 asks the device panel to show which runtime build is talking
-- to the server. A device stuck on an old runtime is the first thing
-- to check when a command misbehaves, so the value is stored on the
-- device rather than inferred from the platform string.
--
-- ``config_json`` is deliberately a separate column from ``cursor_json``:
-- the cursor is worker-owned scan progress that changes every batch, while
-- the config is the immutable, requester-supplied job description
-- (provider ids, model names, thumbnail size) that a worker must be able to
-- read back after a restart. Mixing them would mean every progress write
-- rewrites the job's identity, and a worker could not tell "where am I" from
-- "what am I doing". It holds public provider / model identifiers only —
-- never an API key, which is always read live from the Octop provider repo.

ALTER TABLE homemind_family_devices ADD COLUMN runtime_version TEXT;

ALTER TABLE homemind_asset_jobs ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}';

UPDATE _homemind_schema_version SET version = 15;
