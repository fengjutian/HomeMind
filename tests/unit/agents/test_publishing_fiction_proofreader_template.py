"""Coverage for the bundled publishing fiction proofreader template."""

from __future__ import annotations

import json

from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root

_ROOT = default_library_root() / "publishing-fiction-proofreader"


def test_publishing_fiction_proofreader_is_discoverable() -> None:
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()

    expert = catalog.get("publishing-fiction-proofreader")

    assert expert is not None
    assert expert.summary.label_zh == "出版小说校阅专家"
    assert expert.prompt_files == ["SOUL.md"]
    assert "SOUL.md" in expert.files
    assert len(expert.quick_prompts) == 6


def test_publishing_fiction_proofreader_manifest_has_bilingual_workflows() -> None:
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "publishing-fiction-proofreader"
    assert manifest["icon_name"] == "book-open"
    assert len(manifest["task_examples"]["zh"]) == 6
    assert len(manifest["task_examples"]["en"]) == 6
    assert all(card["prompt"]["zh"].strip() for card in manifest["quick_prompts"])
    assert all(card["prompt"]["en"].strip() for card in manifest["quick_prompts"])


def test_publishing_fiction_proofreader_covers_required_audits() -> None:
    soul = (_ROOT / "SOUL.md").read_text(encoding="utf-8")

    assert "错别字" in soul
    assert "语言表述" in soul
    assert "故事逻辑" in soul
    assert "时间线" in soul
    assert "确定矛盾" in soul
    assert "疑似漏洞" in soul
    assert "人物声音" in soul
    assert "最小修改" in soul
    assert "不得声称已核对" in soul
