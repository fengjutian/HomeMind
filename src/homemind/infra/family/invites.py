"""Stage 10: family invites + redemption flow.

Managers mint a single-use token; the redeemer exchanges it for a
``FamilyMemberRow`` bound to their ``User.id``. Tokens are stored only
as hashes so a database dump never leaks a usable invite.

V0.1 simplification: only the redeeming user can use a token (no
forwarding to other accounts). Multi-process deployment does not need
a separate backing store because redemption is atomic on the row.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

from homemind.infra.db.repos.family_invites import FamilyInviteRepo, FamilyInviteRow
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.metrics import inc as _hm_inc
from octop.infra.users.identity import User

DEFAULT_INVITE_TTL_SECONDS = 7 * 24 * 3600  # one week
MIN_INVITE_TTL_SECONDS = 60
MAX_INVITE_TTL_SECONDS = 30 * 24 * 3600


def hash_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_invite_token(length_bytes: int = 24) -> str:
    return secrets.token_urlsafe(length_bytes)


def clamp_invite_ttl(seconds: int) -> int:
    return max(MIN_INVITE_TTL_SECONDS, min(int(seconds), MAX_INVITE_TTL_SECONDS))


@dataclass(frozen=True)
class InviteToken:
    """Returned to the manager — plaintext is shown once, never persisted."""

    invite_id: str
    token: str
    expires_at: int


@dataclass(frozen=True)
class RedeemResult:
    member_id: str
    family_id: str
    role: str
    display_name: str


class FamilyInviteManager:
    def __init__(
        self,
        family: FamilyManager,
        repo: FamilyInviteRepo,
    ) -> None:
        self.family = family
        self.repo = repo

    # ----------------------------------------------------------- manager-side

    def create_invite(
        self,
        family_id: str,
        user: User,
        *,
        display_name: str,
        role: MemberRole = MemberRole.MEMBER,
        ttl_seconds: int = DEFAULT_INVITE_TTL_SECONDS,
    ) -> InviteToken:
        self.family.require_manager(family_id, user)
        if not display_name.strip():
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "display name is required for invite",
            )
        token = mint_invite_token()
        expires_at = int(time.time()) + clamp_invite_ttl(ttl_seconds)
        row = self.repo.create(
            family_id,
            role=str(role),
            display_name=display_name,
            token_hash=hash_invite_token(token),
            created_by=user.id,
            expires_at=expires_at,
        )
        _hm_inc("invite_mint_total")
        return InviteToken(invite_id=row.id, token=token, expires_at=expires_at)

    def list_invites(
        self,
        family_id: str,
        user: User,
        *,
        include_redeemed: bool = False,
    ) -> list[FamilyInviteRow]:
        self.family.require_manager(family_id, user)
        return self.repo.list_for_family(
            family_id, include_redeemed=include_redeemed,
        )

    def revoke_invite(
        self,
        family_id: str,
        invite_id: str,
        user: User,
    ) -> bool:
        self.family.require_manager(family_id, user)
        row = self.repo.get(invite_id)
        if row is None or row.family_id != family_id:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "invite not found",
            )
        revoked = self.repo.revoke(invite_id)
        if revoked:
            _hm_inc("invite_revoke_total")
        return revoked

    # ---------------------------------------------------------- public redeem

    def redeem(self, token: str, user: User) -> RedeemResult:
        row = self.repo.get_by_token_hash(hash_invite_token(token))
        if row is None:
            _hm_inc("invite_rejected_total")
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "invite token is invalid",
            )
        if row.redeemed_at is not None:
            _hm_inc("invite_rejected_total")
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "invite has already been redeemed",
            )
        if row.expires_at < int(time.time()):
            _hm_inc("invite_rejected_total")
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "invite has expired",
            )
        # ``create_member`` enforces ``require_manager`` which a brand
        # new redeemer does not have. The invite IS the authorization,
        # so go straight through the repo via a manager-only helper.
        member = self.family.create_member_for_invite(
            row.family_id,
            display_name=row.display_name,
            role=MemberRole(row.role),
            user_id=user.id,
        )
        self.repo.mark_redeemed(row.id, user.id)
        _hm_inc("invite_redeem_total")
        return RedeemResult(
            member_id=member.id,
            family_id=row.family_id,
            role=row.role,
            display_name=row.display_name,
        )


__all__ = [
    "DEFAULT_INVITE_TTL_SECONDS",
    "FamilyInviteManager",
    "InviteToken",
    "MAX_INVITE_TTL_SECONDS",
    "MIN_INVITE_TTL_SECONDS",
    "RedeemResult",
    "clamp_invite_ttl",
    "hash_invite_token",
    "mint_invite_token",
]
