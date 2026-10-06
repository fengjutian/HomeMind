"""Boundary-safe filesystem operations for registered family asset sources."""

from __future__ import annotations

import builtins
import fnmatch
import hashlib
import json
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePath
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from homemind.infra.db.repos.family_assets import FamilyAssetRepo, FamilyAssetSourceRow
from homemind.infra.db.repos.family_transactions import FamilyTransactionRepo
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.permissions import (
    FamilyPermissionEvaluator,
    PermissionEffect,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User


class FilesystemRisk(StrEnum):
    READ = "READ"
    CREATE = "CREATE"
    MOVE = "MOVE"
    RENAME = "RENAME"
    DELETE = "DELETE"


@dataclass(frozen=True)
class FilesystemEntry:
    path: str
    kind: str
    size_bytes: int | None


@dataclass(frozen=True)
class FilesystemMutationResult:
    action: str
    source_path: str
    destination_path: str | None
    verified: bool
    recovery_path: str | None = None


class FamilyFilesystemManager:
    """Operate only inside roots previously registered by an asset scan."""

    def __init__(
        self,
        family: FamilyManager,
        assets: FamilyAssetRepo,
        audit: FamilyTransactionRepo,
        *,
        permission_evaluator: FamilyPermissionEvaluator | None = None,
    ) -> None:
        self.family = family
        self.assets = assets
        self.audit = audit
        self.permissions = permission_evaluator or FamilyPermissionEvaluator(family.repo)

    def list(
        self, family_id: str, user: User, *, source_id: str, path: str = "."
    ) -> builtins.list[FilesystemEntry]:
        root = self._root(family_id, user, source_id)
        target = self._resolve(root, path, must_exist=True)
        if not target.is_dir():
            raise self._invalid("filesystem path is not a directory")
        rows = [self._entry(root, item) for item in sorted(target.iterdir()) if not item.is_symlink()]
        self._audit(family_id, user, "filesystem.list", path, {"count": len(rows)})
        return rows

    def search(
        self,
        family_id: str,
        user: User,
        *,
        source_id: str,
        query: str,
        path: str = ".",
        limit: int = 100,
    ) -> list[FilesystemEntry]:
        if not 1 <= limit <= 500:
            raise self._invalid("filesystem search limit must be between 1 and 500")
        root = self._root(family_id, user, source_id)
        target = self._resolve(root, path, must_exist=True)
        if not target.is_dir():
            raise self._invalid("filesystem path is not a directory")
        pattern = query.casefold()
        rows: builtins.list[FilesystemEntry] = []
        for item in target.rglob("*"):
            if item.is_symlink() or ".homemind-trash" in item.parts:
                continue
            if fnmatch.fnmatch(item.name.casefold(), f"*{pattern}*"):
                rows.append(self._entry(root, item))
                if len(rows) == limit:
                    break
        self._audit(family_id, user, "filesystem.search", path, {"count": len(rows)})
        return rows

    def read(
        self,
        family_id: str,
        user: User,
        *,
        source_id: str,
        path: str,
        max_bytes: int = 1024 * 1024,
    ) -> str:
        if not 1 <= max_bytes <= 4 * 1024 * 1024:
            raise self._invalid("filesystem read limit must be between 1 and 4194304 bytes")
        root = self._root(family_id, user, source_id)
        target = self._resolve(root, path, must_exist=True)
        if not target.is_file():
            raise self._invalid("filesystem path is not a file")
        if target.stat().st_size > max_bytes:
            raise self._invalid("filesystem file exceeds read limit")
        content = target.read_text(encoding="utf-8")
        self._audit(family_id, user, "filesystem.read", path, {"bytes": len(content.encode())})
        return content

    def execute(
        self,
        family_id: str,
        user: User,
        *,
        action: str,
        transaction_id: str,
        payload: dict[str, object],
    ) -> FilesystemMutationResult:
        source_id = str(payload["source_id"])
        root = self._root(family_id, user, source_id)
        source_text = str(payload["path"])
        source = self._resolve(root, source_text, must_exist=True)
        if not source.is_file():
            raise self._invalid("filesystem mutations currently require a file")

        if action == "filesystem.delete":
            recovery = root / ".homemind-trash" / transaction_id / source.relative_to(root)
            recovery.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(recovery))
            verified = not source.exists() and recovery.is_file()
            if not verified:
                if recovery.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(recovery), str(source))
                raise RuntimeError("filesystem delete verification failed")
            return FilesystemMutationResult(action, source_text, None, True, recovery.relative_to(root).as_posix())

        destination_text = str(payload["destination"])
        destination = self._resolve(root, destination_text, must_exist=False)
        if destination.exists():
            raise self._invalid("filesystem destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        before_hash = self._sha256(source)
        if action == "filesystem.copy":
            shutil.copy2(source, destination)
            verified = source.is_file() and destination.is_file() and self._sha256(destination) == before_hash
            if not verified:
                destination.unlink(missing_ok=True)
                raise RuntimeError("filesystem copy verification failed")
        elif action in {"filesystem.move", "filesystem.rename"}:
            shutil.move(str(source), str(destination))
            verified = not source.exists() and destination.is_file() and self._sha256(destination) == before_hash
            if not verified:
                if destination.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(destination), str(source))
                raise RuntimeError(f"{action} verification failed")
        else:
            raise self._invalid(f"unsupported filesystem action: {action}")
        return FilesystemMutationResult(action, source_text, destination_text, True)

    def _root(self, family_id: str, user: User, source_id: str) -> Path:
        self.family.require_access(family_id, user)
        source = self.assets.get_source(source_id)
        if source is None or source.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family asset source not found")
        decision = self.permissions.evaluate(
            family_id=family_id,
            user=user,
            action="filesystem.read",
            space_id=source.space_id,
            asset=source,
        )
        if decision.effect is not PermissionEffect.ALLOW:
            raise OctopError(ErrorCode.FORBIDDEN, "family asset source access denied")
        return self._source_path(source)

    @staticmethod
    def _source_path(source: FamilyAssetSourceRow) -> Path:
        parsed = urlparse(source.directory_uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "asset source is not a local directory")
        root = Path(url2pathname(unquote(parsed.path))).resolve(strict=True)
        if not root.is_dir() or root.is_symlink():
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "asset source root is invalid")
        return root

    def _resolve(self, root: Path, relative: str, *, must_exist: bool) -> Path:
        raw = PurePath(relative)
        if (
            raw.is_absolute()
            or any(part == ".." for part in raw.parts)
            or ".homemind-trash" in raw.parts
        ):
            raise self._invalid("absolute paths and parent traversal are forbidden")
        candidate = root.joinpath(*raw.parts)
        check = candidate if candidate.exists() else candidate.parent
        resolved = check.resolve(strict=True)
        if not resolved.is_relative_to(root) or any(part.is_symlink() for part in self._parents(root, check)):
            raise self._invalid("filesystem path escapes the registered source")
        if must_exist and not candidate.exists():
            raise OctopError(ErrorCode.NOT_FOUND, "filesystem path not found")
        if candidate.exists() and candidate.is_symlink():
            raise self._invalid("symbolic links are forbidden")
        return candidate

    @staticmethod
    def _parents(root: Path, target: Path) -> builtins.list[Path]:
        relative = target.relative_to(root)
        current = root
        paths = [root]
        for part in relative.parts:
            current /= part
            paths.append(current)
        return paths

    @staticmethod
    def _entry(root: Path, path: Path) -> FilesystemEntry:
        return FilesystemEntry(path.relative_to(root).as_posix(), "directory" if path.is_dir() else "file", path.stat().st_size if path.is_file() else None)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _audit(self, family_id: str, user: User, action: str, target: str, detail: dict[str, object]) -> None:
        self.audit.add_audit(family_id, user.id, action, "SUCCESS", transaction_id=None, target=target, detail_json=json.dumps(detail, sort_keys=True))

    @staticmethod
    def _invalid(message: str) -> HomeMindError:
        return HomeMindError(HomeMindErrorCode.FAMILY_INVALID, message)
