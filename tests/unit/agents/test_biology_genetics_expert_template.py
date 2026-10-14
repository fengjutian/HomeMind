"""Coverage for the bundled biology and genetics expert template."""

from __future__ import annotations

import json

from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root

_ROOT = default_library_root() / "biology-genetics-expert"


def test_biology_genetics_expert_is_discoverable() -> None:
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()

    expert = catalog.get("biology-genetics-expert")

    assert expert is not None
    assert expert.summary.label_zh == "生物与基因专家"
    assert expert.prompt_files == ["SOUL.md"]
    assert "SOUL.md" in expert.files
    assert len(expert.quick_prompts) == 6


def test_biology_genetics_manifest_has_bilingual_workflows() -> None:
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["icon_name"] == "dna"
    assert len(manifest["task_examples"]["zh"]) == 6
    assert len(manifest["task_examples"]["en"]) == 6
    assert all(card["prompt"]["zh"].strip() for card in manifest["quick_prompts"])
    assert all(card["prompt"]["en"].strip() for card in manifest["quick_prompts"])


def test_biology_genetics_soul_covers_rigor_and_safety() -> None:
    soul = (_ROOT / "SOUL.md").read_text(encoding="utf-8")

    assert "事实与推断分开" in soul
    assert "可证伪的主假设" in soul
    assert "参考组装" in soul
    assert "多重检验" in soul
    assert "遗传咨询" in soul
    assert "不得编造" in soul
    assert "默认将其视为概念教学" in soul
    assert "不要让试剂用量、温度和时长占据回答主体" in soul
    assert "发生了什么" in soul
    assert "引物如何界定扩增边界与方向" in soul
