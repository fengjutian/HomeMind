"""Path containment for the device runtime.

The runtime executes filesystem commands on behalf of a household, so a
single unresolved ``..`` is a remote file-read primitive. Everything here
is deliberately paranoid:

* **Resolve, then compare.** The candidate is fully resolved (symlinks
  included) and must still sit inside the resolved root. Comparing the
  *string* forms would be defeated by both ``a/../../b`` and a symlink
  pointing outside.
* **Refuse a missing root.** A runtime with no root cannot serve
  path-bearing commands at all; failing open here would mean "no root
  configured" reads as "everything allowed".
* **Windows reparse points.** A junction or symlink on Windows resolves
  like any other link, but a *dangling* one does not, so the resolved
  check is paired with a lexical one on the pre-resolution path.

Cross-platform by construction: every comparison uses ``pathlib`` and
``os.path.normcase``, never a hard-coded ``/``.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePath


class PathEscapeError(ValueError):
    """A requested path resolved outside the runtime's authorized root."""


def normalize(path: str) -> str:
    """Case-normalised absolute form, for comparison only."""
    return os.path.normcase(os.path.abspath(path))


def _is_within(candidate: str, root: str) -> bool:
    """True when ``candidate`` is ``root`` or sits beneath it.

    Uses ``os.path.commonpath`` rather than a string ``startswith``: on
    Windows ``C:/data-evil`` starts with ``C:/data`` as text but is not
    inside it, and ``startswith`` is exactly the check that lets it
    through.
    """
    try:
        common = os.path.commonpath([candidate, root])
    except ValueError:
        # Different drives on Windows; no common prefix means no overlap.
        return False
    return common == root


def resolve_within(candidate: str, root: str) -> Path:
    """Resolve ``candidate`` and return it, or raise if it escapes ``root``.

    Both the lexical form and the resolved form are checked. The lexical
    check rejects a traversal before it is followed (so a dangling symlink
    cannot be used to probe existence), and the resolved check catches a
    link that points outside the root.
    """
    if not root or not root.strip():
        raise PathEscapeError("runtime has no authorized root")
    root_path = Path(root)
    if not root_path.is_dir():
        raise PathEscapeError(f"authorized root does not exist: {root}")

    raw = Path(candidate)
    joined = raw if raw.is_absolute() else root_path / raw

    # Lexical check on the unresolved path, so a traversal is refused even
    # when the final component does not exist.
    if not _is_within(normalize(str(joined)), normalize(str(root_path))):
        raise PathEscapeError(f"path escapes the authorized root: {candidate}")

    resolved = joined.resolve()
    if not _is_within(normalize(str(resolved)), normalize(str(root_path.resolve()))):
        raise PathEscapeError(f"path resolves outside the authorized root: {candidate}")
    return resolved


def is_within(candidate: str, root: str | None) -> bool:
    """Boolean form, matching the server-side check's signature."""
    if not root:
        return False
    try:
        resolve_within(candidate, root)
    except PathEscapeError:
        return False
    except OSError:
        # An unresolvable path is treated as an escape, not as a pass.
        return False
    return True


def relative_to_root(candidate: str, root: str) -> str:
    """The POSIX-style relative form, for reporting back to the server."""
    resolved = resolve_within(candidate, root)
    rel = resolved.relative_to(Path(root).resolve())
    return PurePath(*rel.parts).as_posix()


__all__ = [
    "PathEscapeError",
    "is_within",
    "normalize",
    "relative_to_root",
    "resolve_within",
]
