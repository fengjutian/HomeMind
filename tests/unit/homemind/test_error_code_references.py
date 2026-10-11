"""Every ``ErrorCode.NAME`` reference must name a real member.

``ErrorCode`` is a closed ``StrEnum``. Naming a member it does not define is
not caught at import time — the attribute is only resolved when that branch
actually runs, so the first request that hits it raises ``AttributeError``
and surfaces as an opaque 500 instead of the error the caller wrote.

Walking the tree here turns that class of latent bug into a review-time
failure.
"""

from __future__ import annotations

import ast
from pathlib import Path

import homemind
from octop.infra.errors import ErrorCode

HOMEMIND_ROOT = Path(homemind.__file__).resolve().parent


def _referenced_members(tree: ast.AST) -> set[str]:
    """Collect every ``ErrorCode.NAME`` attribute access in ``tree``."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ErrorCode"
        ):
            found.add(node.attr)
    return found


def test_all_error_code_references_exist() -> None:
    members = frozenset(ErrorCode.__members__)
    sources = sorted(HOMEMIND_ROOT.rglob("*.py"))
    assert sources, f"no HomeMind sources found under {HOMEMIND_ROOT}"

    unknown: list[str] = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(HOMEMIND_ROOT).as_posix()
        for name in sorted(_referenced_members(tree) - members):
            unknown.append(f"{rel}: ErrorCode.{name}")

    assert not unknown, "ErrorCode defines no such member: " + ", ".join(unknown)
