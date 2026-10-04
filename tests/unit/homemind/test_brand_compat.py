from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from homemind.cli import cli
from homemind.compat import prepare_environment, resolve_home


def test_new_install_uses_homemind_home(tmp_path: Path) -> None:
    env: dict[str, str] = {}

    root = prepare_environment(env, user_home=tmp_path)

    assert root == tmp_path / ".homemind"
    assert env["HOMEMIND_HOME"] == str(root)
    assert env["OCTOP_HOME"] == str(root)
    assert env["OCTOP_DATABASE_SQLITE_PATH"] == "homemind.db"


def test_existing_octop_home_is_reused_without_moving(tmp_path: Path) -> None:
    legacy = tmp_path / ".octop"
    legacy.mkdir()
    env: dict[str, str] = {}

    root = prepare_environment(env, user_home=tmp_path)

    assert root == legacy
    assert env["HOMEMIND_HOME"] == str(legacy)
    assert env["OCTOP_HOME"] == str(legacy)
    assert env["OCTOP_DATABASE_SQLITE_PATH"] == "octop.db"
    assert legacy.is_dir()
    assert not (tmp_path / ".homemind").exists()


def test_homemind_environment_wins_over_legacy_names(tmp_path: Path) -> None:
    current = tmp_path / "current"
    env = {
        "HOMEMIND_HOME": str(current),
        "OCTOP_HOME": str(tmp_path / "legacy"),
        "HOMEMIND_PORT": "9000",
        "OCTOP_PORT": "8088",
    }

    root = prepare_environment(env, user_home=tmp_path)

    assert root == current
    assert env["OCTOP_HOME"] == str(current)
    assert env["OCTOP_PORT"] == "9000"


def test_explicit_legacy_home_remains_supported(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    assert resolve_home({"OCTOP_HOME": str(legacy)}, user_home=tmp_path) == (
        legacy,
        True,
    )


def test_homemind_cli_exposes_upstream_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "HomeMind command-line interface" in result.output
    assert "run" in result.output
    assert "agent" in result.output
    assert "backup" in result.output
