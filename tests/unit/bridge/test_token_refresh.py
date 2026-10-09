"""Token refresh policy (plan phase 14).

The behaviours under test are the ones that were actually wrong before: a
concurrent burst used to produce one login per caller, and a failed login used
to be indistinguishable from a revoked credential.
"""

from __future__ import annotations

import asyncio

import pytest

from octop.infra.bridge.token_refresh import (
    REFRESH_SAFETY_MARGIN_SECONDS,
    RefreshFailure,
    TokenRefresher,
    classify_login_failure,
    refresh_delay_seconds,
    should_refresh,
)


class TestFailureClassification:
    def test_unreachable_is_transient(self) -> None:
        assert classify_login_failure(None) is RefreshFailure.TRANSIENT

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_credentials_never_retry(self, status: int) -> None:
        failure = classify_login_failure(status)
        assert failure is RefreshFailure.CREDENTIAL_REVOKED
        assert failure.retryable is False

    def test_server_error_is_transient(self) -> None:
        assert classify_login_failure(500).retryable is True
        assert classify_login_failure(503).retryable is True

    def test_client_error_is_a_protocol_problem(self) -> None:
        failure = classify_login_failure(426)  # Upgrade Required
        assert failure is RefreshFailure.PROTOCOL_OR_SERVER
        assert failure.retryable is True

    def test_three_kinds_are_distinguishable(self) -> None:
        kinds = {
            classify_login_failure(None),
            classify_login_failure(401),
            classify_login_failure(400),
        }
        assert len(kinds) == 3


class TestShouldRefresh:
    def test_far_from_expiry_is_kept(self) -> None:
        assert should_refresh(expires_at=10_000, now=0) is False

    def test_inside_the_margin_is_replaced(self) -> None:
        assert should_refresh(expires_at=100, now=0) is True

    def test_already_expired_is_replaced(self) -> None:
        assert should_refresh(expires_at=-5, now=0) is True

    def test_unknown_expiry_is_kept(self) -> None:
        """No recorded expiry is not grounds for a login on every reconnect.

        Some peers issue non-expiring tokens; refreshing each time would be a
        login storm against the user's own instance. The peer decides validity,
        and a rejected token is dropped by the caller.
        """
        assert should_refresh(expires_at=None, now=0) is False

    def test_boundary_is_the_margin(self) -> None:
        assert should_refresh(expires_at=REFRESH_SAFETY_MARGIN_SECONDS, now=0) is True
        assert should_refresh(expires_at=REFRESH_SAFETY_MARGIN_SECONDS + 1, now=0) is False


class TestJitter:
    def test_no_delay_inside_the_margin(self) -> None:
        assert refresh_delay_seconds(expires_at=100, now=0) == 0.0

    def test_delay_is_the_remaining_window(self) -> None:
        delay = refresh_delay_seconds(expires_at=10_000, now=0, jitter_fraction=0)
        assert delay == 10_000 - REFRESH_SAFETY_MARGIN_SECONDS

    def test_jitter_keeps_the_mean_and_spreads_instances(self) -> None:
        """A fleet refreshing on the same offset is the problem being fixed."""
        samples = [
            refresh_delay_seconds(expires_at=100_000, now=0, jitter_fraction=0.1)
            for _ in range(400)
        ]
        base = 100_000 - REFRESH_SAFETY_MARGIN_SECONDS
        mean = sum(samples) / len(samples)
        assert mean == pytest.approx(base, rel=0.05)
        assert len(set(samples)) > 100, "jitter must actually spread the values"
        assert min(samples) >= base * 0.9 - 1

    def test_jitter_can_be_disabled(self) -> None:
        a = refresh_delay_seconds(expires_at=100_000, now=0, jitter_fraction=0)
        b = refresh_delay_seconds(expires_at=100_000, now=0, jitter_fraction=0)
        assert a == b


class TestSingleFlight:
    async def test_a_burst_of_callers_logs_in_once(self) -> None:
        """The peer restarting must not produce N logins at N links."""
        calls = 0
        gate = asyncio.Event()

        async def login() -> str:
            nonlocal calls
            calls += 1
            await gate.wait()
            return "tok"

        refresher = TokenRefresher(login)
        waiters = [asyncio.ensure_future(refresher.acquire("c1")) for _ in range(5)]
        await asyncio.sleep(0)
        gate.set()
        tokens = await asyncio.gather(*waiters)

        assert tokens == ["tok"] * 5
        assert calls == 1

    async def test_different_connections_do_not_block_each_other(self) -> None:
        """Two healthy links to one peer must not serialise behind one login."""
        slow_started = asyncio.Event()
        release_slow = asyncio.Event()

        async def login_for(name: str, *, slow: bool):
            if slow:
                slow_started.set()
                await release_slow.wait()
            return f"tok-{name}"

        refresher = TokenRefresher(lambda: login_for("a", slow=True))
        slow = asyncio.ensure_future(refresher.acquire("c1"))
        await slow_started.wait()

        fast_calls = 0

        async def fast_login() -> str:
            nonlocal fast_calls
            fast_calls += 1
            return "tok-b"

        refresher._login = fast_login  # noqa: SLF001
        assert await asyncio.wait_for(refresher.acquire("c2"), timeout=1) == "tok-b"
        assert fast_calls == 1

        release_slow.set()
        assert await slow == "tok-a"

    async def test_a_successful_token_is_cached(self) -> None:
        calls = 0

        async def login() -> str:
            nonlocal calls
            calls += 1
            return "tok"

        refresher = TokenRefresher(login)
        await refresher.acquire("c1")
        await refresher.acquire("c1")
        assert calls == 1
        assert refresher.cached("c1") == "tok"

    async def test_a_failed_login_is_not_cached(self) -> None:
        """Otherwise a transient outage would poison the connection for good."""
        calls = 0

        async def login() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("peer down")
            return "tok"

        refresher = TokenRefresher(login)
        with pytest.raises(RuntimeError):
            await refresher.acquire("c1")
        assert refresher.cached("c1") is None
        assert await refresher.acquire("c1") == "tok"
        assert calls == 2

    async def test_a_concurrent_failure_reaches_every_caller(self) -> None:
        calls = 0

        async def login() -> str:
            nonlocal calls
            calls += 1
            raise RuntimeError("revoked")

        refresher = TokenRefresher(login)
        results = await asyncio.gather(
            *(refresher.acquire("c1") for _ in range(3)), return_exceptions=True
        )
        assert all(isinstance(r, RuntimeError) for r in results)
        assert calls == 1, "a shared failure must not fan out into N logins"

    async def test_drop_forgets_the_token(self) -> None:
        calls = 0

        async def login() -> str:
            nonlocal calls
            calls += 1
            return f"tok{calls}"

        refresher = TokenRefresher(login)
        await refresher.acquire("c1")
        refresher.drop("c1")
        assert refresher.cached("c1") is None
        assert await refresher.acquire("c1") == "tok2"

    async def test_seed_adopts_a_token_without_logging_in(self) -> None:
        calls = 0

        async def login() -> str:
            nonlocal calls
            calls += 1
            return "from-login"

        refresher = TokenRefresher(login)
        refresher.seed("c1", "from-probe")
        assert await refresher.acquire("c1") == "from-probe"
        assert calls == 0
