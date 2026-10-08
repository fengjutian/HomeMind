"""Smart-home HTTP surface must actually be mounted (plan phase 5).

The router existed for a long time and was never included in the app, so every
endpoint was unreachable over HTTP and the whole adapter path was dead code.
These tests exist to make that regression impossible to reintroduce silently.
"""

from __future__ import annotations

from typing import Any

from homemind.api.app import build_app

EXPECTED_ROUTES: tuple[tuple[str, str], ...] = (
    ("post", "/api/homemind/families/{family_id}/smart-home/providers"),
    ("get", "/api/homemind/families/{family_id}/smart-home/providers"),
    ("delete", "/api/homemind/families/{family_id}/smart-home/providers/{provider_id}"),
    ("put", "/api/homemind/families/{family_id}/smart-home/providers/{provider_id}/topics"),
    ("post", "/api/homemind/families/{family_id}/smart-home/providers/{provider_id}/probe"),
    ("post", "/api/homemind/families/{family_id}/smart-home/providers/{provider_id}/sync"),
    ("get", "/api/homemind/families/{family_id}/smart-home/entities"),
    ("get", "/api/homemind/families/{family_id}/smart-home/entities/{entity_id}/commands"),
    ("post", "/api/homemind/families/{family_id}/smart-home/entities/{entity_id}/preview"),
)


def _documented_routes(app: Any) -> set[tuple[str, str]]:
    """Routes from the OpenAPI document.

    ``app.routes`` holds lazy ``_IncludedRouter`` wrappers in this codebase, so
    the generated schema is the reliable source for what is actually exposed.
    """
    paths = app.openapi().get("paths", {})
    return {
        (method, path)
        for path, operations in paths.items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }


async def test_smart_home_router_is_mounted(env: Any) -> None:
    """Route registration only happens once the server has bound services."""
    _client, srv, _auth = env
    present = _documented_routes(build_app(srv))
    missing = [r for r in EXPECTED_ROUTES if r not in present]
    assert not missing, f"smart-home routes not mounted: {missing}"


async def test_smart_home_responses_are_not_publicly_reachable(env: Any) -> None:
    """A mounted-but-unauthenticated route would answer 200 here."""
    client, _srv, _auth = env
    r = await client.get("/api/homemind/families/fam1/smart-home/providers")
    assert r.status_code == 401, r.text
