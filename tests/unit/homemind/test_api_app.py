from __future__ import annotations

from homemind.api.app import build_app
from homemind.infra.server import HomeMindServer


def test_homemind_app_registers_family_routes() -> None:
    app = build_app(HomeMindServer())
    paths = app.openapi()["paths"]

    assert "/api/homemind/families" in paths
    assert "/api/homemind/families/{family_id}/members" in paths
    assert "/api/homemind/families/{family_id}/albums" in paths
    assert "/api/homemind/families/{family_id}/tasks" in paths
    assert "/api/homemind/families/{family_id}/memories" in paths
    assert "/api/homemind/families/{family_id}/search" in paths
