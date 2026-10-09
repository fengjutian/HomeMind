"""Bridge connection state machine (plan phase 13).

The behaviour worth protecting is not the enum itself — it is that a retry
attempt can be distinguished from a condition a retry will never fix.
"""

from __future__ import annotations

from typing import Any

import pytest

from octop.infra.bridge.states import (
    LEGACY_STATUS_FOR_STATE,
    BridgeState,
    coerce_state,
    is_recoverable,
    persist_state,
)


class TestStateVocabulary:
    def test_the_eight_planned_states_exist(self) -> None:
        assert {s.value for s in BridgeState} == {
            "DISCONNECTED",
            "CONNECTING",
            "AUTHENTICATING",
            "ONLINE",
            "DEGRADED",
            "REAUTH_REQUIRED",
            "INCOMPATIBLE",
            "DISABLED",
        }

    @pytest.mark.parametrize(
        "state,recoverable",
        [
            (BridgeState.DISCONNECTED, True),
            (BridgeState.CONNECTING, True),
            (BridgeState.AUTHENTICATING, True),
            (BridgeState.DEGRADED, True),
            (BridgeState.REAUTH_REQUIRED, False),
            (BridgeState.INCOMPATIBLE, False),
            (BridgeState.DISABLED, False),
        ],
    )
    def test_retry_only_helps_where_it_can(self, state: BridgeState, recoverable: bool) -> None:
        assert is_recoverable(state) is recoverable
        assert state.needs_human is not recoverable

    def test_only_online_and_degraded_carry_traffic(self) -> None:
        live = {s for s in BridgeState if s.is_live}
        assert live == {BridgeState.ONLINE, BridgeState.DEGRADED}


class TestLegacyMapping:
    def test_every_state_has_a_legacy_value(self) -> None:
        assert set(LEGACY_STATUS_FOR_STATE) == {s.value for s in BridgeState}

    def test_mapping_is_total_and_bounded(self) -> None:
        """The legacy column only ever held these four values."""
        assert set(LEGACY_STATUS_FOR_STATE.values()) == {
            "disconnected",
            "connecting",
            "connected",
            "error",
        }

    def test_online_and_degraded_differ_in_the_new_vocabulary(self) -> None:
        """The whole point: these two used to be indistinguishable."""
        assert BridgeState.ONLINE.value != BridgeState.DEGRADED.value
        assert LEGACY_STATUS_FOR_STATE[BridgeState.ONLINE.value] == "connected"
        assert LEGACY_STATUS_FOR_STATE[BridgeState.DEGRADED.value] == "error"


class TestCoercion:
    def test_reads_a_known_state(self) -> None:
        assert coerce_state("ONLINE") is BridgeState.ONLINE

    def test_reads_a_legacy_value(self) -> None:
        assert coerce_state("connected") is BridgeState.ONLINE
        assert coerce_state("error") is BridgeState.DEGRADED

    @pytest.mark.parametrize("raw", [None, "", "   ", "SOMETHING_NEW", 42, object()])
    def test_unknown_never_raises(self, raw: object) -> None:
        """A newer build's value must not take down a dashboard render."""
        assert coerce_state(raw) is BridgeState.DISCONNECTED

    def test_is_case_insensitive(self) -> None:
        assert coerce_state("online") is BridgeState.ONLINE


class _FakeRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def set_state(self, connection_id: str, state: str, **kw: Any) -> dict[str, Any]:
        self.calls.append({"connection_id": connection_id, "state": state, **kw})
        return {"connection_id": connection_id, "state": state}


class TestPersist:
    def test_passing_the_legacy_value_keeps_the_repo_sql_only(self) -> None:
        """The repo must never import the bridge domain to derive the mapping."""
        repo = _FakeRepo()
        persist_state(repo, "c1", BridgeState.REAUTH_REQUIRED, detail="token revoked")

        call = repo.calls[0]
        assert call["state"] == "REAUTH_REQUIRED"
        assert call["legacy_status"] == "error"
        assert call["detail"] == "token revoked"

    def test_every_state_persists_with_its_mapped_legacy_value(self) -> None:
        repo = _FakeRepo()
        for state in BridgeState:
            persist_state(repo, "c1", state)
        assert [c["legacy_status"] for c in repo.calls] == [
            LEGACY_STATUS_FOR_STATE[s.value] for s in BridgeState
        ]

    def test_forwarded_optional_fields(self) -> None:
        repo = _FakeRepo()
        persist_state(
            repo,
            "c1",
            BridgeState.ONLINE,
            peer_protocol="1.0",
            peer_instance_id="inst-a",
        )
        call = repo.calls[0]
        assert call["peer_protocol"] == "1.0"
        assert call["peer_instance_id"] == "inst-a"
