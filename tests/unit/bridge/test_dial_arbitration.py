"""Deterministic dial arbitration (plan phase 13).

The property that matters: two instances, each holding only the two instance
ids, must reach the *same* verdict. Anything that depends on arrival order
fails that test — which is exactly what last-writer-wins did.
"""

from __future__ import annotations

import pytest

from octop.infra.bridge.manager import DialYielded
from octop.infra.bridge.protocol import arbitrate_dial


class TestArbitrationRule:
    def test_higher_instance_id_wins(self) -> None:
        verdict = arbitrate_dial(incumbent="inst-aaa", challenger="inst-zzz")
        assert verdict.winner == "inst-zzz"
        assert verdict.loser == "inst-aaa"
        assert verdict.reason == "higher-instance-id"

    def test_lower_instance_id_keeps_the_connection(self) -> None:
        verdict = arbitrate_dial(incumbent="inst-zzz", challenger="inst-aaa")
        assert verdict.winner == "inst-zzz"
        assert verdict.loser == "inst-aaa"

    def test_a_legacy_peer_without_an_id_never_displaces_us(self) -> None:
        verdict = arbitrate_dial(incumbent="inst-aaa", challenger="")
        assert verdict.winner == "inst-aaa"
        assert verdict.reason == "incumbent-keeps"

    def test_same_id_is_idempotent(self) -> None:
        verdict = arbitrate_dial(incumbent="inst-aaa", challenger="inst-aaa")
        assert verdict.winner == "inst-aaa"
        assert verdict.reason == "incumbent-keeps"

    def test_we_win_when_we_are_the_only_one_with_an_id(self) -> None:
        verdict = arbitrate_dial(incumbent="inst-aaa", challenger="")
        assert verdict.winner == "inst-aaa"


class TestSymmetry:
    """Both sides must reach the same conclusion from the same two ids."""

    @pytest.mark.parametrize(
        "id_a,id_b",
        [("inst-aaa", "inst-zzz"), ("inst-zzz", "inst-aaa"), ("a", "b"), ("01", "02")],
    )
    def test_both_sides_agree_on_the_winner(self, id_a: str, id_b: str) -> None:
        """A sees itself as incumbent; B sees itself as challenger.

        They are different processes with different roles, and must still
        agree — otherwise one will keep a socket the other has closed.
        """
        from_a = arbitrate_dial(incumbent=id_a, challenger=id_b)
        from_b = arbitrate_dial(incumbent=id_b, challenger=id_a)
        assert from_a.winner == from_b.winner
        assert {from_a.winner, from_a.loser} == {id_a, id_b}

    def test_order_does_not_change_the_outcome(self) -> None:
        """The whole point: no packet crossing may flip the result."""
        results = {
            arbitrate_dial(incumbent="inst-aaa", challenger="inst-zzz").winner,
            arbitrate_dial(incumbent="inst-zzz", challenger="inst-aaa").winner,
            arbitrate_dial(incumbent="inst-zzz", challenger="inst-aaa").winner,
        }
        assert results == {"inst-zzz"}

    def test_repeated_arbitration_is_stable(self) -> None:
        first = arbitrate_dial(incumbent="inst-a", challenger="inst-b")
        for _ in range(5):
            again = arbitrate_dial(incumbent="inst-a", challenger="inst-b")
            assert again.winner == first.winner


class TestDialYielded:
    def test_carries_the_winner_so_the_caller_can_log_it(self) -> None:
        err = DialYielded("cid1", "inst-zzz")
        assert err.connection_id == "cid1"
        assert err.winner == "inst-zzz"
        assert "inst-zzz" in str(err)
