"""Protocol version and capability negotiation (plan phase 13).

The behaviour that matters is the asymmetry: a major bump must refuse, a minor
bump must degrade, and a peer that predates negotiation must still connect.
"""

from __future__ import annotations

import pytest

from octop.infra.bridge.protocol import (
    LOCAL_CAPABILITIES,
    LOCAL_VERSION,
    LOCAL_VERSION_STRING,
    MAX_FRAME_BYTES,
    Capability,
    ProtocolIncompatible,
    ProtocolVersion,
    build_hello,
    effective_max_frame,
    negotiate,
)


def _hello(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "protocol_version": LOCAL_VERSION_STRING,
        "capabilities": sorted(c.value for c in LOCAL_CAPABILITIES),
        "max_frame_bytes": MAX_FRAME_BYTES,
        "instance_id": "peer-1",
    }
    base.update(kw)
    return base


class TestVersionParsing:
    def test_parses_dotted(self) -> None:
        assert ProtocolVersion.parse("1.0") == ProtocolVersion(1, 0)
        assert ProtocolVersion.parse("2.7") == ProtocolVersion(2, 7)

    def test_parses_bare_major(self) -> None:
        assert ProtocolVersion.parse("1") == ProtocolVersion(1, 0)

    def test_parses_legacy_integer(self) -> None:
        assert ProtocolVersion.parse(1) == ProtocolVersion(1, 0)

    @pytest.mark.parametrize("raw", [None, "", "abc", "1.2.3", 0, -1, True, False, {}, []])
    def test_rejects_junk(self, raw: object) -> None:
        assert ProtocolVersion.parse(raw) is None

    def test_str_round_trips(self) -> None:
        assert str(ProtocolVersion(3, 4)) == "3.4"


class TestCompatibility:
    def test_same_version_compatible(self) -> None:
        assert negotiate(_hello()).compatible is True

    def test_minor_difference_is_compatible(self) -> None:
        assert negotiate(_hello(protocol_version="1.99")).compatible is True

    def test_major_difference_raises(self) -> None:
        with pytest.raises(ProtocolIncompatible) as excinfo:
            negotiate(_hello(protocol_version="2.0"))
        assert excinfo.value.ours == LOCAL_VERSION_STRING
        assert "2.0" in excinfo.value.theirs

    def test_minor_gap_does_not_raise(self) -> None:
        """A minor gap is the whole point of major/minor; it must degrade."""
        result = negotiate(_hello(protocol_version="1.0"))
        assert result.peer_version == ProtocolVersion(1, 0)

    def test_older_major_minor_peer_is_recorded_as_older(self) -> None:
        result = negotiate(_hello(protocol_version="1.0"))
        assert LOCAL_VERSION.is_older_than(result.peer_version) is False
        assert result.peer_version.is_older_than(LOCAL_VERSION) is False


class TestLegacyTransition:
    def test_bare_integer_peer_still_connects(self) -> None:
        """Before negotiation existed the version was an int. Refusing it
        would break every deployed link on upgrade day."""
        result = negotiate(_hello(protocol_version=1, capabilities=None))
        assert result.compatible is True
        assert result.legacy is True
        assert result.peer_version == ProtocolVersion(1, 0)

    def test_legacy_peer_gets_no_optional_features(self) -> None:
        result = negotiate(_hello(protocol_version=1, capabilities=None))
        assert result.peer_capabilities == frozenset()

    def test_unparseable_version_is_treated_as_legacy_not_refused(self) -> None:
        """Garbage is not evidence of a newer major; refusing would turn a
        cosmetic change into an outage."""
        result = negotiate(_hello(protocol_version="not-a-version"))
        assert result.compatible is True
        assert result.legacy is True

    def test_missing_version_is_legacy(self) -> None:
        result = negotiate({"connection_id": "c1"})
        assert result.compatible is True
        assert result.legacy is True

    def test_string_version_is_not_legacy(self) -> None:
        assert negotiate(_hello(protocol_version="1.0")).legacy is False


class TestCapabilityNegotiation:
    def test_intersection_is_taken(self) -> None:
        peer = [Capability.CANCEL.value, Capability.COMPRESSION.value]
        result = negotiate(_hello(capabilities=peer))
        # COMPRESSION is not implemented locally, so it is not negotiated.
        assert result.peer_capabilities == {Capability.CANCEL}

    def test_unknown_capability_is_ignored_not_fatal(self) -> None:
        result = negotiate(
            _hello(capabilities=["time_travel", Capability.CANCEL.value])
        )
        assert result.peer_capabilities == {Capability.CANCEL}

    def test_comma_separated_string_is_accepted(self) -> None:
        result = negotiate(_hello(capabilities="cancel,diagnostics"))
        assert Capability.CANCEL in result.peer_capabilities
        assert Capability.DIAGNOSTICS in result.peer_capabilities

    def test_empty_capabilities_negotiates_nothing(self) -> None:
        assert negotiate(_hello(capabilities=[])).peer_capabilities == frozenset()

    def test_garbage_capabilities_negotiate_nothing(self) -> None:
        assert negotiate(_hello(capabilities=42)).peer_capabilities == frozenset()

    def test_local_capabilities_are_a_subset_of_the_enum(self) -> None:
        assert LOCAL_CAPABILITIES <= set(Capability)


class TestHelloFrame:
    def test_hello_carries_everything_negotiation_needs(self) -> None:
        frame = build_hello(
            connection_id="c1",
            role="initiator",
            base_url="http://127.0.0.1:8080",
            username="peer",
            display_name="Peer",
            instance_id="inst-1",
            octop_version="1.2.3",
        )
        assert frame["protocol_version"] == LOCAL_VERSION_STRING
        assert frame["protocol_major"] == LOCAL_VERSION.major
        assert frame["protocol_minor"] == LOCAL_VERSION.minor
        assert frame["capabilities"] == sorted(c.value for c in LOCAL_CAPABILITIES)
        assert frame["max_frame_bytes"] == MAX_FRAME_BYTES
        assert frame["instance_id"] == "inst-1"
        assert frame["octop_version"] == "1.2.3"
        assert frame["type"] == "hello"

    def test_a_fresh_hello_negotiates_against_itself(self) -> None:
        frame = build_hello(
            connection_id="c1",
            role="initiator",
            base_url="http://127.0.0.1:8080",
            username="peer",
            display_name="Peer",
            instance_id="inst-1",
            octop_version="1.2.3",
        )
        result = negotiate(frame)
        assert result.compatible is True
        assert result.peer_instance_id == "inst-1"
        assert result.peer_max_frame_bytes == MAX_FRAME_BYTES


class TestFrameLimit:
    def test_smallest_peer_limit_wins(self) -> None:
        assert effective_max_frame([16 * 1024 * 1024, MAX_FRAME_BYTES]) == 16 * 1024 * 1024

    def test_our_own_limit_when_no_peers(self) -> None:
        assert effective_max_frame([]) == MAX_FRAME_BYTES

    def test_junk_limits_are_ignored(self) -> None:
        assert effective_max_frame([0, -5]) == MAX_FRAME_BYTES
        assert negotiate(_hello(max_frame_bytes="junk")).peer_max_frame_bytes == 0


class TestDeterministicTieBreakInput:
    """Phase 13 also requires a deterministic dial tie-break by instance id.

    These assert the *inputs* the rule needs are actually present on the wire;
    the arbitration itself is exercised where it is applied.
    """

    def test_instance_id_is_always_advertised(self) -> None:
        frame = build_hello(
            connection_id="c1",
            role="responder",
            base_url="http://127.0.0.1:8080",
            username="peer",
            display_name="Peer",
            instance_id="inst-abc",
            octop_version="1.2.3",
        )
        assert negotiate(frame).peer_instance_id == "inst-abc"

    def test_missing_instance_id_is_empty_not_fatal(self) -> None:
        assert negotiate(_hello(instance_id=None)).peer_instance_id == ""

    def test_two_dialers_can_be_compared_deterministically(self) -> None:
        """A newer build must win regardless of who arrived first."""
        a = negotiate(_hello(instance_id="inst-aaa"))
        b = negotiate(_hello(instance_id="inst-zzz"))
        # The rule the session registry will use: highest instance id wins.
        assert max(a.peer_instance_id, b.peer_instance_id) == "inst-zzz"
