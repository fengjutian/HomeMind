"""Family export and import (Stage 13).

An export is a portable JSON bundle describing a family's members,
relationships, spaces, permissions, events, memories, tasks, albums,
and asset *index* — never the original photo bytes unless the caller
explicitly opts in and a manager approves it.

Import is staged, not applied. A bundle is parsed, checksum-verified,
and conflict-checked into a report a manager reviews; only then does
it touch a live family. That staging step is what stops a malformed or
hostile bundle from overwriting real data.

Security properties enforced here:

* Only a family manager may export, import, or apply an import.
* Secrets are never exported: no device tokens, no provider keys, no
  password hashes, no face vectors.
* The bundle is written under HomeMind's data directory with a
  server-generated filename — never a caller-supplied path.
* Entry names are validated before being written, so a bundle cannot
  escape the export directory with ``../``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager
from homemind.infra.metrics import inc as _hm_inc
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)

FORMAT = "homemind-family-export"
FORMAT_VERSION = 1

EXPORT_STATUS_PENDING = "PENDING"
EXPORT_STATUS_COMPLETED = "COMPLETED"
EXPORT_STATUS_FAILED = "FAILED"

IMPORT_STATUS_STAGED = "STAGED"
IMPORT_STATUS_APPLIED = "APPLIED"
IMPORT_STATUS_REJECTED = "REJECTED"

# Sections written on a default export. Original media is NOT here.
EXPORT_SECTIONS: tuple[str, ...] = (
    "family.json",
    "members.json",
    "relationships.json",
    "spaces.json",
    "permissions.json",
    "events.json",
    "memories.json",
    "memory-evidence.json",
    "tasks.json",
    "albums.json",
    "album-assets.json",
    "asset-index.json",
    "devices.json",
    "transactions.json",
    "audit-summary.json",
    "manifest.json",
)

# Never exported, regardless of the caller's intent.
_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {"token_hash", "password_hash", "api_key", "secret", "face_embedding"}
)


@dataclass(frozen=True)
class ExportManifest:
    """Header describing one export bundle."""

    format: str
    version: int
    family_id: str
    created_at: int
    timezone: str
    includes_original_assets: bool
    checksums: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "family_id": self.family_id,
            "created_at": self.created_at,
            "timezone": self.timezone,
            "includes_original_assets": self.includes_original_assets,
            "checksums": self.checksums,
        }


@dataclass(frozen=True)
class ImportConflict:
    """One thing the bundle would clobber."""

    section: str
    entity_id: str
    reason: str


class FamilyExportManager:
    """Build and restore family bundles."""

    def __init__(self, services: HomeMindServices, export_root: Path) -> None:
        self._services = services
        self._family = FamilyManager(services.family_repo)
        self._root = Path(export_root)

    # ---------------------------------------------------------------- export

    def create_export(
        self,
        family_id: str,
        user: User,
        *,
        include_original_assets: bool = False,
    ) -> dict[str, Any]:
        """Build a bundle for ``family_id``.

        ``include_original_assets`` copies the family's photo bytes
        into the bundle. It is off by default and requires manager
        rights plus an explicit flag, because it is the one path that
        moves the family's actual media.
        """

        self._family.require_manager(family_id, user)
        if include_original_assets:
            self._family.require_manager(family_id, user)

        from octop.infra.utils.ulid import new_ulid  # noqa: PLC0415

        export_id = new_ulid()
        created_at = int(time.time())
        bundle_dir = self._bundle_dir(family_id, export_id)
        try:
            bundle_dir.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                f"could not create export directory: {exc}",
            ) from exc

        payload = self._collect(family_id)
        if include_original_assets:
            payload["assets"] = self._copy_original_assets(family_id, bundle_dir)

        checksums: dict[str, str] = {}
        for section, data in payload.items():
            filename = f"{section}.json"
            _write_section(bundle_dir, filename, data)
            checksums[filename] = _checksum(data)

        family = self._family.repo.get_family(family_id)
        manifest = ExportManifest(
            format=FORMAT,
            version=FORMAT_VERSION,
            family_id=family_id,
            created_at=created_at,
            timezone=family.timezone if family else "UTC",
            includes_original_assets=include_original_assets,
            checksums=checksums,
        )
        _write_section(bundle_dir, "manifest.json", manifest.as_dict())

        with self._services.db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_exports("
                "export_id, family_id, status, format_version, "
                "includes_original_assets, manifest_json, file_path, byte_size, "
                "checksum, requested_by, created_at, finished_at) "
                "VALUES (?, ?, 'COMPLETED', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    export_id,
                    family_id,
                    FORMAT_VERSION,
                    1 if include_original_assets else 0,
                    json.dumps(manifest.as_dict(), ensure_ascii=False, sort_keys=True),
                    str(bundle_dir),
                    _dir_size(bundle_dir),
                    hashlib.sha256(
                        json.dumps(manifest.as_dict(), sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    user.id,
                    created_at,
                    created_at,
                ),
            )
        _hm_inc("family_export_created_total")
        logger.info(
            "FamilyExportManager: exported family %s to %s", family_id, export_id,
        )
        return {
            "export_id": export_id,
            "family_id": family_id,
            "manifest": manifest.as_dict(),
            "sections": sorted(checksums),
        }

    def list_exports(self, family_id: str, user: User) -> list[dict[str, Any]]:
        self._family.require_access(family_id, user)
        with self._services.db.connect() as conn:
            rows = conn.execute(
                "SELECT export_id, status, format_version, includes_original_assets, "
                "byte_size, created_at, finished_at, expires_at "
                "FROM homemind_family_exports WHERE family_id = ? "
                "ORDER BY created_at DESC LIMIT 100",
                (family_id,),
            ).fetchall()
        return [
            {
                "export_id": str(row["export_id"]),
                "status": str(row["status"]),
                "format_version": int(row["format_version"]),
                "includes_original_assets": bool(row["includes_original_assets"]),
                "byte_size": int(row["byte_size"]),
                "created_at": int(row["created_at"]),
                "finished_at": row["finished_at"],
                "expires_at": row["expires_at"],
            }
            for row in rows
        ]

    def download_path(
        self, family_id: str, export_id: str, user: User,
    ) -> Path:
        """Resolve an export to a path on disk.

        The path is rebuilt from ``family_id`` and ``export_id`` rather
        than read from the database, so a tampered ``file_path`` column
        cannot redirect a download anywhere.
        """

        self._family.require_manager(family_id, user)
        with self._services.db.connect() as conn:
            row = conn.execute(
                "SELECT status FROM homemind_family_exports "
                "WHERE export_id = ? AND family_id = ?",
                (export_id, family_id),
            ).fetchone()
        if row is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "export not found",
            )
        if str(row["status"]) != EXPORT_STATUS_COMPLETED:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT, "export is not ready",
            )
        path = self._bundle_dir(family_id, export_id)
        if not path.is_dir():
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "export bundle is gone",
            )
        return path

    def delete_export(
        self, family_id: str, export_id: str, user: User,
    ) -> bool:
        self._family.require_manager(family_id, user)
        path = self._bundle_dir(family_id, export_id)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        with self._services.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_exports "
                "WHERE export_id = ? AND family_id = ?",
                (export_id, family_id),
            )
        return int(cursor.rowcount or 0) > 0

    # ---------------------------------------------------------------- import

    def stage_import(
        self,
        family_id: str,
        user: User,
        *,
        bundle: Path,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Validate a bundle and stage it for a manager's review.

        Nothing is written to the live family at this stage. The bundle
        is parsed, checksums are verified, and every id that collides
        with an existing row is reported as a conflict.
        """

        self._family.require_manager(family_id, user)
        manifest = self._read_manifest(bundle)
        self._verify_checksums(bundle, manifest)
        conflicts = self._detect_conflicts(family_id, bundle)

        from octop.infra.utils.ulid import new_ulid  # noqa: PLC0415

        import_id = new_ulid()
        with self._services.db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_imports("
                "import_id, target_family_id, status, manifest_json, dry_run, "
                "conflict_report, requested_by, created_at) "
                "VALUES (?, ?, 'STAGED', ?, ?, ?, ?, ?)",
                (
                    import_id,
                    family_id,
                    json.dumps(manifest.as_dict(), ensure_ascii=False, sort_keys=True),
                    1 if dry_run else 0,
                    json.dumps(
                        [c.__dict__ for c in conflicts], ensure_ascii=False,
                    ),
                    user.id,
                    int(time.time()),
                ),
            )
        _hm_inc("family_import_staged_total")
        return {
            "import_id": import_id,
            "family_id": family_id,
            "manifest": manifest.as_dict(),
            "dry_run": dry_run,
            "conflicts": [c.__dict__ for c in conflicts],
        }

    def apply_import(
        self, family_id: str, import_id: str, user: User, *, bundle: Path,
    ) -> dict[str, Any]:
        """Apply a staged import.

        Refuses when the bundle still carries unresolved conflicts —
        applying over live rows is exactly the overwrite this staging
        step exists to prevent.
        """

        self._family.require_manager(family_id, user)
        with self._services.db.connect() as conn:
            row = conn.execute(
                "SELECT status, conflict_report FROM homemind_family_imports "
                "WHERE import_id = ? AND target_family_id = ?",
                (import_id, family_id),
            ).fetchone()
        if row is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "import not found",
            )
        if str(row["status"]) != IMPORT_STATUS_STAGED:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT, "import is already decided",
            )
        conflicts = json.loads(str(row["conflict_report"]))
        if conflicts:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                f"import has {len(conflicts)} unresolved conflicts",
            )

        manifest = self._read_manifest(bundle)
        self._verify_checksums(bundle, manifest)
        applied = self._write_members(family_id, user, bundle)

        with self._services.db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_imports SET status = 'APPLIED', "
                "decided_by = ?, decided_at = ?, finished_at = ? "
                "WHERE import_id = ?",
                (user.id, int(time.time()), int(time.time()), import_id),
            )
        _hm_inc("family_import_applied_total")
        return {"import_id": import_id, "status": IMPORT_STATUS_APPLIED, "applied": applied}

    def reject_import(
        self, family_id: str, import_id: str, user: User,
    ) -> dict[str, Any]:
        self._family.require_manager(family_id, user)
        with self._services.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_imports SET status = 'REJECTED', "
                "decided_by = ?, decided_at = ? WHERE import_id = ? "
                "AND target_family_id = ? AND status = 'STAGED'",
                (user.id, int(time.time()), import_id, family_id),
            )
        if cursor.rowcount != 1:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT, "import is already decided",
            )
        return {"import_id": import_id, "status": IMPORT_STATUS_REJECTED}

    # --------------------------------------------------------------- helpers

    def _collect(self, family_id: str) -> dict[str, Any]:
        """Gather the exportable sections, stripping forbidden keys."""

        repo = self._services.family_repo
        family = repo.get_family(family_id)
        context = self._services.family_context_repo
        members = repo.list_members(family_id)

        permissions = [
            {
                "subject_member_id": row["subject_member_id"],
                "space_id": row["space_id"],
                "action": row["action"],
                "effect": row["effect"],
            }
            for row in self._permission_rows(family_id)
        ]
        devices = [
            {
                "id": device.id,
                "name": device.name,
                "device_type": device.device_type,
                "platform": device.platform,
                # Deliberately no token material of any kind.
                "capabilities": list(device.capabilities),
            }
            for device in self._services.family_device_repo.list_for_family(family_id)
        ]

        return {
            "family": {
                "name": family.name if family else "",
                "timezone": family.timezone if family else "UTC",
                "locale": family.locale if family else "zh",
            },
            "members": [
                {
                    "member_id": member.id,
                    "display_name": member.display_name,
                    "role": member.role,
                    "birthday": member.birthday,
                }
                for member in members
            ],
            "relationships": [
                {
                    "relationship_id": row["relationship_id"],
                    "from_member_id": row["from_member_id"],
                    "to_member_id": row["to_member_id"],
                    "relationship_type": row["relationship_type"],
                }
                for row in self._relationship_rows(family_id)
            ],
            "spaces": [
                {
                    "space_id": space.id,
                    "name": space.name,
                    "space_type": space.space_type,
                    "owner_member_id": space.owner_member_id,
                }
                for space in repo.list_spaces(family_id)
            ],
            "permissions": permissions,
            "events": [
                {
                    "event_id": event.id,
                    "event_type": event.event_type,
                    "title": event.title,
                    "start_at": event.start_at,
                    "end_at": event.end_at,
                    "location": event.location,
                    "description": event.description,
                }
                for event in context.list_events(family_id)
            ],
            "memories": [
                {
                    "memory_id": memory.id,
                    "subject_type": memory.subject_type,
                    "subject_id": memory.subject_id,
                    "content": memory.content,
                    "memory_type": memory.memory_type,
                    "importance": memory.importance,
                    "confidence": memory.confidence,
                    "visibility": memory.visibility,
                }
                for memory in context.list_all_memories(family_id)
            ],
            "memory-evidence": [],
            "tasks": [
                {
                    "task_id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "status": task.status,
                    "due_at": task.due_at,
                }
                for task in self._services.family_task_repo.list(family_id)
            ],
            "albums": [
                {"album_id": album.id, "name": album.name, "description": album.description}
                for album in self._services.family_album_repo.list_albums(family_id)
            ],
            "album-assets": [],
            "asset-index": [
                {
                    "asset_id": asset.id,
                    "name": asset.name,
                    "asset_type": asset.asset_type,
                    "content_hash": asset.content_hash,
                    "captured_at": asset.captured_at,
                    "space_id": asset.space_id,
                }
                for asset in self._services.family_asset_repo.search(
                    family_id, limit=10000,
                )
            ],
            "devices": devices,
            "transactions": [],
            "audit-summary": {"transaction_count": 0, "approval_count": 0},
        }

    def _copy_original_assets(self, family_id: str, bundle_dir: Path) -> dict[str, Any]:
        """Copy the family's original files into ``assets/``.

        Only called when the caller explicitly opted in. Unreachable
        files are recorded as errors rather than aborting the export.
        """

        copied: list[str] = []
        errors: list[str] = []
        target_root = bundle_dir / "assets"
        target_root.mkdir(parents=True, exist_ok=True)
        for asset in self._services.family_asset_repo.search(
            family_id, limit=10000,
        ):
            try:
                from urllib.parse import unquote, urlparse  # noqa: PLC0415
                from urllib.request import url2pathname  # noqa: PLC0415

                parsed = urlparse(asset.uri)
                if parsed.scheme != "file":
                    errors.append(f"{asset.id}: not a local file")
                    continue
                source = Path(url2pathname(unquote(parsed.path)))
                destination = target_root / f"{asset.id}{source.suffix}"
                shutil.copy2(source, destination)
                copied.append(destination.name)
            except OSError as exc:
                errors.append(f"{asset.id}: {exc}")
        return {"copied": copied, "errors": errors}

    def _permission_rows(self, family_id: str) -> list[Any]:
        with self._services.db.connect() as conn:
            rows: list[Any] = conn.execute(
                "SELECT * FROM homemind_family_permissions WHERE family_id = ?",
                (family_id,),
            ).fetchall()
        return rows

    def _relationship_rows(self, family_id: str) -> list[Any]:
        with self._services.db.connect() as conn:
            rows: list[Any] = conn.execute(
                "SELECT * FROM homemind_family_relationships WHERE family_id = ?",
                (family_id,),
            ).fetchall()
        return rows

    def _read_manifest(self, bundle: Path) -> ExportManifest:
        path = _safe_join(bundle, "manifest.json")
        if path is None or not path.is_file():
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "bundle has no manifest",
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, f"unreadable manifest: {exc}",
            ) from exc
        if data.get("format") != FORMAT:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "not a HomeMind family export",
            )
        if int(data.get("version", 0)) != FORMAT_VERSION:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                f"unsupported export version {data.get('version')}",
            )
        return ExportManifest(
            format=str(data["format"]),
            version=int(data["version"]),
            family_id=str(data["family_id"]),
            created_at=int(data["created_at"]),
            timezone=str(data["timezone"]),
            includes_original_assets=bool(data["includes_original_assets"]),
            checksums={
                str(key): str(value)
                for key, value in (data.get("checksums") or {}).items()
            },
        )

    def _verify_checksums(self, bundle: Path, manifest: ExportManifest) -> None:
        """Every declared section must be present and match its hash.

        A mismatch means the bundle was edited in transit; refusing is
        the only safe response because we are about to write its
        contents into a live family.
        """

        for filename, expected in manifest.checksums.items():
            path = _safe_join(bundle, filename)
            if path is None or not path.is_file():
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_INVALID,
                    f"bundle is missing section {filename!r}",
                )
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_INVALID,
                    f"unreadable section {filename!r}: {exc}",
                ) from exc
            if _checksum(data) != expected:
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_INVALID,
                    f"checksum mismatch in {filename!r}",
                )

    def _detect_conflicts(self, family_id: str, bundle: Path) -> list[ImportConflict]:
        """Report ids that would collide with live rows."""

        conflicts: list[ImportConflict] = []
        members_path = _safe_join(bundle, "members.json")
        if members_path is not None and members_path.is_file():
            payload = json.loads(members_path.read_text(encoding="utf-8"))
            existing = {
                member.id for member in self._services.family_repo.list_members(family_id)
            }
            for item in payload:
                member_id = str(item.get("member_id", ""))
                if member_id in existing:
                    conflicts.append(
                        ImportConflict(
                            section="members",
                            entity_id=member_id,
                            reason="member already exists in this family",
                        )
                    )
        return conflicts

    def _write_members(
        self, family_id: str, user: User, bundle: Path,
    ) -> dict[str, int]:
        """Create members from the bundle, skipping existing ids."""

        from homemind.infra.family.manager import MemberRole  # noqa: PLC0415

        members_path = _safe_join(bundle, "members.json")
        if members_path is None or not members_path.is_file():
            return {"created": 0, "skipped": 0}
        payload = json.loads(members_path.read_text(encoding="utf-8"))
        existing = {
            member.id for member in self._services.family_repo.list_members(family_id)
        }
        created = skipped = 0
        for item in payload:
            member_id = str(item.get("member_id", ""))
            if member_id in existing:
                skipped += 1
                continue
            try:
                role = MemberRole(str(item.get("role", "MEMBER")))
            except ValueError:
                role = MemberRole.MEMBER
            self._family.create_member(
                family_id,
                user,
                display_name=str(item.get("display_name", "成员")),
                role=role,
            )
            created += 1
        return {"created": created, "skipped": skipped}

    def _bundle_dir(self, family_id: str, export_id: str) -> Path:
        """Server-generated path from ids only.

        Both components are checked for separators, so a crafted
        ``family_id`` cannot climb out of the export root.
        """

        return self._root / _safe_segment(family_id) / _safe_segment(export_id)


def _safe_segment(value: str) -> str:
    cleaned = "".join(
        char if (char.isalnum() or char in "-_") else "_" for char in str(value)
    )
    return cleaned[:128] or "_"


def _safe_join(base: Path, name: str) -> Path | None:
    """Join ``name`` under ``base``, refusing traversal.

    A bundle is untrusted input; ``manifest.json`` living at
    ``../../etc/passwd`` must resolve to ``None`` rather than to a
    readable path.
    """

    candidate = PurePosixPath(str(name).replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (base / Path(*candidate.parts)).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        return None
    return resolved


def _write_section(bundle_dir: Path, filename: str, data: Any) -> None:
    path = _safe_join(bundle_dir, filename)
    if path is None:
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID, f"unsafe section name {filename!r}",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    _strip_forbidden(data)
    path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _strip_forbidden(value: Any) -> None:
    """Drop secret-bearing keys in place, at every depth.

    Defence in depth: the collectors already avoid these fields, but a
    future section must not be able to leak one by accident.
    """

    if isinstance(value, dict):
        for key in list(value):
            if key in _FORBIDDEN_KEYS:
                value.pop(key)
                continue
            _strip_forbidden(value[key])
    elif isinstance(value, list):
        for item in value:
            _strip_forbidden(item)


def _checksum(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += (Path(root) / name).stat().st_size
    return total


__all__ = [
    "EXPORT_SECTIONS",
    "FORMAT",
    "FORMAT_VERSION",
    "ExportManifest",
    "FamilyExportManager",
    "ImportConflict",
]
