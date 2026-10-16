"""Coverage for the bundled Android development expert template."""

from __future__ import annotations

import json

from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root

_ROOT = default_library_root() / "android-developer"


def test_android_developer_is_discoverable() -> None:
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()

    expert = catalog.get("android-developer")

    assert expert is not None
    assert expert.summary.label_zh == "Android 开发专家"
    assert expert.prompt_files == ["SOUL.md"]
    assert "SOUL.md" in expert.files
    assert len(expert.quick_prompts) == 6


def test_android_developer_manifest_has_bilingual_workflows() -> None:
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "android-developer"
    assert len(manifest["task_examples"]["zh"]) == 6
    assert len(manifest["task_examples"]["en"]) == 6
    assert all(card["prompt"]["zh"].strip() for card in manifest["quick_prompts"])
    assert all(card["prompt"]["en"].strip() for card in manifest["quick_prompts"])


def test_android_developer_soul_covers_delivery_constraints() -> None:
    soul = (_ROOT / "SOUL.md").read_text(encoding="utf-8")

    assert "Jetpack Compose" in soul
    assert "传统 View/XML" in soul
    assert "进程死亡和配置变更" in soul
    assert "不声称未执行" in soul
