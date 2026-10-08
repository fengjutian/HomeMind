"""Authorization-header parsing for the HomeMind HTTP surface.

Two schemes, two meanings, and they must never substitute for one
another:

``Bearer <token>``
    A device's long-lived credential. Identifies the *device*, so it may
    only be accepted by control-plane routes.

``Transfer <token>``
    A short-lived data-plane credential bound to one transfer. It carries
    no device identity and grants nothing but byte reads.

A dashboard user JWT is neither. The data plane deliberately rejects
``Bearer``: a long-lived credential pasted into a download URL would stay
valid long after the job it was minted for.
"""

from __future__ import annotations

from homemind.infra.errors import HomeMindError, HomeMindErrorCode


def bearer_token(authorization: str | None) -> str:
    """Extract a device credential from ``Authorization: Bearer <token>``."""
    if not authorization:
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID,
            "missing authorization header",
        )
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID,
            "authorization must be Bearer <token>",
        )
    return value


def transfer_token(authorization: str | None) -> str:
    """Extract a data-plane credential from ``Transfer <token>``.

    A ``Bearer`` header here is rejected rather than downgraded: the
    device credential is long-lived, and letting it stand in for a
    per-transfer one would make every download URL a permanent key to the
    family library.
    """
    if not authorization:
        raise HomeMindError(
            HomeMindErrorCode.ASSET_TRANSFER_TOKEN_INVALID,
            "missing authorization header",
        )
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "transfer" or not value:
        raise HomeMindError(
            HomeMindErrorCode.ASSET_TRANSFER_TOKEN_INVALID,
            "authorization must be Transfer <short-lived-token>",
        )
    return value


__all__ = ["bearer_token", "transfer_token"]
