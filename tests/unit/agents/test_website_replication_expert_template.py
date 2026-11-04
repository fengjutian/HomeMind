"""Coverage for the bundled website replication expert template."""

from __future__ import annotations

import json

from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root


def test_website_replication_expert_template_is_complete() -> None:
    library = default_library_root()
    catalog = ExpertCatalog(library)
    catalog.refresh()
    expert = catalog.get("website-replication-expert")

    assert expert is not None
    assert expert.summary.label_zh == "网站复刻专家"
    assert expert.summary.label_en == "Website Replication Expert"
    assert expert.prompt_files == ["SOUL.md"]
    assert len(expert.quick_prompts) == 6

    manifest = json.loads(
        (library / "website-replication-expert" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["icon_name"] == "panels-top-left"
    assert len(manifest["task_examples"]["zh"]) == 6
    assert len(manifest["task_examples"]["en"]) == 6
    assert all(card["prompt"]["zh"].strip() for card in manifest["quick_prompts"])
    assert all(card["prompt"]["en"].strip() for card in manifest["quick_prompts"])

    soul = (library / "website-replication-expert" / "SOUL.md").read_text(encoding="utf-8")
    assert "URL 即任务书" in soul
    assert "探索 → 实现 → 启动 → 对比 → 修正 → 验证" in soul
    assert "1440×900" in soul
    assert "P0" in soul
    assert "最多创建 4 个待办" in soul
    assert "不超过 90 秒" in soul
    assert "不得以前台阻塞方式运行" in soul
    assert "截图对比" in soul
    assert "不绕过登录" in soul
