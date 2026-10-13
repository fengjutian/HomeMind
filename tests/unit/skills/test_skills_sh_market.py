from __future__ import annotations

import io
import json
import urllib.error

import pytest

from octop.infra.skills import skills_sh_market


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def test_read_search_normalizes_installable_github_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "skills": [
            {
                "id": "vercel-labs/skills/find-skills",
                "source": "vercel-labs/skills",
                "skillId": "find-skills",
                "name": "Find Skills",
                "installs": 123,
            },
            {"source": "example.com", "skillId": "well-known", "name": "Skipped"},
        ]
    }
    monkeypatch.setattr(
        skills_sh_market.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(json.dumps(payload).encode()),
    )

    assert skills_sh_market._read_search("find", limit=10, timeout=1) == [
        {
            "id": "vercel-labs/skills/find-skills",
            "slug": "find-skills",
            "name": "Find Skills",
            "source": "vercel-labs/skills",
            "installs": 123,
            "url": "https://skills.sh/vercel-labs/skills/find-skills",
        }
    ]


def test_read_search_maps_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.URLError(TimeoutError())

    monkeypatch.setattr(skills_sh_market.urllib.request, "urlopen", fail)
    with pytest.raises(skills_sh_market.SkillsShMarketTimeout):
        skills_sh_market._read_search("find", limit=10, timeout=1)


@pytest.mark.asyncio
async def test_search_uses_default_query_and_clamps_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, object] = {}

    def fake(query: str, *, limit: int, timeout: float) -> list[dict[str, object]]:
        called.update(query=query, limit=limit, timeout=timeout)
        return []

    monkeypatch.setattr(skills_sh_market, "_read_search", fake)
    assert await skills_sh_market.search_skills_sh("", limit=999) == []
    assert called == {"query": "skill", "limit": 100, "timeout": 20}
