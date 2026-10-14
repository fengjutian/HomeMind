"""Tests for natural-language expert draft generation."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.agents.experts.manifest_generator import ExpertManifestGenerationError
from octop.infra.agents.experts.requirement_generator import (
    generate_expert_from_requirement,
)


class FakeLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.messages: list[Any] = []

    def bind(self, **_: Any) -> FakeLLM:
        return self

    async def ainvoke(self, messages: list[Any]) -> SimpleNamespace:
        self.messages = messages
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


def _payload() -> dict[str, Any]:
    return {
        "label": {"zh": "生物研究专家", "en": "Biology Research Expert"},
        "description": {
            "zh": "帮助设计实验并研判生物学证据。",
            "en": "Designs experiments and appraises biological evidence.",
        },
        "welcome_message": {
            "zh": "把生物学问题转化为可验证的研究方案",
            "en": "Turn biology questions into verifiable research plans",
        },
        "icon_name": "sparkles",
        "color": "#e8f4ff",
        "quick_prompts": [
            {
                "title": {"zh": f"研究入口{i}", "en": f"Research {i}"},
                "description": {"zh": "分析研究问题", "en": "Analyze a research question"},
                "prompt": {"zh": "请分析以下问题：\n", "en": "Analyze this question:\n"},
                "color": "#e8f4ff",
                "icon_name": "sparkles",
            }
            for i in range(1, 7)
        ],
        "task_examples": {
            "zh": [
                "每天「09:00」汇总研究进展",
                "每周一「10:00」复盘实验",
                "每月 1 日「09:30」更新计划",
            ],
            "en": [
                "Every day at 09:00, summarize research progress",
                "Every Monday at 10:00, review experiments",
                "On the 1st of each month at 09:30, update the plan",
            ],
        },
        "soul_md": "# 生物研究专家 · SOUL\n\n区分事实、推断和未知项。",
    }


@pytest.mark.asyncio
async def test_generates_normalized_editable_expert_draft() -> None:
    llm = FakeLLM(_payload())

    draft = await generate_expert_from_requirement(
        llm=llm,
        requirement="创建一个严谨的生物学研究专家",
        locale="zh",
    )

    assert draft["label"]["zh"] == "生物研究专家"
    assert draft["icon_name"] == "sparkles"
    assert draft["color"] == "#e8f4ff"
    assert len(draft["quick_prompts"]) == 6
    assert len(draft["task_examples"]["zh"]) == 3
    assert draft["soul_md"].startswith("# 生物研究专家")
    assert "executable_files_allowed" in llm.messages[1].content


@pytest.mark.asyncio
async def test_rejects_missing_soul() -> None:
    payload = _payload()
    payload.pop("soul_md")

    with pytest.raises(ExpertManifestGenerationError, match="SOUL"):
        await generate_expert_from_requirement(
            llm=FakeLLM(payload),
            requirement="创建一个有完整规则的研究专家",
            locale="zh",
        )


@pytest.mark.asyncio
async def test_falls_back_from_unapproved_visuals() -> None:
    payload = _payload()
    payload["icon_name"] = "skull"
    payload["color"] = "red"

    draft = await generate_expert_from_requirement(
        llm=FakeLLM(payload),
        requirement="创建一个安全的研究专家",
        locale="zh",
    )

    assert draft["icon_name"] == "sparkles"
    assert draft["color"] == "#e8f4ff"
