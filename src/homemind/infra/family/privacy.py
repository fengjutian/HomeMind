"""External provider boundary for family data (Stage 12).

The question this module answers is narrow and important: *may this
family send this piece of data to this external provider?* Every
outbound call — vision, embedding, geocoding — must pass through
:func:`authorize_external_call` first.

The checks run in order, cheapest and most decisive first:

1. **Privacy mode.** ``LOCAL_ONLY`` blocks everything, unconditionally.
2. **Operation gate.** A family that disabled external vision cannot
   get a vision call even in ``ALLOW_EXTERNAL`` mode.
3. **Provider allow-list.** An empty list means *none*, not *all*.
4. **Sensitivity.** Medical / identity / minor data needs an explicit
   opt-in on top of everything above.
5. **Permission.** The caller must already be allowed to read the
   asset — the provider is not a way around the space boundary.

Only when all five pass does the caller proceed. When the family is in
``ASK_EACH_TIME``, a passing check still produces a *pending request*
rather than a silent send, so a human gets the decision.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.permissions import (
    FamilyPermissionEvaluator,
    PermissionEffect,
)
from homemind.infra.metrics import inc as _hm_inc
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)


class ProcessingMode(StrEnum):
    """How willing a family is to let data leave the house."""

    LOCAL_ONLY = "LOCAL_ONLY"
    ASK_EACH_TIME = "ASK_EACH_TIME"
    ALLOW_EXTERNAL = "ALLOW_EXTERNAL"


class ExternalOperation(StrEnum):
    VISION = "VISION"
    EMBEDDING = "EMBEDDING"
    GEOCODING = "GEOCODING"


class DataCategory(StrEnum):
    """What kind of data a payload carries.

    ``SENSITIVE_*`` categories require an explicit family opt-in on
    top of the ordinary gates.
    """

    PHOTO = "PHOTO"
    FACE = "FACE"
    GPS = "GPS"
    HEALTH = "SENSITIVE_HEALTH"
    FINANCIAL = "SENSITIVE_FINANCIAL"
    IDENTITY = "SENSITIVE_IDENTITY"
    MINOR = "SENSITIVE_MINOR"
    DOCUMENT = "DOCUMENT"
    VOICE = "VOICE"
    GENERAL = "GENERAL"


SENSITIVE_CATEGORIES: frozenset[str] = frozenset(
    {
        DataCategory.HEALTH.value,
        DataCategory.FINANCIAL.value,
        DataCategory.IDENTITY.value,
        DataCategory.MINOR.value,
    }
)


@dataclass(frozen=True)
class PrivacySettings:
    """A family's external-processing policy."""

    family_id: str
    processing_mode: ProcessingMode = ProcessingMode.ASK_EACH_TIME
    allow_external_vision: bool = False
    allow_external_embedding: bool = False
    allow_external_geocoding: bool = False
    allow_sensitive_external: bool = False
    allowed_provider_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AuthorizationDecision:
    """Outcome of :func:`authorize_external_call`."""

    allowed: bool
    reason: str
    needs_approval: bool = False
    request_id: str | None = None

    def require(self) -> None:
        """Raise unless the call may proceed."""
        if self.allowed and not self.needs_approval:
            return
        if self.allowed and self.needs_approval:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "external processing needs an explicit approval",
            )
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID,
            f"external processing denied: {self.reason}",
        )


class ExternalProcessingGuard:
    """Decides whether family data may leave the house."""

    def __init__(self, services: HomeMindServices) -> None:
        self._services = services
        self._family = FamilyManager(services.family_repo)
        self._permissions = FamilyPermissionEvaluator(services.family_repo)

    # ------------------------------------------------------------- settings

    def get_settings(self, family_id: str) -> PrivacySettings:
        with self._services.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_privacy_settings WHERE family_id = ?",
                (family_id,),
            ).fetchone()
        if row is None:
            # No row means the family never configured a policy. The
            # default is deliberately the cautious one.
            return PrivacySettings(family_id=family_id)
        return PrivacySettings(
            family_id=family_id,
            processing_mode=ProcessingMode(str(row["processing_mode"])),
            allow_external_vision=bool(row["allow_external_vision"]),
            allow_external_embedding=bool(row["allow_external_embedding"]),
            allow_external_geocoding=bool(row["allow_external_geocoding"]),
            allow_sensitive_external=bool(row["allow_sensitive_external"]),
            allowed_provider_ids=frozenset(
                json.loads(str(row["allowed_provider_ids"]))
            ),
        )

    def update_settings(
        self,
        family_id: str,
        user: User,
        *,
        processing_mode: ProcessingMode | None = None,
        allow_external_vision: bool | None = None,
        allow_external_embedding: bool | None = None,
        allow_external_geocoding: bool | None = None,
        allow_sensitive_external: bool | None = None,
        allowed_provider_ids: list[str] | None = None,
    ) -> PrivacySettings:
        """Manager-only. ``LOCAL_ONLY`` clears the allow-list so a
        family cannot be surprised by a stale provider entry if they
        later switch modes back."""

        self._family.require_manager(family_id, user)
        current = self.get_settings(family_id)
        mode = processing_mode or current.processing_mode
        providers = (
            frozenset(allowed_provider_ids)
            if allowed_provider_ids is not None
            else current.allowed_provider_ids
        )
        if mode is ProcessingMode.LOCAL_ONLY:
            providers = frozenset()
        values: dict[str, Any] = {
            "family_id": family_id,
            "processing_mode": mode.value,
            "allow_external_vision": _flag(
                allow_external_vision, current.allow_external_vision,
            ),
            "allow_external_embedding": _flag(
                allow_external_embedding, current.allow_external_embedding,
            ),
            "allow_external_geocoding": _flag(
                allow_external_geocoding, current.allow_external_geocoding,
            ),
            "allow_sensitive_external": _flag(
                allow_sensitive_external, current.allow_sensitive_external,
            ),
            "allowed_provider_ids": json.dumps(
                sorted(providers), ensure_ascii=False,
            ),
            "updated_at": int(time.time()),
            "updated_by": user.id,
        }
        fields = ", ".join(f"{key} = ?" for key in values if key != "family_id")
        with self._services.db.transaction() as conn:
            existing = conn.execute(
                "SELECT family_id FROM homemind_family_privacy_settings "
                "WHERE family_id = ?",
                (family_id,),
            ).fetchone()
            if existing is None:
                columns = ", ".join(values)
                placeholders = ", ".join("?" for _ in values)
                conn.execute(
                    f"INSERT INTO homemind_family_privacy_settings"
                    f"({columns}) VALUES ({placeholders})",
                    list(values.values()),
                )
            else:
                conn.execute(
                    f"UPDATE homemind_family_privacy_settings SET {fields} "
                    "WHERE family_id = ?",
                    [*[v for k, v in values.items() if k != "family_id"], family_id],
                )
        return self.get_settings(family_id)

    # ---------------------------------------------------------- authorize

    def authorize_external_call(
        self,
        family_id: str,
        user: User,
        *,
        operation: ExternalOperation,
        provider_id: str,
        data_categories: frozenset[str] = frozenset({DataCategory.GENERAL.value}),
        asset_id: str | None = None,
        model: str = "",
    ) -> AuthorizationDecision:
        """Run every gate in order and report the first that blocks."""

        settings = self.get_settings(family_id)

        # 1. Coarse switch.
        if settings.processing_mode is ProcessingMode.LOCAL_ONLY:
            _hm_inc("external_processing_denied_total")
            return AuthorizationDecision(
                allowed=False,
                reason="family is in LOCAL_ONLY mode",
            )

        # 2. Per-operation gate.
        if not _operation_allowed(settings, operation):
            _hm_inc("external_processing_denied_total")
            return AuthorizationDecision(
                allowed=False,
                reason=f"{operation.value} is disabled for this family",
            )

        # 3. Provider allow-list. Empty means none.
        if provider_id not in settings.allowed_provider_ids:
            _hm_inc("external_processing_denied_total")
            return AuthorizationDecision(
                allowed=False,
                reason=f"provider {provider_id!r} is not on the allow-list",
            )

        # 4. Sensitivity.
        if data_categories & SENSITIVE_CATEGORIES and not settings.allow_sensitive_external:
            _hm_inc("external_processing_denied_total")
            return AuthorizationDecision(
                allowed=False,
                reason="sensitive data cannot be sent externally",
            )

        # 5. The caller must already be allowed to read the asset.
        if asset_id is not None and not self._can_read_asset(family_id, user, asset_id):
            _hm_inc("external_processing_denied_total")
            return AuthorizationDecision(
                allowed=False,
                reason="caller may not read this asset",
            )

        # Everything passed. In ASK_EACH_TIME the call still needs a
        # human decision rather than going out silently.
        if settings.processing_mode is ProcessingMode.ASK_EACH_TIME:
            request_id = self._create_request(
                family_id,
                user,
                operation=operation,
                provider_id=provider_id,
                data_categories=data_categories,
                asset_id=asset_id,
                model=model,
            )
            return AuthorizationDecision(
                allowed=True,
                reason="awaiting approval",
                needs_approval=True,
                request_id=request_id,
            )
        return AuthorizationDecision(allowed=True, reason="allowed")

    # ------------------------------------------------------------ requests

    def list_requests(
        self, family_id: str, user: User, *, status: str | None = None,
    ) -> list[dict[str, Any]]:
        self._family.require_manager(family_id, user)
        clauses = ["family_id = ?"]
        params: list[Any] = [family_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        with self._services.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_external_processing_requests "
                f"WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT 200",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def decide_request(
        self, family_id: str, request_id: str, user: User, *, approve: bool,
    ) -> dict[str, Any]:
        """Manager approves or rejects a pending outbound request."""

        self._family.require_manager(family_id, user)
        with self._services.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_external_processing_requests "
                "WHERE request_id = ? AND family_id = ?",
                (request_id, family_id),
            ).fetchone()
        if row is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "external request not found",
            )
        status = "APPROVED" if approve else "REJECTED"
        with self._services.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_external_processing_requests "
                "SET status = ?, decided_by = ?, decided_at = ? "
                "WHERE request_id = ? AND status = 'PENDING'",
                (status, user.id, int(time.time()), request_id),
            )
            if cursor.rowcount != 1:
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_CONFLICT,
                    "external request is already decided",
                )
        _hm_inc(
            "external_request_approved_total"
            if approve
            else "external_request_rejected_total",
        )
        return {"request_id": request_id, "status": status}

    # -------------------------------------------------------------- helpers

    def _create_request(
        self,
        family_id: str,
        user: User,
        *,
        operation: ExternalOperation,
        provider_id: str,
        data_categories: frozenset[str],
        asset_id: str | None,
        model: str,
    ) -> str:
        from octop.infra.utils.ulid import new_ulid  # noqa: PLC0415

        request_id = new_ulid()
        with self._services.db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_external_processing_requests("
                "request_id, family_id, asset_id, user_id, operation, provider_id, "
                "model, data_categories, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)",
                (
                    request_id,
                    family_id,
                    asset_id,
                    user.id,
                    operation.value,
                    provider_id,
                    model,
                    json.dumps(sorted(data_categories), ensure_ascii=False),
                    int(time.time()),
                ),
            )
        _hm_inc("external_request_created_total")
        return request_id

    def _can_read_asset(self, family_id: str, user: User, asset_id: str) -> bool:
        asset = self._services.family_asset_repo.get(asset_id)
        if asset is None or asset.family_id != family_id:
            return False
        decision = self._permissions.evaluate(
            family_id=family_id,
            user=user,
            action="asset.read",
            asset=asset,
        )
        return decision.effect is PermissionEffect.ALLOW

    def audit_call(
        self,
        family_id: str,
        user: User,
        *,
        operation: ExternalOperation,
        provider_id: str,
        asset_id: str | None,
        data_categories: frozenset[str],
        result: dict[str, Any],
        model: str = "",
    ) -> None:
        """Record that data actually left.

        The row records *that* and *what kind* — never the payload, the
        original file path, or any credential.
        """

        with self._services.db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_external_processing_audit("
                "audit_id, family_id, user_id, asset_id, provider_id, model, "
                "operation, data_categories, result, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _audit_id(),
                    family_id,
                    user.id,
                    asset_id,
                    provider_id,
                    model,
                    operation.value,
                    json.dumps(sorted(data_categories), ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
                    int(time.time()),
                ),
            )


def _operation_allowed(settings: PrivacySettings, operation: ExternalOperation) -> bool:
    if operation is ExternalOperation.VISION:
        return settings.allow_external_vision
    if operation is ExternalOperation.EMBEDDING:
        return settings.allow_external_embedding
    return settings.allow_external_geocoding


def _flag(value: bool | None, fallback: bool) -> bool:
    return fallback if value is None else bool(value)


def _audit_id() -> str:
    from octop.infra.utils.ulid import new_ulid  # noqa: PLC0415

    return new_ulid()


__all__ = [
    "AuthorizationDecision",
    "DataCategory",
    "ExternalOperation",
    "ExternalProcessingGuard",
    "PrivacySettings",
    "ProcessingMode",
    "SENSITIVE_CATEGORIES",
]
