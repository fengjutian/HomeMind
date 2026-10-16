"""Coverage for the bundled novel-writing expert template."""

from __future__ import annotations

import json

from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root

_ROOT = default_library_root() / "novel-writing-expert"


def test_novel_writing_expert_is_discoverable() -> None:
    catalog = ExpertCatalog(default_library_root())
    catalog.refresh()

    expert = catalog.get("novel-writing-expert")

    assert expert is not None
    assert expert.summary.label_zh == "末世小说家"
    assert expert.prompt_files == ["SOUL.md"]
    assert "SOUL.md" in expert.files
    assert {
        "STORY_BIBLE.md",
        "OUTLINE.md",
        "CONTINUITY.md",
        "STYLE_GUIDE.md",
        "CHAPTER_LOG.md",
        "CHAPTER_BRIEF.md",
        "CHAPTER_QA.md",
    } <= set(expert.files)
    assert len(expert.quick_prompts) == 6


def test_novel_writing_manifest_has_bilingual_workflows() -> None:
    manifest = json.loads((_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["id"] == "novel-writing-expert"
    assert len(manifest["task_examples"]["zh"]) == 6
    assert len(manifest["task_examples"]["en"]) == 6
    assert all(card["prompt"]["zh"].strip() for card in manifest["quick_prompts"])
    assert all(card["prompt"]["en"].strip() for card in manifest["quick_prompts"])


def test_novel_writing_soul_preserves_authorial_control() -> None:
    soul = (_ROOT / "SOUL.md").read_text(encoding="utf-8")

    assert "最终决定权" in soul
    assert "长篇连续性" in soul
    assert "不复刻其独特文风" in soul
    assert "末世题材" in soul
    assert "丧尸、灾变、恐怖、悬疑、战争与军事" in soul
    assert "悬疑必须公平" in soul
    assert "军事细节服务人物与情节" in soul
    assert "默认先拆解，不直接写小说正文" in soul
    assert "用户明确要求或确认结构后，才写小说正文" in soul
    assert "写正文前执行一致性闸门" in soul
    assert "不得在一次回答中连续生成多章" in soul
    assert "上下文包未经用户确认，不直接成稿" in soul
    assert "关键项失败时先修正文" in soul


def test_novel_writing_expert_tracks_knowledge_and_chapter_quality() -> None:
    continuity = (_ROOT / "CONTINUITY.md").read_text(encoding="utf-8")
    brief = (_ROOT / "CHAPTER_BRIEF.md").read_text(encoding="utf-8")
    qa = (_ROOT / "CHAPTER_QA.md").read_text(encoding="utf-8")

    assert "人物知识矩阵" in continuity
    assert "作者真相" in continuity
    assert "禁止提前揭晓的信息" in brief
    assert "人员、武器、弹药与战损是否守恒" in qa
    assert "允许更新正史档案：否" in qa
