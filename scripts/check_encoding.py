"""Scan HomeMind + dashboard sources for encoding damage.

Detects the two failure modes the spec calls out:
  * mojibake markers (莽 / 锟 / the replacement character),
  * files that are not valid UTF-8 at all.

Run as a script; exits non-zero when anything is damaged.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (ROOT / "src" / "homemind", ROOT / "dashboard" / "src")
MARKERS = ("莽", "锟", "�")
SKIP_DIRS = {"node_modules", "dist", "__pycache__", ".git", "build"}


def main() -> int:
    problems: list[str] = []
    scanned = 0
    for target in TARGETS:
        if not target.is_dir():
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".json", ".md", ".less"}:
                continue
            scanned += 1
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                problems.append(f"{path}: not valid UTF-8 ({exc})")
                continue
            for marker in MARKERS:
                if marker in text:
                    line = next(
                        (
                            index + 1
                            for index, row in enumerate(text.splitlines())
                            if marker in row
                        ),
                        0,
                    )
                    problems.append(f"{path}:{line}: contains {marker!r}")
    print(f"scanned {scanned} files")
    for problem in problems:
        print(f"  {problem}")
    print("clean" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
