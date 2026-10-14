"""Generate an editable expert draft from a natural-language requirement."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from octop.infra.agents.experts.manifest_generator import (
    ExpertManifestGenerationError,
    normalize_manifest_assets,
    parse_model_json,
)
from octop.infra.utils.llm_text import ainvoke_text

_SKILL_PATH = Path(__file__).with_name("requirement_generator_skill.md")
_GENERATION_TIMEOUT_SECONDS = 90.0
_MAX_REQUIREMENT_CHARS = 8000
_MAX_SOUL_CHARS = 30000
_ALLOWED_ICONS = {
    "zap",
    "list-todo",
    "file-text",
    "activity",
    "trending-up",
    "presentation",
    "cpu",
    "server",
    "wrench",
    "message-square",
    "book-open",
    "globe",
    "mail",
    "terminal",
    "hard-drive",
    "heart",
    "user",
    "sparkles",
}


def resolve_requirement_generator_llm(
    server: Any,
    requested_model: str | None,
) -> tuple[Any, str] | None:
    """Resolve a configured model for an explicit user-requested generation."""
    assert server.app_runtime is not None
    registry = server.app_runtime.agent_registry
    providers = registry.providers
    requested = (requested_model or "").strip()
    if requested and providers.is_model_ref_usable(requested):
        model_ref = requested
    else:
        active_name, active_model = server.services.settings_repo.get_active_model()
        active_ref = f"{active_name}/{active_model}" if active_name and active_model else ""
        model_ref = active_ref if providers.is_model_ref_usable(active_ref) else ""
        if not model_ref:
            model_ref = providers.resolve_first_model_ref() or ""
        if not model_ref or not providers.is_model_ref_usable(model_ref):
            return None

    manager = registry.harness_manager
    factory = manager.shared_factory if manager is not None else None
    if factory is None:
        return None
    try:
        return factory.get(model_ref), model_ref
    except Exception:
        return None


async def generate_expert_from_requirement(
    *,
    llm: Any,
    requirement: str,
    locale: str,
    timeout: float = _GENERATION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Generate and strictly normalize a non-executable expert draft."""
    clean_requirement = requirement.strip()
    if not clean_requirement:
        raise ExpertManifestGenerationError("expert requirement is empty")
    if len(clean_requirement) > _MAX_REQUIREMENT_CHARS:
        raise ExpertManifestGenerationError("expert requirement is too long")

    system_prompt = await asyncio.to_thread(_SKILL_PATH.read_text, encoding="utf-8")
    payload = {
        "requirement": clean_requirement,
        "preferred_locale": locale,
        "constraints": {
            "editable_draft": True,
            "executable_files_allowed": False,
            "quick_prompts": 6,
            "task_examples": [3, 6],
        },
    }
    model = _bind_json_response(llm)
    text = await ainvoke_text(
        model,
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2)),
        ],
        timeout=timeout,
    )
    raw = parse_model_json(text)
    nested_manifest = raw.get("manifest")
    assets_payload: dict[str, Any]
    if isinstance(nested_manifest, dict):
        assets_payload = nested_manifest
    else:
        assets_payload = raw
    assets = normalize_manifest_assets(
        assets_payload,
        fallback_name_zh="自定义专家",
        fallback_name_en="Custom Expert",
        fallback_summary_zh="根据用户需求生成的可编辑专家。",
        fallback_summary_en="An editable expert generated from the user's requirements.",
    )
    soul = raw.get("soul_md")
    if not isinstance(soul, str) or not soul.strip():
        raise ExpertManifestGenerationError("generated SOUL.md is empty")
    soul = soul.strip()
    if len(soul) > _MAX_SOUL_CHARS:
        soul = soul[:_MAX_SOUL_CHARS].rstrip()

    icon_name = raw.get("icon_name")
    if not isinstance(icon_name, str) or icon_name not in _ALLOWED_ICONS:
        icon_name = "sparkles"
    color = raw.get("color")
    if not isinstance(color, str) or not _is_hex_color(color):
        color = "#e8f4ff"

    return {
        **assets,
        "icon_name": icon_name,
        "color": color,
        "soul_md": soul,
    }


def _bind_json_response(llm: Any) -> Any:
    bind = getattr(llm, "bind", None)
    if not callable(bind):
        return llm
    try:
        return bind(response_format={"type": "json_object"})
    except TypeError:
        return llm


def _is_hex_color(value: str) -> bool:
    if len(value) != 7 or not value.startswith("#"):
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value[1:])
