from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Cross-platform implementation of the repository's ``make all`` gate.
REPO_ROOT = Path(__file__).resolve().parents[1]


def _python_tool(*args: str) -> list[str]:
    return [sys.executable, "-m", *args]


def commands() -> list[tuple[str, list[str], Path]]:
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm was not found on PATH")

    pytest_jobs = os.environ.get("PYTEST_JOBS", "auto")
    dashboard = REPO_ROOT / "dashboard"
    return [
        ("format", _python_tool("ruff", "check", "--fix", "src", "tests"), REPO_ROOT),
        ("format", _python_tool("ruff", "format", "src", "tests"), REPO_ROOT),
        ("format-frontend", [npm, "run", "format"], dashboard),
        ("lint", _python_tool("ruff", "check", "src", "tests"), REPO_ROOT),
        ("lint", _python_tool("ruff", "format", "--check", "src", "tests"), REPO_ROOT),
        ("typecheck", _python_tool("mypy", "src/octop"), REPO_ROOT),
        (
            "test",
            _python_tool("pytest", "-n", pytest_jobs, "-m", "not live"),
            REPO_ROOT,
        ),
    ]


def main() -> int:
    try:
        steps = commands()
    except RuntimeError as exc:
        print(f"[all] {exc}", file=sys.stderr)
        return 1

    for label, argv, cwd in steps:
        print(f"[{label}] {' '.join(argv)}", flush=True)
        result = subprocess.run(argv, cwd=cwd, check=False)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
