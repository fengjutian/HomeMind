"""Stage 12: in-memory metrics counters for HomeMind.

Mirrors octop's ``octop.infra.metrics`` pattern but keeps the
HomeMind counters separate so the control-plane snapshot does not get
polluted with product-internal events.

Counters cover:

- permission decisions (allow / deny / require_confirmation / force_confirm)
- transaction lifecycle (plan / approve / reject / cancel / retry / recover)
- memory candidate lifecycle (create / approve / reject / merge)
- asset scan job (sweep / sources_scanned / failures)
- device runtime (heartbeat / rotate / revoke / commands enqueued)
- invite lifecycle (mint / redeem / revoke)

The singleton is intentionally thread-safe; callers should not assume
cross-process visibility (use the dashboard for that).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class HomeMindMetrics:
    # permissions
    permission_allow_total: int = 0
    permission_deny_total: int = 0
    permission_require_confirmation_total: int = 0
    permission_force_confirmation_total: int = 0

    # transactions
    transaction_plan_total: int = 0
    transaction_approve_total: int = 0
    transaction_reject_total: int = 0
    transaction_cancel_total: int = 0
    transaction_retry_total: int = 0
    transaction_recover_total: int = 0
    transaction_idempotent_replay_total: int = 0
    transaction_failed_requires_review_total: int = 0
    transaction_approval_expired_total: int = 0

    # memory candidates
    memory_candidate_create_total: int = 0
    memory_candidate_approve_total: int = 0
    memory_candidate_reject_total: int = 0
    memory_candidate_merge_total: int = 0
    memory_candidate_sensitive_flagged_total: int = 0
    # post-turn extractor (Stage 3)
    memory_post_turn_candidates_created_total: int = 0
    memory_post_turn_extractor_error_total: int = 0
    memory_post_turn_lifecycle_error_total: int = 0
    memory_post_turn_sensitive_skip_total: int = 0
    memory_post_turn_low_confidence_skip_total: int = 0
    # daily maintenance runner (Stage 3)
    memory_decay_total: int = 0
    memory_expiration_total: int = 0
    memory_dedup_groups_total: int = 0

    # asset scan job
    asset_scan_sweep_total: int = 0
    asset_scan_sources_total: int = 0
    asset_scan_failures_total: int = 0

    # device runtime
    device_heartbeat_total: int = 0
    device_rotate_token_total: int = 0
    device_revoke_token_total: int = 0
    device_command_enqueued_total: int = 0
    device_command_succeeded_total: int = 0
    device_command_failed_total: int = 0

    # invites
    invite_mint_total: int = 0
    invite_redeem_total: int = 0
    invite_revoke_total: int = 0
    invite_rejected_total: int = 0  # expired / already-used / bad token

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def inc(self, name: str, n: int = 1) -> None:
        if not hasattr(self, name):
            raise AttributeError(f"unknown HomeMind metric: {name!r}")
        with self._lock:
            setattr(self, name, getattr(self, name) + n)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                key: getattr(self, key)
                for key in self.__dataclass_fields__  # type: ignore[attr-defined]
                if not key.startswith("_")
            }

    def reset(self) -> None:
        """Clear all counters. Test-only convenience."""
        with self._lock:
            for key in self.__dataclass_fields__:  # type: ignore[attr-defined]
                if key.startswith("_"):
                    continue
                setattr(self, key, 0)


METRICS = HomeMindMetrics()


def inc(name: str, n: int = 1) -> None:
    """Shorthand for the most common call site."""
    METRICS.inc(name, n)


__all__ = ["METRICS", "HomeMindMetrics", "inc"]
