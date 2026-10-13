"""Read-only client for the public skills.sh search used by its official CLI."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_SEARCH_URL = "https://skills.sh/api/search"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_SOURCE_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class SkillsShMarketError(RuntimeError):
    """The skills.sh search request or response was invalid."""


class SkillsShMarketTimeout(SkillsShMarketError):
    """The skills.sh search request timed out."""


def _read_search(query: str, *, limit: int, timeout: float) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"q": query, "limit": limit})
    request = urllib.request.Request(
        f"{_SEARCH_URL}?{params}",
        headers={"Accept": "application/json", "User-Agent": "octop-skills-sh-market/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise SkillsShMarketError(f"skills.sh search failed: HTTP {exc.code}") from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
            raise SkillsShMarketTimeout("skills.sh search timed out") from exc
        raise SkillsShMarketError("skills.sh search request failed") from exc
    except OSError as exc:
        raise SkillsShMarketError("skills.sh search request failed") from exc

    if len(payload) > _MAX_RESPONSE_BYTES:
        raise SkillsShMarketError("skills.sh search response is too large")
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillsShMarketError("skills.sh returned invalid JSON") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("skills"), list):
        raise SkillsShMarketError("skills.sh search response is invalid")

    results: list[dict[str, Any]] = []
    for item in raw["skills"]:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        slug = str(item.get("skillId") or item.get("name") or "").strip()
        if not _SOURCE_RE.fullmatch(source) or not _SLUG_RE.fullmatch(slug):
            continue
        name = str(item.get("name") or slug).strip()[:200] or slug
        installs = item.get("installs")
        results.append(
            {
                "id": f"{source}/{slug}",
                "slug": slug,
                "name": name,
                "source": source,
                "installs": installs if isinstance(installs, int) and installs >= 0 else 0,
                "url": f"https://skills.sh/{source}/{slug}",
            }
        )
    return results


async def search_skills_sh(query: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Search skills.sh using the credential-free endpoint used by ``skills find``."""
    normalized = query.strip() or "skill"
    if len(normalized) < 2:
        normalized = f"{normalized} skill"
    effective_limit = max(1, min(limit, 100))
    return await asyncio.to_thread(
        _read_search,
        normalized,
        limit=effective_limit,
        timeout=20,
    )


__all__ = ["SkillsShMarketError", "SkillsShMarketTimeout", "search_skills_sh"]
