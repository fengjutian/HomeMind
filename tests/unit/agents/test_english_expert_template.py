"""Coverage for the bundled English expert template."""

from __future__ import annotations

import json

from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root

_ROOT = default_library_root() / "english-expert"


def test_english_expert_is_discoverable_with_its_own_avatar() -> None:
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()

    expert = catalog.get("english-expert")

    assert expert is not None
    assert expert.summary.label_zh == "英语专家"
    assert expert.summary.icon_url == "/experts/avatars/english-expert.svg"
    assert expert.prompt_files == ["SOUL.md"]
    assert "SOUL.md" in expert.files
    assert len(expert.quick_prompts) == 6


def test_english_expert_manifest_is_bilingual() -> None:
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["icon_name"] == "languages"
    assert all(card["prompt"]["zh"].strip() for card in manifest["quick_prompts"])
    assert all(card["prompt"]["en"].strip() for card in manifest["quick_prompts"])


def test_english_expert_soul_covers_requested_behavior() -> None:
    soul = (_ROOT / "SOUL.md").read_text(encoding="utf-8")

    assert "解释单词、拆解语法、中英互译" in soul
    assert "默认将其视为词汇学习请求" in soul
    assert "不要因为单词表示星期、月份、节日或时间概念而调用时间工具" in soul
    assert "诙谐幽默" in soul
    assert "幽默用来帮助理解和记忆，不用来抢戏" in soul


def test_deprecated_default_avatar_is_removed() -> None:
    avatars = default_library_root().parents[5] / "dashboard" / "public" / "experts" / "avatars"

    assert not (avatars / "default.svg").exists()
