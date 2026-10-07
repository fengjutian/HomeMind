-- HomeMind schema v15: record the runtime version a device reports.
--
-- Stage 5 asks the device panel to show which runtime build is talking
-- to the server. A device stuck on an old runtime is the first thing
-- to check when a command misbehaves, so the value is stored on the
-- device rather than inferred from the platform string.

ALTER TABLE homemind_family_devices ADD COLUMN runtime_version TEXT;

UPDATE _homemind_schema_version SET version = 15;
