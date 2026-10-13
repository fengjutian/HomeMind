from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import make_all


def test_commands_match_make_all_order(monkeypatch) -> None:
    monkeypatch.setattr(make_all.shutil, "which", lambda command: f"bin/{command}")
    monkeypatch.setenv("PYTEST_JOBS", "4")

    steps = make_all.commands()

    assert [label for label, _, _ in steps] == [
        "format",
        "format",
        "format-frontend",
        "lint",
        "lint",
        "typecheck",
        "test",
    ]
    assert steps[-1][1][-5:] == ["pytest", "-n", "4", "-m", "not live"]
    assert steps[2][2] == make_all.REPO_ROOT / "dashboard"


def test_main_stops_after_first_failure(monkeypatch) -> None:
    steps = [
        ("first", ["first"], Path("one")),
        ("second", ["second"], Path("two")),
    ]
    calls: list[list[str]] = []

    def fake_run(argv, *, cwd, check):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(make_all, "commands", lambda: steps)
    monkeypatch.setattr(make_all.subprocess, "run", fake_run)

    assert make_all.main() == 7
    assert calls == [["first"]]
