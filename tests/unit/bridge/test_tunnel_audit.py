"""Per-request audit binding for tunneled HTTP (plan phase 15).

The load-bearing property: a peer refuses a tunneled request that carries no
accountable actor. Anything else means this instance performed a write it
cannot attribute to anybody.
"""

from __future__ import annotations

import pytest

from octop.infra.bridge.audit import (
    DEFAULT_MAX_RESPONSE_BYTES,
    TunnelAudit,
    audit_from_frame,
    deadline_remaining,
    enforce_response_budget,
    new_audit,
)
from octop.infra.errors import ErrorCode, OctopError

NOW = 1_700_000_000


def _frame(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "req-1",
        "request_id": "req-1",
        "owner_user_id": 7,
        "deadline_at": NOW + 120,
        "max_response_bytes": DEFAULT_MAX_RESPONSE_BYTES,
    }
    base.update(kw)
    return base


class TestNewAudit:
    def test_carries_the_hops_identity(self) -> None:
        audit = new_audit(owner_user_id=7, now=NOW, remote_agent_id="ag1", hub_instance_id="inst-a")
        assert audit.owner_user_id == 7
        assert audit.deadline_at == NOW + 120
        assert audit.request_id
        assert audit.as_frame_fields()["hub_instance_id"] == "inst-a"

    def test_max_response_is_capped_to_a_frame(self) -> None:
        audit = new_audit(owner_user_id=7, now=NOW, max_response_bytes=10**12)
        assert audit.max_response_bytes <= 32 * 1024 * 1024

    def test_describe_is_log_safe(self) -> None:
        audit = new_audit(owner_user_id=7, now=NOW, remote_agent_id="ag1")
        line = audit.describe()
        assert "owner_user_id=7" in line
        assert "request_id=" in line
        assert "Bearer" not in line


class TestAuditFromFrame:
    def test_reads_a_well_formed_frame(self) -> None:
        audit = audit_from_frame(_frame(remote_agent_id="ag1"))
        assert audit.request_id == "req-1"
        assert audit.owner_user_id == 7
        assert audit.remote_agent_id == "ag1"

    def test_falls_back_to_the_frame_id(self) -> None:
        frame = _frame()
        frame.pop("request_id")
        assert audit_from_frame(frame).request_id == "req-1"

    @pytest.mark.parametrize(
        "frame",
        [
            _frame(owner_user_id=None),
            _frame(owner_user_id="7"),
            _frame(owner_user_id=True),
            _frame(owner_user_id=0),
            _frame(owner_user_id=-1),
            _frame(owner_user_id=10**20),
            _frame(deadline_at=0),
            _frame(deadline_at="soon"),
            _frame(max_response_bytes=0),
            _frame(max_response_bytes=10**12),
        ],
        ids=[
            "owner-none",
            "owner-string",
            "owner-bool",
            "owner-zero",
            "owner-negative",
            "owner-too-big",
            "deadline-zero",
            "deadline-string",
            "max-zero",
            "max-over-frame",
        ],
    )
    def test_a_malformed_block_is_refused(self, frame: dict[str, object]) -> None:
        with pytest.raises(OctopError) as excinfo:
            audit_from_frame(frame)
        assert excinfo.value.code is ErrorCode.BRIDGE_REMOTE_UNSUPPORTED

    def test_a_frame_with_no_id_at_all_is_refused(self) -> None:
        frame = _frame()
        frame.pop("id")
        frame.pop("request_id")
        with pytest.raises(OctopError):
            audit_from_frame(frame)

    def test_an_old_hub_without_the_block_is_refused(self) -> None:
        """A pre-audit peer still works; a *new* peer will not accept it.

        The point is that the peer is explicit rather than guessing.
        """
        with pytest.raises(OctopError):
            audit_from_frame({"id": "req-1", "method": "GET", "path": "/api/agents"})


class TestDeadline:
    def test_counts_down(self) -> None:
        assert deadline_remaining(NOW + 30, now=NOW) == 30

    def test_never_goes_negative(self) -> None:
        assert deadline_remaining(NOW - 5, now=NOW) == 0


class TestResponseBudget:
    def test_a_body_within_budget_passes(self) -> None:
        assert enforce_response_budget(b"x" * 100, max_response_bytes=100) == b"x" * 100

    def test_an_oversized_body_is_refused_not_truncated(self) -> None:
        """Truncating silently would leave the caller unable to tell."""
        with pytest.raises(OctopError) as excinfo:
            enforce_response_budget(b"x" * 101, max_response_bytes=100)
        assert excinfo.value.code is ErrorCode.BRIDGE_REMOTE_UNSUPPORTED
        assert excinfo.value.details["size"] == 101


class TestAuditIsImmutable:
    def test_the_block_cannot_be_rewritten_after_construction(self) -> None:
        audit = TunnelAudit(request_id="r", owner_user_id=1, deadline_at=2, max_response_bytes=3)
        with pytest.raises(Exception):
            audit.owner_user_id = 2  # type: ignore[misc]
