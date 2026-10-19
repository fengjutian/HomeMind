"""Coverage for the bundled historical-fiction novelist template."""

from __future__ import annotations

import json

from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root

_ROOT = default_library_root() / "historical-fiction-novelist"


def test_historical_fiction_novelist_is_discoverable() -> None:
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()

    expert = catalog.get("historical-fiction-novelist")

    assert expert is not None
    assert expert.summary.label_zh == "历史小说家"
    assert expert.prompt_files == ["SOUL.md"]
    assert len(expert.quick_prompts) == 6
    assert {
        "SOUL.md",
        "STORY_BIBLE.md",
        "HISTORY_LEDGER.md",
        "OUTLINE.md",
        "CONTINUITY.md",
        "STYLE_GUIDE.md",
        "CHAPTER_LOG.md",
        "CHAPTER_BRIEF.md",
        "CHAPTER_QA.md",
    } <= set(expert.files)


def test_historical_fiction_manifest_has_bilingual_workflows() -> None:
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert len(manifest["task_examples"]["zh"]) == 6
    assert len(manifest["task_examples"]["en"]) == 6
    assert all(card["prompt"]["zh"].strip() for card in manifest["quick_prompts"])
    assert all(card["prompt"]["en"].strip() for card in manifest["quick_prompts"])


def test_historical_fiction_soul_keeps_history_and_style_boundaries() -> None:
    soul = (_ROOT / "SOUL.md").read_text(encoding="utf-8")

    assert "最终决定权" in soul
    assert "不模仿马伯庸" in soul
    assert "绝不伪造引文" in soul
    assert "小人物叙事" in soul
    assert "公平悬疑" in soul
    assert "A｜硬锚点" in soul
    assert "制度不是背景板" in soul
    assert "人物知识边界" in soul
    assert "正文写作闸门" in soul
    assert "默认一次只生成一个完整场景或一章" in soul


def test_historical_fiction_quick_prompts_define_concrete_outputs() -> None:
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))
    prompts = [card["prompt"]["zh"] for card in manifest["quick_prompts"]]

    assert "A–F" in prompts[0]
    assert "线索表" in prompts[1]
    assert "时代错置" in prompts[2]
    assert "前 12 章" in prompts[3]
    assert "连续性检查" in prompts[4]
    assert "分层审校" in prompts[5]
