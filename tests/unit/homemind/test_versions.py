"""HomeMind product and integrated Octop versions stay independent."""

from __future__ import annotations

import tomllib
from pathlib import Path

from homemind import __version__ as homemind_version
from homemind.version import HOMEMIND_VERSION
from octop import __version__ as octop_version
from octop.upstream_version import OCTOP_UPSTREAM_COMMIT, OCTOP_UPSTREAM_VERSION


def test_homemind_version_matches_package_metadata() -> None:
    root = Path(__file__).resolve().parents[3]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["version"] == HOMEMIND_VERSION
    assert homemind_version == HOMEMIND_VERSION


def test_octop_version_comes_from_upstream_metadata() -> None:
    assert octop_version == OCTOP_UPSTREAM_VERSION
    assert len(OCTOP_UPSTREAM_COMMIT) == 40
    assert all(character in "0123456789abcdef" for character in OCTOP_UPSTREAM_COMMIT)
