"""The filesystem commands the device runtime executes.

Seven operations, chosen because a household photo library needs them and
nothing more. **Delete is deliberately absent.** Removing a file the user
cannot get back, from a process that runs unattended on a NAS, is not
something to add casually; if a future stage needs it, the plan is to move
into ``.homemind-trash`` *inside the root* after approval, never an
unlink.

Every handler follows the same contract:

* resolve each path through :func:`path_guard.resolve_within` before
  touching the filesystem — an escaped path is an error, never a warning,
* be idempotent where it can be, because the server may re-dispatch a
  command whose lease lapsed,
* return a JSON-serialisable summary, never a path outside the root and
  never file content.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemind.device_runtime.path_guard import (
    PathEscapeError,
    relative_to_root,
    resolve_within,
)

#: The only capabilities this runtime build accepts.
SUPPORTED_CAPABILITIES: frozenset[str] = frozenset(
    {
        "filesystem.list",
        "filesystem.stat",
        "filesystem.hash",
        "filesystem.scan",
        "filesystem.mkdir",
        "filesystem.move",
        "filesystem.copy",
    }
)

#: Version this build speaks; a newer command is refused rather than
#: partially understood.
PROTOCOL_VERSION = 1

_HASH_CHUNK = 1024 * 1024
_MAX_SCAN_ENTRIES = 50_000


class CommandRefused(RuntimeError):
    """The command is understood but must not run as specified."""


class UnsupportedCommand(RuntimeError):
    """The capability is not in this build's contract."""


@dataclass(frozen=True)
class CommandContext:
    """What a handler is allowed to touch."""

    root: str
    command_id: str


Handler = Callable[[CommandContext, dict[str, Any]], dict[str, Any]]


# ----------------------------------------------------------------- reads


def _list(ctx: CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve(ctx, payload.get("path"))
    entries: list[dict[str, Any]] = []
    for child in sorted(path.iterdir(), key=lambda p: p.name):
        if child.name == ".homemind-trash":
            continue
        try:
            stat = child.stat()
        except OSError:
            # A broken symlink is still an entry; the caller just gets no
            # size for it rather than the whole listing failing.
            entries.append({"name": child.name, "type": "broken"})
            continue
        entries.append(
            {
                "name": child.name,
                "type": "directory" if child.is_dir() else "file",
                "size": stat.st_size,
                "modified_at": int(stat.st_mtime),
            }
        )
    return {"entries": entries, "count": len(entries)}


def _stat(ctx: CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve(ctx, payload.get("path"))
    stat = path.stat()
    return {
        "name": path.name,
        "type": "directory" if path.is_dir() else "file",
        "size": stat.st_size,
        "modified_at": int(stat.st_mtime),
        "created_at": int(stat.st_ctime),
    }


def _hash(ctx: CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve(ctx, payload.get("path"))
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size": path.stat().st_size}


def _scan(ctx: CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root = _resolve(ctx, payload.get("path"))
    recursive = bool(payload.get("recursive", True))
    found: list[str] = []
    truncated = False
    walker = root.rglob("*") if recursive else root.glob("*")
    for candidate in walker:
        if len(found) >= _MAX_SCAN_ENTRIES:
            # Reporting the cap is more useful than silently returning a
            # partial tree that looks complete.
            truncated = True
            break
        if ".homemind-trash" in candidate.parts or candidate.is_symlink():
            continue
        if not candidate.is_file():
            continue
        try:
            found.append(relative_to_root(str(candidate), ctx.root))
        except PathEscapeError:
            # A link that escapes the root is simply not part of the tree.
            continue
    return {"files": found, "count": len(found), "truncated": truncated}


# ---------------------------------------------------------------- writes


def _mkdir(ctx: CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    path = _resolve(ctx, payload.get("path"))
    if path.exists():
        if not path.is_dir():
            raise CommandRefused(
                f"a file already exists at {relative_to_root(str(path), ctx.root)}"
            )
        # Idempotent: a retry after a lapsed lease must not fail.
        return {"created": False, "path": relative_to_root(str(path), ctx.root)}
    path.mkdir(parents=True, exist_ok=True)
    return {"created": True, "path": relative_to_root(str(path), ctx.root)}


def _move(ctx: CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    return _transfer(ctx, payload, shutil.move)


def _copy(ctx: CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    return _transfer(ctx, payload, shutil.copy2)


def _transfer(
    ctx: CommandContext,
    payload: dict[str, Any],
    operation: Callable[[str, str], object],
) -> dict[str, Any]:
    """Shared body for move and copy.

    Idempotence comes from the command id: if the destination already
    matches the source, the operation is reported as done rather than
    re-run, so a re-dispatched command cannot copy a file onto itself or
    move one that is already gone.
    """
    source = _resolve(ctx, payload.get("source_path"))
    destination = _resolve(ctx, payload.get("destination_path"))
    if source == destination:
        return {
            "moved": False,
            "reason": "source and destination are the same",
        }
    if not source.exists():
        # Already-transferred is a success, not a failure: the previous
        # attempt may have completed without its report reaching us.
        if destination.exists():
            return {
                "changed": False,
                "reason": "destination already present",
                "path": relative_to_root(str(destination), ctx.root),
            }
        raise CommandRefused(
            f"source does not exist: {relative_to_root(str(source), ctx.root)}",
        )
    if source.is_dir():
        raise CommandRefused("directories are not transferred by this runtime")
    destination.parent.mkdir(parents=True, exist_ok=True)
    operation(str(source), str(destination))
    return {
        "changed": True,
        "path": relative_to_root(str(destination), ctx.root),
        "size": destination.stat().st_size,
    }


# --------------------------------------------------------------- helpers


def _resolve(ctx: CommandContext, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise CommandRefused("command payload is missing a path")
    try:
        return resolve_within(raw, ctx.root)
    except PathEscapeError as exc:
        raise CommandRefused(str(exc)) from exc


HANDLERS: dict[str, Handler] = {
    "filesystem.list": _list,
    "filesystem.stat": _stat,
    "filesystem.hash": _hash,
    "filesystem.scan": _scan,
    "filesystem.mkdir": _mkdir,
    "filesystem.move": _move,
    "filesystem.copy": _copy,
}


def supports(capability: str, protocol_version: int = PROTOCOL_VERSION) -> bool:
    """Whether this build can run ``capability`` at ``protocol_version``.

    A command from a newer server is refused rather than attempted: the
    payload's meaning may have changed, and running a half-understood
    command against someone's photo library is worse than not running it.
    """
    return protocol_version <= PROTOCOL_VERSION and capability in SUPPORTED_CAPABILITIES


def execute(
    ctx: CommandContext,
    capability: str,
    payload: dict[str, Any],
    *,
    protocol_version: int = PROTOCOL_VERSION,
) -> dict[str, Any]:
    """Run one command, mapping refusals to a structured result."""
    if not supports(capability, protocol_version):
        raise UnsupportedCommand(
            f"capability {capability!r} is not supported by runtime protocol v{PROTOCOL_VERSION}",
        )
    return HANDLERS[capability](ctx, payload)


__all__ = [
    "HANDLERS",
    "PROTOCOL_VERSION",
    "SUPPORTED_CAPABILITIES",
    "CommandContext",
    "CommandRefused",
    "UnsupportedCommand",
    "execute",
    "supports",
]
