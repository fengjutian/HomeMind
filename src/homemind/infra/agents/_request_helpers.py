"""Shared helpers for the agent middleware pipeline.

Kept in its own module so ``family_context_middleware`` and
``memory_post_turn`` can both import the helpers without creating a
circular import between each other.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from langgraph.config import get_config


@dataclass(frozen=True)
class ResolvedTurnUser:
    """Caller identity read from per-request LangGraph ``configurable``.

    The agent runtime writes the authenticated principal onto the
    request's ``configurable`` slot; this struct pulls the user id
    out without touching any process global so concurrent users stay
    isolated.
    """

    user_id: int
    thread_id: str

    @classmethod
    def from_request_config(cls) -> "ResolvedTurnUser | None":
        try:
            configurable = dict(get_config().get("configurable") or {})
        except RuntimeError:  # outside a LangGraph run — fall through
            return None
        raw_user = configurable.get("user")
        user_id: int | None = None
        if isinstance(raw_user, int):
            user_id = raw_user
        elif isinstance(raw_user, str) and raw_user.isdigit():
            user_id = int(raw_user)
        if user_id is None:
            return None
        thread_id = str(configurable.get("thread_id") or "")
        return cls(user_id=user_id, thread_id=thread_id)


def extract_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for chunk in content:
            if isinstance(chunk, dict):
                if chunk.get("type") == "text" and isinstance(chunk.get("text"), str):
                    chunks.append(chunk["text"])
            elif isinstance(chunk, str):
                chunks.append(chunk)
        return "\n".join(chunks).strip()
    return ""


def message_hash(text: str) -> str:
    """Stable per-process hash for the latest user message.

    Used to dedupe post-turn extraction across the multiple model
    calls a single agent turn may make.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["ResolvedTurnUser", "extract_text", "message_hash"]
