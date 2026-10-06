"""Runtime client surface for stage 6 device pairing and command dispatch.

The runtime client (``homemind_runtime`` long-lived process) authenticates
with the bearer token issued during pairing. Unlike family-scoped routes,
no dashboard JWT is involved; the token's hash is looked up directly
against :class:`homemind.infra.db.repos.family_devices.FamilyDeviceRepo`.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.repos.family_devices import (
    FamilyDeviceCommandRow,
    FamilyDeviceRow,
)
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.device_runtime import (
    DeviceRuntimeManager,
)
from homemind.infra.family.manager import FamilyManager
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from octop.api.deps import get_server
from octop.infra.server import OctopServer

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]


class _RowModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RuntimeDeviceResponse(_RowModel):
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


class RuntimePairBody(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=200)


class RuntimePairResponse(BaseModel):
    device: RuntimeDeviceResponse
    token: str
    expires_at: int


class RuntimeHeartbeatBody(BaseModel):
    address: str | None = Field(default=None, max_length=200)
    status: str = Field(default="ONLINE", pattern="^(ONLINE|OFFLINE|BUSY|ERROR)$")


class RuntimeHeartbeatResponse(BaseModel):
    device: RuntimeDeviceResponse


class RuntimeCommandResponse(BaseModel):
    id: str
    device_id: str
    family_id: str
    capability: str
    payload: dict[str, Any]
    expires_at: int
    transaction_id: str | None


class RuntimeNoCommandResponse(BaseModel):
    command: None


class RuntimeCommandResultBody(BaseModel):
    status: str = Field(pattern="^(SUCCEEDED|FAILED|RUNNING)$")
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=2000)


def _manager(server: OctopServer) -> DeviceRuntimeManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    return DeviceRuntimeManager(
        FamilyManager(services.family_repo), services.family_device_repo,
    )


def _device_response(row: FamilyDeviceRow) -> RuntimeDeviceResponse:
    return RuntimeDeviceResponse(
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


def _command_payload(row: FamilyDeviceCommandRow) -> RuntimeCommandResponse:
    return RuntimeCommandResponse(
        id=row.id,
        device_id=row.device_id,
        family_id=row.family_id,
        capability=row.capability,
        payload=json.loads(row.payload_json),
        expires_at=row.expires_at,
        transaction_id=row.transaction_id,
    )


def _resolve_token(authorization: str | None) -> str:
    if not authorization:
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID, "missing authorization header",
        )
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID, "authorization must be Bearer <token>",
        )
    return value


@router.post(
    "/runtime/pair",
    response_model=RuntimePairResponse,
    status_code=201,
    summary="Exchange a pairing code for a long-lived credential",
    description=(
        "Called by the runtime client after the family admin handed it a "
        "pairing code. Returns the freshly-created device row plus the "
        "plaintext bearer token; the token is shown exactly once."
    ),
)
async def pair(body: RuntimePairBody, server: Server) -> RuntimePairResponse:
    device, token, expires_at = _manager(server).complete_pairing(
        body.code, address=body.address,
    )
    return RuntimePairResponse(
        device=_device_response(device),
        token=token,
        expires_at=expires_at,
    )


@router.post(
    "/runtime/heartbeat",
    response_model=RuntimeHeartbeatResponse,
    summary="Refresh device online state from the runtime",
)
async def heartbeat(
    body: RuntimeHeartbeatBody,
    server: Server,
    authorization: Annotated[str | None, Header()] = None,
) -> RuntimeHeartbeatResponse:
    token = _resolve_token(authorization)
    device = _manager(server).heartbeat(
        token, address=body.address, status=body.status,
    )
    return RuntimeHeartbeatResponse(device=_device_response(device))


@router.get(
    "/runtime/commands",
    response_model=RuntimeCommandResponse | RuntimeNoCommandResponse,
    summary="Fetch the next pending command for this runtime",
)
async def next_command(
    server: Server,
    authorization: Annotated[str | None, Header()] = None,
) -> RuntimeCommandResponse | RuntimeNoCommandResponse:
    token = _resolve_token(authorization)
    manager = _manager(server)
    device = manager.heartbeat(token)
    command = manager.next_pending_command(device.id)
    if command is None:
        return RuntimeNoCommandResponse(command=None)
    return _command_payload(command)


@router.post(
    "/runtime/commands/{command_id}/result",
    status_code=204,
    summary="Report the outcome of a dispatched command",
)
async def report_result(
    command_id: str,
    body: RuntimeCommandResultBody,
    server: Server,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    token = _resolve_token(authorization)
    manager = _manager(server)
    # Confirm the token still authenticates the same device before we
    # let the runtime mutate command state — otherwise an ex-device
    # could still report success on a stale command_id it once owned.
    manager.heartbeat(token)
    updated = manager.report_command_result(
        command_id,
        status=body.status,
        result=body.result,
        error=body.error,
    )
    if updated is None:
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID,
            "command is not in a claimable state",
        )
    return Response(status_code=204)
