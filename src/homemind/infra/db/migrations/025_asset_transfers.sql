-- HomeMind schema v25: asynchronous device asset transfers.
--
-- A device never downloads a family asset through the logged-in user
-- route. It authenticates with its long-lived device credential on the
-- control plane, which authorizes one transfer and mints a *short-lived*
-- data-plane credential; only that credential may read bytes. Keeping the
-- two planes apart is what lets a leaked download URL expire in minutes
-- while the device credential survives.
--
-- One transfer is pinned to exactly one (family, asset, device) triple,
-- so revoking the device, deleting the asset, or cancelling the family
-- can reach it without a scan. The row also snapshots the resource
-- *version* -- size, mtime and content hash -- because a resumed
-- download is only meaningful against the exact bytes it started on.
--
-- ``request_key`` is the device-generated idempotency key. UNIQUE on
-- (device_id, request_key) turns a retried POST into the same transfer
-- instead of a second 5 GiB job.

CREATE TABLE IF NOT EXISTS homemind_asset_transfers (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  transfer_id      TEXT NOT NULL UNIQUE,
  family_id        TEXT NOT NULL REFERENCES homemind_families(family_id) ON DELETE CASCADE,
  asset_id         TEXT NOT NULL REFERENCES homemind_family_assets(asset_id) ON DELETE CASCADE,
  device_id        TEXT NOT NULL REFERENCES homemind_family_devices(device_id) ON DELETE CASCADE,
  request_key      TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'PENDING',
  source_kind      TEXT NOT NULL DEFAULT 'LOCAL_FILE',
  size_bytes       BIGINT NOT NULL,
  sha256           TEXT NOT NULL,
  source_mtime_ns  BIGINT,
  etag             TEXT NOT NULL,
  chunk_size       INTEGER NOT NULL,
  bytes_reported   BIGINT NOT NULL DEFAULT 0,
  last_progress_at BIGINT,
  expires_at       BIGINT NOT NULL,
  completed_at     BIGINT,
  failure_code     TEXT,
  failure_detail   TEXT,
  created_at       BIGINT NOT NULL,
  updated_at       BIGINT NOT NULL,
  UNIQUE(device_id, request_key),
  -- Progress is an aggregate the device reports; a server bug that
  -- double-counted would otherwise let a transfer report more bytes than
  -- the file even holds.
  CHECK (bytes_reported >= 0 AND bytes_reported <= size_bytes)
);

-- "what can this device resume?" -- the create/get/list query shape.
CREATE INDEX IF NOT EXISTS idx_homemind_asset_transfers_device
  ON homemind_asset_transfers(device_id, status, created_at);

-- "cancel everything for this asset" -- runs when the index row goes away.
CREATE INDEX IF NOT EXISTS idx_homemind_asset_transfers_asset
  ON homemind_asset_transfers(family_id, asset_id, status);

-- Periodic expiry sweep reads this and nothing else.
CREATE INDEX IF NOT EXISTS idx_homemind_asset_transfers_expiry
  ON homemind_asset_transfers(status, expires_at);

-- Short-lived data-plane credentials. Only the hash lands here; the
-- plaintext is returned once in the create/refresh response and never
-- stored, logged, or persisted into a command payload.
CREATE TABLE IF NOT EXISTS homemind_asset_transfer_tokens (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  token_id     TEXT NOT NULL UNIQUE,
  transfer_id  TEXT NOT NULL REFERENCES homemind_asset_transfers(transfer_id) ON DELETE CASCADE,
  token_hash   TEXT NOT NULL,
  expires_at   BIGINT NOT NULL,
  revoked_at   BIGINT,
  created_at   BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_homemind_asset_transfer_tokens_transfer
  ON homemind_asset_transfer_tokens(transfer_id, revoked_at, expires_at);

-- The credential lookup is a hash scan settled in constant time in the
-- domain layer, so this index only narrows the candidate set.
CREATE INDEX IF NOT EXISTS idx_homemind_asset_transfer_tokens_hash
  ON homemind_asset_transfer_tokens(token_hash);

UPDATE _homemind_schema_version SET version = 25;