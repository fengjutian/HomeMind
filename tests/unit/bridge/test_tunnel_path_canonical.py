"""Tunnel path canonicalisation (plan phase 15).

The rule: a path must already be canonical, or it is refused. A path that
needs ``.``, ``..``, ``//`` or percent-decoding to be understood is one whose
literal form and routed form can differ, and which of the two the allow-list
judged would then decide the answer.
"""

from __future__ import annotations

import pytest

from octop.infra.bridge.tunnel_policy import is_tunnel_path_allowed, normalize_tunnel_path


class TestNormalize:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("/api/agents", "/api/agents"),
            ("/api/agents/ag1", "/api/agents/ag1"),
            ("/api/agents/ag1/threads", "/api/agents/ag1/threads"),
            ("/api/agents/ag1/threads?page=2", "/api/agents/ag1/threads"),
            ("/api/agents/ag1/threads/", "/api/agents/ag1/threads"),
        ],
    )
    def test_canonical_paths_pass_through(self, raw: str, expected: str) -> None:
        assert normalize_tunnel_path(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "/api/agents/../admin",
            "/api/agents/./threads",
            "/api//agents",
            "/api/agents/ag1//threads",
            "/api/agents/%2e%2e/admin",
            "/api/agents/%2E%2E/admin",
            "/api/agents/a\\..\\admin",
            "/api/agents/%252e%252e/admin",
            "api/agents",
            "",
            "/",
        ],
    )
    def test_non_canonical_paths_are_refused(self, raw: str) -> None:
        assert normalize_tunnel_path(raw) is None

    def test_a_plain_segment_named_like_a_word_is_fine(self) -> None:
        assert normalize_tunnel_path("/api/agents/...weird") == "/api/agents/...weird"


class TestStillRefused:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/agents/../../admin/users"),
            ("GET", "/api/agents/%2e%2e/admin"),
            ("GET", "/api//agents"),
            ("GET", "/api/agents/./threads"),
            ("GET", "/api/agents%2f..%2fadmin"),
            ("GET", "/api/agents/x/../../../admin"),
            ("GET", "/api/agents/%252e%252e/admin"),
            ("GET", "/api/agents/a\\..\\admin"),
        ],
    )
    def test_traversal_variants_stay_denied(self, method: str, path: str) -> None:
        assert is_tunnel_path_allowed(method, path) is False


class TestNothingRegressed:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/agents"),
            ("GET", "/api/agents/ag1"),
            ("GET", "/api/agents/ag1/threads"),
            ("POST", "/api/agents/bridge:c1:ag1/upload"),
            ("GET", "/api/agents/"),
            ("GET", "/api/uploads/sessions"),
            ("GET", "/api/uploads/sessions/abc"),
            ("PUT", "/api/uploads/sessions/abc/parts/1"),
            ("POST", "/api/uploads/sessions/abc/complete"),
            ("DELETE", "/api/uploads/sessions/abc"),
            ("GET", "/api/uploads/blobs/abc"),
            ("GET", "/api/knowledge-bases"),
            ("GET", "/api/cron/settings"),
            ("GET", "/api/plugins/agents/bridge:c1:ag1"),
        ],
    )
    def test_legitimate_routes_are_still_allowed(self, method: str, path: str) -> None:
        assert is_tunnel_path_allowed(method, path) is True

    def test_query_strings_do_not_affect_the_decision(self) -> None:
        assert is_tunnel_path_allowed("GET", "/api/agents/ag1?x=1") is True
        # The query is not part of what we authorise, so a smuggled one cannot
        # smuggle a path.
        assert is_tunnel_path_allowed("GET", "/api/agents?x=/../admin") is True

    def test_method_still_governs(self) -> None:
        assert is_tunnel_path_allowed("GET", "/api/cron/settings") is True
        assert is_tunnel_path_allowed("POST", "/api/cron/settings") is False
        assert is_tunnel_path_allowed("DELETE", "/api/uploads/blobs/x") is False


class TestAttachmentUploadIsReachable:
    def test_the_real_upload_route_is_allow_listed(self) -> None:
        """Regression: the list said ``uploads``, the route is ``upload``.

        With the typo, attaching a file to a *remote* Bridge agent was refused
        outright — the feature could not work at all.
        """
        assert is_tunnel_path_allowed("POST", "/api/agents/bridge:c1:ag1/upload") is True
        assert is_tunnel_path_allowed("POST", "/api/agents/ag1/upload") is True
