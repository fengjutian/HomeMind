from __future__ import annotations

from octop.infra.skills.qoder_market import _parse_entry


def test_parse_entry_uses_safe_github_source() -> None:
    payload = b"""---
name: brainstorming
title: Brainstorming
description: Generate ideas
author: obra
githubUrl: https://github.com/obra/superpowers/tree/main/skills/brainstorming
category: productivity
featured: true
popular: true
---
Body
"""
    assert _parse_entry(payload) == {
        "id": "qoder-community/brainstorming",
        "slug": "brainstorming",
        "name": "Brainstorming",
        "description": "Generate ideas",
        "source": "obra",
        "installs": 0,
        "category": "productivity",
        "featured": True,
        "popular": True,
        "url": "https://github.com/obra/superpowers/tree/main/skills/brainstorming",
        "homepage": "https://qoder-community.pages.dev/skills/brainstorming/",
    }


def test_parse_entry_rejects_non_github_install_source() -> None:
    payload = b"""---
name: unsafe
title: Unsafe
githubUrl: https://example.com/skill.zip
---
"""
    assert _parse_entry(payload) is None
