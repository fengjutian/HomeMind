"""Stage 11 acceptance tests for the HomeMind skill package.

Covers the spec's rules:

* every family skill is discoverable,
* each one names the tools it depends on,
* no skill instructs bypassing the transaction manager,
* no skill instructs bypassing the permission evaluator,
* every skill requires an active family before acting,
* dangerous steps are marked as requiring confirmation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parents[3] / "src" / "homemind" / "skills"

EXPECTED_SKILLS = (
    "family-photo-organizer",
    "family-trip-planner",
    "family-document-manager",
    "family-memory",
    "family-calendar",
    "family-shopping",
)

# Tools the MCP server actually exposes. A skill naming anything else
# is stale documentation that would send an agent hunting for a tool
# that does not exist.
from homemind.tools.mcp_server import HomeMindMcpServer  # noqa: E402

AVAILABLE_TOOLS = set(HomeMindMcpServer.TOOL_NAMES)


def _skill_md(name: str) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", EXPECTED_SKILLS)
def test_skill_exists_and_has_frontmatter(name: str) -> None:
    path = SKILLS_DIR / name / "SKILL.md"
    assert path.is_file(), f"missing skill: {name}"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = text.split("---", 2)[1]
    assert f"name: {name}" in frontmatter
    assert "description:" in frontmatter
    # Both locales are required by the repo's i18n rule.
    assert "zh:" in frontmatter and "en:" in frontmatter


@pytest.mark.parametrize("name", EXPECTED_SKILLS)
def test_skill_requires_active_family(name: str) -> None:
    """Every skill must stop when no family is selected rather than
    guessing one."""

    text = _skill_md(name)
    assert "active family" in text.lower()


@pytest.mark.parametrize("name", EXPECTED_SKILLS)
def test_skill_does_not_bypass_transactions(name: str) -> None:
    """No skill may tell the agent to write a repo directly or skip
    the approval workflow."""

    text = _skill_md(name).lower()
    for forbidden in ("直接写 repo", "绕过 family.plan_transaction", "跳过审批"):
        assert forbidden not in text


@pytest.mark.parametrize("name", EXPECTED_SKILLS)
def test_skill_does_not_bypass_permissions(name: str) -> None:
    text = _skill_md(name).lower()
    assert "绕过" in text or "不得假设" in text, (
        f"{name} should state that permission checks are enforced server-side"
    )


@pytest.mark.parametrize("name", EXPECTED_SKILLS)
def test_skill_only_references_real_tools(name: str) -> None:
    """A skill must not name a tool the MCP server does not expose."""

    text = _skill_md(name)
    referenced = set(re.findall(r"`(family\.[a-z_.]+)`", text))
    unknown = referenced - AVAILABLE_TOOLS
    assert not unknown, f"{name} references unknown tools: {sorted(unknown)}"


@pytest.mark.parametrize("name", EXPECTED_SKILLS)
def test_destructive_skill_marks_dangerous_steps(name: str) -> None:
    """Skills that can move or delete must say so explicitly."""

    text = _skill_md(name)
    if name in {"family-photo-organizer", "family-document-manager"}:
        assert "禁止" in text
        assert any(
            token in text for token in ("恢复站", "不包含任何文件移动", "移动与重命名必须审批")
        )


def test_skill_package_has_no_stray_files() -> None:
    """Only SKILL.md per skill — a stray script would ship without the
    review the rest of the package gets."""

    for name in EXPECTED_SKILLS:
        entries = sorted(
            entry.name for entry in (SKILLS_DIR / name).iterdir()
        )
        assert entries == ["SKILL.md"], f"{name} has unexpected files: {entries}"


def test_skill_package_is_under_the_homemind_src_tree() -> None:
    """Skills live with the code, not in the dashboard or the wheel's
    build output."""

    assert SKILLS_DIR.name == "skills"
    assert SKILLS_DIR.parent.name == "homemind"
