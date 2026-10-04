"""Compatibility bridge from HomeMind names to the upstream Octop runtime."""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

HOMEMIND_PREFIX = "HOMEMIND_"
OCTOP_PREFIX = "OCTOP_"


def resolve_home(
    environ: MutableMapping[str, str] | None = None,
    *,
    user_home: Path | None = None,
) -> tuple[Path, bool]:
    """Return the data root and whether it is a legacy ``~/.octop`` root."""
    env = os.environ if environ is None else environ
    if raw := env.get("HOMEMIND_HOME", "").strip():
        return Path(raw).expanduser(), False
    if raw := env.get("OCTOP_HOME", "").strip():
        return Path(raw).expanduser(), True

    base = Path.home() if user_home is None else user_home
    current = base / ".homemind"
    legacy = base / ".octop"
    if not current.exists() and legacy.exists():
        return legacy, True
    return current, False


def prepare_environment(
    environ: MutableMapping[str, str] | None = None,
    *,
    user_home: Path | None = None,
) -> Path:
    """Translate HomeMind settings for the compatibility runtime.

    ``HOMEMIND_*`` values win over matching legacy ``OCTOP_*`` values. New
    installs use ``~/.homemind`` and ``homemind.db``. An existing
    ``~/.octop`` tree is reused in place and is never moved or deleted.
    """
    env = os.environ if environ is None else environ
    for key, value in tuple(env.items()):
        if key.startswith(HOMEMIND_PREFIX):
            suffix = key.removeprefix(HOMEMIND_PREFIX)
            env[f"{OCTOP_PREFIX}{suffix}"] = value

    root, legacy = resolve_home(env, user_home=user_home)
    root_text = str(root)
    env["HOMEMIND_HOME"] = root_text
    env["OCTOP_HOME"] = root_text
    if not env.get("OCTOP_DATABASE_SQLITE_PATH", "").strip():
        env["OCTOP_DATABASE_SQLITE_PATH"] = "octop.db" if legacy else "homemind.db"
    return root
