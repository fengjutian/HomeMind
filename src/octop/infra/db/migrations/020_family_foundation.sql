-- Schema v20: HomeMind family foundation.

CREATE TABLE IF NOT EXISTS families (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  family_id      TEXT NOT NULL UNIQUE,
  name           TEXT NOT NULL,
  avatar         TEXT,
  owner_user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  timezone       TEXT NOT NULL,
  locale         TEXT NOT NULL,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_families_owner ON families(owner_user_id);

CREATE TABLE IF NOT EXISTS family_members (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  member_id      TEXT NOT NULL UNIQUE,
  family_id      TEXT NOT NULL REFERENCES families(family_id) ON DELETE CASCADE,
  user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
  display_name   TEXT NOT NULL,
  role           TEXT NOT NULL DEFAULT 'MEMBER',
  avatar         TEXT,
  birthday       TEXT,
  status         TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL,
  UNIQUE(family_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_family_members_family ON family_members(family_id);

CREATE TABLE IF NOT EXISTS family_memberships (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  membership_id  TEXT NOT NULL UNIQUE,
  family_id      TEXT NOT NULL REFERENCES families(family_id) ON DELETE CASCADE,
  member_id      TEXT NOT NULL UNIQUE REFERENCES family_members(member_id) ON DELETE CASCADE,
  user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role           TEXT NOT NULL DEFAULT 'MEMBER',
  status         TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL,
  UNIQUE(family_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_family_memberships_user ON family_memberships(user_id);

CREATE TABLE IF NOT EXISTS family_relationships (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  relationship_id    TEXT NOT NULL UNIQUE,
  family_id          TEXT NOT NULL REFERENCES families(family_id) ON DELETE CASCADE,
  from_member_id     TEXT NOT NULL REFERENCES family_members(member_id) ON DELETE CASCADE,
  to_member_id       TEXT NOT NULL REFERENCES family_members(member_id) ON DELETE CASCADE,
  relationship_type  TEXT NOT NULL,
  created_at         INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL,
  CHECK(from_member_id <> to_member_id),
  UNIQUE(family_id, from_member_id, to_member_id, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_family_relationships_family ON family_relationships(family_id);

CREATE TABLE IF NOT EXISTS family_spaces (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  space_id    TEXT NOT NULL UNIQUE,
  family_id   TEXT NOT NULL REFERENCES families(family_id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  space_type  TEXT NOT NULL,
  owner_member_id TEXT REFERENCES family_members(member_id) ON DELETE SET NULL,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  UNIQUE(family_id, name)
);

CREATE INDEX IF NOT EXISTS idx_family_spaces_family ON family_spaces(family_id);

CREATE TABLE IF NOT EXISTS family_permissions (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  permission_id     TEXT NOT NULL UNIQUE,
  family_id         TEXT NOT NULL REFERENCES families(family_id) ON DELETE CASCADE,
  subject_member_id TEXT REFERENCES family_members(member_id) ON DELETE CASCADE,
  space_id          TEXT REFERENCES family_spaces(space_id) ON DELETE CASCADE,
  action            TEXT NOT NULL,
  effect            TEXT NOT NULL,
  expires_at        INTEGER,
  created_by        INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  created_at        INTEGER NOT NULL,
  updated_at        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_family_permissions_family ON family_permissions(family_id);
CREATE INDEX IF NOT EXISTS idx_family_permissions_subject ON family_permissions(subject_member_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_family_permissions_rule
  ON family_permissions(
    family_id,
    COALESCE(subject_member_id, ''),
    COALESCE(space_id, ''),
    action
  );

CREATE TABLE IF NOT EXISTS family_asset_sources (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id       TEXT NOT NULL UNIQUE,
  family_id       TEXT NOT NULL REFERENCES families(family_id) ON DELETE CASCADE,
  space_id        TEXT REFERENCES family_spaces(space_id) ON DELETE SET NULL,
  directory_uri   TEXT NOT NULL,
  recursive       INTEGER NOT NULL DEFAULT 1,
  visibility      TEXT NOT NULL DEFAULT 'FAMILY',
  status          TEXT NOT NULL DEFAULT 'ACTIVE',
  last_scanned_at INTEGER,
  created_by      INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL,
  UNIQUE(family_id, directory_uri)
);

CREATE INDEX IF NOT EXISTS idx_family_asset_sources_family
  ON family_asset_sources(family_id);

CREATE TABLE IF NOT EXISTS family_assets (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id      TEXT NOT NULL UNIQUE,
  family_id     TEXT NOT NULL REFERENCES families(family_id) ON DELETE CASCADE,
  source_id     TEXT REFERENCES family_asset_sources(source_id) ON DELETE SET NULL,
  space_id      TEXT REFERENCES family_spaces(space_id) ON DELETE SET NULL,
  asset_type    TEXT NOT NULL,
  name          TEXT NOT NULL,
  uri           TEXT NOT NULL,
  mime_type     TEXT NOT NULL,
  size_bytes    INTEGER NOT NULL,
  content_hash  TEXT NOT NULL,
  captured_at   INTEGER,
  indexed_at    INTEGER NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_by    INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  visibility    TEXT NOT NULL DEFAULT 'FAMILY',
  status        TEXT NOT NULL DEFAULT 'INDEXED',
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL,
  UNIQUE(family_id, uri)
);

CREATE INDEX IF NOT EXISTS idx_family_assets_family ON family_assets(family_id);
CREATE INDEX IF NOT EXISTS idx_family_assets_hash ON family_assets(family_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_family_assets_captured ON family_assets(family_id, captured_at);

CREATE TABLE IF NOT EXISTS family_photo_metadata (
  asset_id     TEXT PRIMARY KEY REFERENCES family_assets(asset_id) ON DELETE CASCADE,
  width        INTEGER,
  height       INTEGER,
  camera_make  TEXT,
  camera_model TEXT,
  latitude     REAL,
  longitude    REAL,
  taken_at     INTEGER
);

UPDATE _schema_version SET version = 20;
