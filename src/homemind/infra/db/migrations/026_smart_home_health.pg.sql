-- HomeMind schema v26: smart-home provider health and device identity.
--
-- Plan phase 5. Two gaps closed:
--
--   * Provider health was ``last_seen_at`` + free-text ``last_error``. The
--     manager wrote ``last_seen_at`` on failure too, so it could not answer
--     "when did this provider last actually work", and a caller could not tell
--     "needs re-auth" from "temporarily rate limited" without parsing prose.
--
--   * Entities had no device identity, so the several entities one physical
--     lamp exposes (``light.x`` plus its energy sensor) could not be shown as
--     one device. ``device_key`` carries the stable composite key derived from
--     the integration's own device id, so a rename cannot fork a new device.

ALTER TABLE homemind_family_smart_providers
  ADD COLUMN IF NOT EXISTS last_sync_at INTEGER;
ALTER TABLE homemind_family_smart_providers
  ADD COLUMN IF NOT EXISTS last_success_at INTEGER;
ALTER TABLE homemind_family_smart_providers
  ADD COLUMN IF NOT EXISTS last_error_code TEXT;
ALTER TABLE homemind_family_smart_providers
  ADD COLUMN IF NOT EXISTS reauth_required INTEGER NOT NULL DEFAULT 0;
ALTER TABLE homemind_family_smart_providers
  ADD COLUMN IF NOT EXISTS rate_limited_until INTEGER;

ALTER TABLE homemind_family_smart_entities
  ADD COLUMN IF NOT EXISTS device_key TEXT;
ALTER TABLE homemind_family_smart_entities
  ADD COLUMN IF NOT EXISTS capabilities_typed_json TEXT NOT NULL DEFAULT '[]';

-- Provider health is read on every dashboard poll.
CREATE INDEX IF NOT EXISTS idx_homemind_family_smart_providers_health
  ON homemind_family_smart_providers(family_id, reauth_required, rate_limited_until);

-- Grouping entities onto one physical device.
CREATE INDEX IF NOT EXISTS idx_homemind_family_smart_entities_device
  ON homemind_family_smart_entities(provider_id, device_key);

UPDATE _homemind_schema_version SET version = 26;