"""Coverage for the bundled presentation expert template."""

from __future__ import annotations

import json

from octop.infra.agents.experts.catalog import (
    ExpertCatalog,
    default_library_root,
)

_ROOT = default_library_root() / "presentation-expert"


def test_presentation_expert_is_discoverable_and_bundles_pptx_skill() -> None:
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()

    expert = catalog.get("presentation-expert")

    assert expert is not None
    assert expert.summary.label_zh == "PPT 演示专家"
    assert expert.prompt_files == ["SOUL.md"]
    assert "SOUL.md" in expert.files
    assert "skills/pptx/SKILL.md" in expert.files
    assert len(expert.quick_prompts) == 6


def test_presentation_expert_manifest_has_bilingual_six_card_tasks() -> None:
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert len(manifest["task_examples"]["zh"]) == 6
    assert len(manifest["task_examples"]["en"]) == 6
    assert all(card["prompt"]["zh"].strip() for card in manifest["quick_prompts"])
    assert all(card["prompt"]["en"].strip() for card in manifest["quick_prompts"])


def test_presentation_expert_soul_requires_rendered_quality_assurance() -> None:
    soul = (_ROOT / "SOUL.md").read_text(encoding="utf-8")

    assert "结论式标题" in soul
    assert "渲染全部页面" in soul
    assert "不得虚构" in soul
