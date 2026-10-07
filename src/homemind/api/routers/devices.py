"""HTTP adapters for the family-scoped side of stage 6 device pairing.

Family admins use these endpoints to list, register, rotate / revoke
credentials, push commands, and remove family devices. The runtime
client surface lives in :mod:`homemind.api.routers.runtime` and uses
bearer-token auth instead of the dashboard JWT.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.repos.family_devices import (
    FamilyDeviceCommandRow,
    FamilyDeviceRow,
)
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.device_runtime import (
    DeviceRuntimeManager,
    PairingCode,
)
from homemind.infra.family.events import (
    EVENT_APPROVAL_CREATED,
    EVENT_APPROVAL_DECIDED,
    EVENT_JOB_PROGRESS,
    emit_family_event,
)
from homemind.infra.family.manager import FamilyManager
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()

Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


async def emit_command_event(
    server: OctopServer,
    family_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Push a device-command event to the family's dashboards.

    The bus lives on ``HomeMindServer`` and is absent in a plain Octop
    deployment, so a device write must not depend on it.
    """

    bus = getattr(server, "family_event_bus", None)
    await emit_family_event(bus, event_type, family_id, payload)


class _RowModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class FamilyDeviceResponse(_RowModel):
    id: str
    family_id: str
    name: str
    device_type: str
    platform: str | None
    status: str
    address: str | None
    capabilities: list[str]
    last_seen: int | None
    created_at: int
    updated_at: int
    root_path: str | None = None
    runtime_version: str | None = None


class FamilyDeviceCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    device_type: str = Field(min_length=1, max_length=50)
    platform: str | None = Field(default=None, max_length=50)
    capabilities: list[str] = Field(
        default_factory=list,
        description="Capabilities the runtime client should advertise.",
    )


class FamilyDeviceUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    platform: str | None = Field(default=None, max_length=50)
    capabilities: list[str] | None = None


class FamilyDevicePairingResponse(BaseModel):
    code: str
    expires_at: int
    device_name: str
    device_type: str


class FamilyDeviceRotateResponse(BaseModel):
    token: str


class FamilyDeviceCommandResponse(_RowModel):
    id: str
    family_id: str
    device_id: str
    capability: str
    payload: dict[str, Any]
    requested_by: int
    transaction_id: str | None
    expires_at: int
    status: str
    result: dict[str, Any] | None
    error: str | None
    created_at: int
    updated_at: int


class FamilyDeviceCommandCreateBody(BaseModel):
    capability: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    expires_in_seconds: int = Field(
        default=300, ge=1, le=86400,
        description="Seconds from now until the command expires.",
    )
    transaction_id: str | None = Field(default=None, max_length=100)


def _manager(server: OctopServer) -> DeviceRuntimeManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    return DeviceRuntimeManager(
        FamilyManager(services.family_repo), services.family_device_repo,
    )


def _device_response(row: FamilyDeviceRow) -> FamilyDeviceResponse:
    return FamilyDeviceResponse(
        id=row.id,
        family_id=row.family_id,
        name=row.name,
        device_type=row.device_type,
        platform=row.platform,
        status=row.status,
        address=row.address,
        capabilities=list(row.capabilities),
        last_seen=row.last_seen,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _command_response(row: FamilyDeviceCommandRow) -> FamilyDeviceCommandResponse:
    payload = json.loads(row.payload_json)
    result = json.loads(row.result_json) if row.result_json else None
    return FamilyDeviceCommandResponse(
        id=row.id,
        family_id=row.family_id,
        device_id=row.device_id,
        capability=row.capability,
        payload=payload,
        requested_by=row.requested_by,
        transaction_id=row.transaction_id,
        expires_at=row.expires_at,
        status=row.status,
        result=result,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/{family_id}/devices",
    response_model=list[FamilyDeviceResponse],
    summary="List family devices",
)
async def list_devices(family_id: str, server: Server, user: CurrentUser) -> list[FamilyDeviceResponse]:
    rows = _manager(server).list_devices(family_id, user)
    return [_device_response(row) for row in rows]


@router.get(
    "/{family_id}/devices/{device_id}",
    response_model=FamilyDeviceResponse,
    summary="Get a family device",
)
async def get_device(
    family_id: str, device_id: str, server: Server, user: CurrentUser,
) -> FamilyDeviceResponse:
    return _device_response(_manager(server).get_device(family_id, device_id, user))


@router.post(
    "/{family_id}/devices",
    response_model=FamilyDevicePairingResponse,
    status_code=201,
    summary="Create a pairing code for a new device",
    description=(
        "Returns a short-lived pairing code that the runtime client "
        "exchanges via ``POST /api/homemind/runtime/pair`` for a "
        "long-lived credential. The actual device row is created by "
        "the runtime so address and capabilities match reality."
    ),
)
async def create_pairing(
    family_id: str,
    body: FamilyDeviceCreateBody,
    server: Server,
    user: CurrentUser,
) -> FamilyDevicePairingResponse:
    pairing: PairingCode = _manager(server).create_pairing_code(
        family_id,
        user,
        device_name=body.name,
        device_type=body.device_type,
        platform=body.platform,
        capabilities=body.capabilities,
    )
    return FamilyDevicePairingResponse(
        code=pairing.code,
        expires_at=pairing.expires_at,
        device_name=body.name,
        device_type=body.device_type,
    )


@router.patch(
    "/{family_id}/devices/{device_id}",
    response_model=FamilyDeviceResponse,
    summary="Update a family device",
)
async def update_device(
    family_id: str,
    device_id: str,
    body: FamilyDeviceUpdateBody,
    server: Server,
    user: CurrentUser,
) -> FamilyDeviceResponse:
    row = _manager(server).update_device(
        family_id, device_id, user,
        name=body.name,
        platform=body.platform,
        capabilities=body.capabilities,
    )
    return _device_response(row)


@router.delete(
    "/{family_id}/devices/{device_id}",
    status_code=204,
    summary="Delete a family device",
)
async def delete_device(
    family_id: str, device_id: str, server: Server, user: CurrentUser,
) -> Response:
    _manager(server).delete_device(family_id, device_id, user)
    return Response(status_code=204)


@router.post(
    "/{family_id}/devices/{device_id}/rotate-token",
    response_model=FamilyDeviceRotateResponse,
    summary="Rotate the bearer token for a device",
    description=(
        "Revokes all existing credentials and returns a fresh token. "
        "The plaintext token is shown exactly once — store it on the "
        "runtime client immediately."
    ),
)
async def rotate_token(
    family_id: str, device_id: str, server: Server, user: CurrentUser,
) -> FamilyDeviceRotateResponse:
    token = _manager(server).rotate_token(family_id, device_id, user)
    return FamilyDeviceRotateResponse(token=token)


@router.post(
    "/{family_id}/devices/{device_id}/revoke-token",
    status_code=204,
    summary="Revoke all active credentials for a device",
)
async def revoke_token(
    family_id: str, device_id: str, server: Server, user: CurrentUser,
) -> Response:
    _manager(server).revoke_token(family_id, device_id, user)
    return Response(status_code=204)


@router.post(
    "/{family_id}/devices/{device_id}/commands",
    response_model=FamilyDeviceCommandResponse,
    status_code=201,
    summary="Enqueue a command for a device",
    description=(
        "Validates family access, the device's declared capability, and "
        "path confinement. Unsafe capabilities (delete / move / rename / "
        "reboot) land in ``WAITING_APPROVAL`` and are invisible to the "
        "runtime until a manager approves them."
    ),
)
async def enqueue_command(
    family_id: str,
    device_id: str,
    body: FamilyDeviceCommandCreateBody,
    server: Server,
    user: CurrentUser,
) -> FamilyDeviceCommandResponse:
    expires_at = int(time.time()) + body.expires_in_seconds
    row = _manager(server).enqueue_command(
        family_id,
        device_id,
        capability=body.capability,
        payload=body.payload,
        requested_by=user.id,
        expires_at=expires_at,
        transaction_id=body.transaction_id,
        user=user,
    )
    await emit_command_event(
        server,
        family_id,
        EVENT_APPROVAL_CREATED if row.status == "WAITING_APPROVAL" else EVENT_JOB_PROGRESS,
        {
            "command_id": row.id,
            "capability": row.capability,
            "status": row.status,
            "is_unsafe": row.is_unsafe,
        },
    )
    return _command_response(row)


@router.post(
    "/{family_id}/devices/commands/{command_id}/approve",
    response_model=FamilyDeviceCommandResponse,
    summary="Approve an approval-gated device command",
    description="Manager-only. The runtime has no route into this action.",
)
async def approve_command(
    family_id: str,
    command_id: str,
    server: Server,
    user: CurrentUser,
) -> FamilyDeviceCommandResponse:
    row = _manager(server).approve_command(family_id, command_id, user)
    await emit_command_event(
        server,
        family_id,
        EVENT_APPROVAL_DECIDED,
        {"command_id": row.id, "status": row.status, "decision": "APPROVED"},
    )
    return _command_response(row)


@router.post(
    "/{family_id}/devices/commands/{command_id}/cancel",
    response_model=FamilyDeviceCommandResponse,
    summary="Cancel a device command that has not finished",
)
async def cancel_command(
    family_id: str,
    command_id: str,
    server: Server,
    user: CurrentUser,
) -> FamilyDeviceCommandResponse:
    row = _manager(server).cancel_command(family_id, command_id, user)
    await emit_command_event(
        server,
        family_id,
        EVENT_APPROVAL_DECIDED,
        {"command_id": row.id, "status": row.status, "decision": "CANCELLED"},
    )
    return _command_response(row)


@router.get(
    "/{family_id}/devices/{device_id}/commands",
    response_model=list[FamilyDeviceCommandResponse],
    summary="List recent commands for a device",
)
async def list_commands(
    family_id: str,
    device_id: str,
    server: Server,
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[FamilyDeviceCommandResponse]:
    rows = _manager(server).list_recent_commands(
        family_id, device_id, user, limit=limit,
    )
    return [_command_response(row) for row in rows]
