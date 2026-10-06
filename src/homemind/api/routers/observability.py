"""Stage 12: in-process HomeMind metrics endpoint.

Returns the current ``HomeMindMetrics`` snapshot to admin users so the
dashboard can show how the family plane is being used. Counter names
mirror those in :mod:`homemind.infra.metrics`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.metrics import METRICS
from octop.api.deps import current_user
from octop.infra.users.identity import User

router = APIRouter()

CurrentUser = Annotated[User, Depends(current_user)]


@router.get(
    "/metrics",
    summary="Snapshot HomeMind in-process counters (admin only)",
    description=(
        "Read-only view of the in-process counter registry. The "
        "counters cover permission decisions, transaction "
        "lifecycle, memory candidate flow, asset scan job, device "
        "runtime, and invites."
    ),
)
async def snapshot(user: CurrentUser) -> dict[str, int]:
    if not user.is_admin:
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID,
            "metrics endpoint requires admin role",
        )
    return METRICS.snapshot()
