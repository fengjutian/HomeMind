"""Three-way merge a single file: local HEAD (ours) + upstream/develop (theirs), base = merge base.

Usage: python _merge3.py <path> [--write]
Prints the merged result, or writes it in place. Conflicts are marked with
``<<<<<<<`` / ``=======`` / ``>>>>>>>`` markers.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
OURS_REF = "HEAD"
THEIRS_REF = "upstream/develop"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def main() -> int:
    path = sys.argv[1]
    write = "--write" in sys.argv
    base_ref = git("merge-base", "c4e7e180", THEIRS_REF).strip()
    try:
        base = git("show", f"{base_ref}:{path}")
    except subprocess.CalledProcessError:
        base = ""
    ours = git("show", f"{OURS_REF}:{path}")
    theirs = git("show", f"{THEIRS_REF}:{path}")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "base").write_text(base, encoding="utf-8", newline="")
        (d / "ours").write_text(ours, encoding="utf-8", newline="")
        (d / "theirs").write_text(theirs, encoding="utf-8", newline="")
        proc = subprocess.run(
            ["git", "merge-file", "-p", "--diff3", str(d / "ours"), str(d / "base"), str(d / "theirs")],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    merged = proc.stdout
    conflicted = "<<<<<<<" in merged
    print(f"--- {path}: {'CONFLICT' if conflicted else 'clean'} ---")
    if conflicted:
        print(merged)
        return 1
    if write:
        (REPO / path).write_text(merged, encoding="utf-8", newline="")
        print("written")
    else:
        sys.stdout.write(merged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
