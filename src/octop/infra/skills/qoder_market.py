"""Qoder Community curated skill catalog backed by its GitHub repository."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from octop.infra.utils.frontmatter import parse_frontmatter

_CONTENTS_URL = (
    "https://api.github.com/repos/Qoder-AI/qoder-community/contents/src/content/skills?ref=main"
)
_MAX_INDEX_BYTES = 2 * 1024 * 1024
_MAX_ENTRY_BYTES = 256 * 1024
_CACHE_SECONDS = 15 * 60
_cache: tuple[float, list[dict[str, Any]]] | None = None
_cache_lock = asyncio.Lock()


class QoderMarketError(RuntimeError):
    """The Qoder Community catalog could not be loaded safely."""


def _http_get(url: str, *, max_bytes: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "octop-qoder-market/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            payload = bytes(response.read(max_bytes + 1))
    except urllib.error.HTTPError as exc:
        raise QoderMarketError(f"Qoder Community request failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise QoderMarketError("Qoder Community request failed") from exc
    if len(payload) > max_bytes:
        raise QoderMarketError("Qoder Community response is too large")
    return payload


def _catalog_download_urls() -> list[str]:
    try:
        data = json.loads(_http_get(_CONTENTS_URL, max_bytes=_MAX_INDEX_BYTES))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QoderMarketError("Qoder Community index is invalid") from exc
    if not isinstance(data, list):
        raise QoderMarketError("Qoder Community index is invalid")
    urls: list[str] = []
    for item in data:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        name = str(item.get("name") or "")
        url = str(item.get("download_url") or "")
        parsed = urlparse(url)
        if (
            name.endswith(".md")
            and parsed.scheme == "https"
            and parsed.hostname == "raw.githubusercontent.com"
        ):
            urls.append(url)
    return urls[:200]


def _parse_entry(payload: bytes) -> dict[str, Any] | None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    metadata, _body = parse_frontmatter(text)
    slug = str(metadata.get("name") or "").strip()
    title = str(metadata.get("title") or slug).strip()
    description = str(metadata.get("description") or "").strip()
    github_url = str(metadata.get("githubUrl") or "").strip()
    parsed = urlparse(github_url)
    if (
        not slug
        or "/" in slug
        or "\\" in slug
        or parsed.scheme != "https"
        or parsed.hostname not in {"github.com", "www.github.com"}
    ):
        return None
    return {
        "id": f"qoder-community/{slug}",
        "slug": slug,
        "name": title[:200] or slug,
        "description": description[:1024],
        "source": str(metadata.get("author") or "Qoder Community").strip()[:200],
        "installs": 0,
        "category": str(metadata.get("category") or "").strip()[:100],
        "featured": metadata.get("featured") is True,
        "popular": metadata.get("popular") is True,
        "url": github_url,
        "homepage": f"https://qoder-community.pages.dev/skills/{slug}/",
    }


async def _download_entry(url: str, semaphore: asyncio.Semaphore) -> dict[str, Any] | None:
    async with semaphore:
        payload = await asyncio.to_thread(_http_get, url, max_bytes=_MAX_ENTRY_BYTES)
    return _parse_entry(payload)


async def _load_catalog() -> list[dict[str, Any]]:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_SECONDS:
        return list(_cache[1])
    async with _cache_lock:
        now = time.monotonic()
        if _cache is not None and now - _cache[0] < _CACHE_SECONDS:
            return list(_cache[1])
        urls = await asyncio.to_thread(_catalog_download_urls)
        semaphore = asyncio.Semaphore(8)
        rows = await asyncio.gather(
            *(_download_entry(url, semaphore) for url in urls),
            return_exceptions=True,
        )
        catalog = [row for row in rows if isinstance(row, dict)]
        if urls and not catalog:
            raise QoderMarketError("Qoder Community entries could not be loaded")
        catalog.sort(key=lambda row: (not row["featured"], not row["popular"], row["name"]))
        _cache = (now, catalog)
        return list(catalog)


async def search_qoder_market(query: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
    """Return Qoder Community curated skills, optionally filtered locally."""
    catalog = await _load_catalog()
    needle = query.strip().casefold()
    if needle:
        catalog = [
            row
            for row in catalog
            if needle
            in " ".join(
                str(row[key]) for key in ("slug", "name", "description", "source", "category")
            ).casefold()
        ]
    return catalog[: max(1, min(limit, 200))]


__all__ = ["QoderMarketError", "search_qoder_market"]
