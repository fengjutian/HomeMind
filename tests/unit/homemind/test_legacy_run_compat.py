from __future__ import annotations

from typing import Any

from octop.cli.commands import run as run_command


def test_legacy_octop_run_uses_homemind_composition(monkeypatch: Any) -> None:
    received: dict[str, object] = {}

    def fake_run(**kwargs: object) -> None:
        received.update(kwargs)

    monkeypatch.setattr("homemind.launch.run_foreground_blocking", fake_run)

    run_command._run_uvicorn(host="127.0.0.1", port=8088)

    assert received == {"host": "127.0.0.1", "port": 8088}
