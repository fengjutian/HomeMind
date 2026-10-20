"""Coverage for the bundled Visio diagram expert template."""

from __future__ import annotations

import json

from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root

_ROOT = default_library_root() / "visio-expert"


def test_visio_expert_is_discoverable() -> None:
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()

    expert = catalog.get("visio-expert")

    assert expert is not None
    assert expert.summary.label_zh == "Visio 制图专家"
    assert expert.prompt_files == ["SOUL.md"]
    assert "SOUL.md" in expert.files
    assert len(expert.quick_prompts) == 6


def test_visio_expert_manifest_has_bilingual_tasks() -> None:
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert len(manifest["task_examples"]["zh"]) == 6
    assert len(manifest["task_examples"]["en"]) == 6
    assert all(card["prompt"]["zh"].strip() for card in manifest["quick_prompts"])
    assert all(card["prompt"]["en"].strip() for card in manifest["quick_prompts"])


def test_visio_expert_requires_editable_output_and_quality_assurance() -> None:
    soul = (_ROOT / "SOUL.md").read_text(encoding="utf-8")

    assert "可编辑的 `.vsdx`" in soul
    assert "动态连接线" in soul
    assert "图型选择决策" in soul
    assert "交付前质检清单" in soul
    assert "文件含宏时保留原格式与宏" in soul
    assert "不得虚构" in soul
    assert "不覆盖用户原文件" in soul
