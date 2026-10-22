"""Coverage for the bundled technology research expert template."""

from __future__ import annotations

import json

from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root

_ROOT = default_library_root() / "technology-research"


def test_technology_research_expert_is_discoverable() -> None:
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()

    expert = catalog.get("technology-research")

    assert expert is not None
    assert expert.summary.label_zh == "技术调研专家"
    assert expert.summary.label_en == "Technology Research Expert"
    assert expert.prompt_files == ["SOUL.md"]
    assert "SOUL.md" in expert.files
    assert len(expert.quick_prompts) == 6


def test_technology_research_manifest_has_bilingual_workflows() -> None:
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "technology-research"
    assert len(manifest["task_examples"]["zh"]) == 6
    assert len(manifest["task_examples"]["en"]) == 6
    assert all(card["prompt"]["zh"].strip() for card in manifest["quick_prompts"])
    assert all(card["prompt"]["en"].strip() for card in manifest["quick_prompts"])


def test_technology_research_soul_enforces_evidence_and_validation() -> None:
    soul = (_ROOT / "SOUL.md").read_text(encoding="utf-8")

    assert "必须联网核实" in soul
    assert "来源优先级" in soul
    assert "PoC 与基准验证" in soul
    assert "不虚构已执行的搜索" in soul
