"""HTTP adapters for boundary-safe family filesystem operations."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator

from homemind.api.routers.transactions import ApprovalResponse, TransactionResponse
from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.filesystem import FamilyFilesystemManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transactions import FamilyTransactionManager
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class FilesystemEntryResponse(BaseModel):
    path: str
    kind: str
    size_bytes: int | None


class FilesystemReadResponse(BaseModel):
    path: str
    content: str


class FilesystemMutationBody(BaseModel):
    action: Literal[
        "filesystem.copy",
        "filesystem.move",
        "filesystem.rename",
        "filesystem.delete",
    ]
    source_id: str
    path: str = Field(min_length=1, max_length=4096)
    destination: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_destination(self) -> FilesystemMutationBody:
        if self.action != "filesystem.delete" and self.destination is None:
            raise ValueError("destination is required for copy, move, and rename")
        return self


class FilesystemMutationResponse(BaseModel):
    transaction: TransactionResponse
    approval: ApprovalResponse | None


def _services(server: OctopServer) -> HomeMindServices:
    assert server.services is not None
    run_migrations(server.services.db)
    return HomeMindServices.from_pool(server.services.db)


def _filesystem(server: OctopServer) -> FamilyFilesystemManager:
    services = _services(server)
    return FamilyFilesystemManager(
        FamilyManager(services.family_repo),
        services.family_asset_repo,
        services.family_transaction_repo,
    )


def _transactions(server: OctopServer) -> FamilyTransactionManager:
    assert server.services is not None
    services = _services(server)
    family = FamilyManager(services.family_repo)
    filesystem = FamilyFilesystemManager(
        family, services.family_asset_repo, services.family_transaction_repo
    )
    return FamilyTransactionManager(
        family,
        FamilyContextManager(family, services.family_context_repo),
        FamilyTaskManager(family, services.family_task_repo),
        services.family_transaction_repo,
        server.services.user_repo,
        filesystem,
    )


@router.get(
    "/{family_id}/filesystem",
    response_model=list[FilesystemEntryResponse],
    summary="List a registered family directory",
)
async def list_directory(
    family_id: str,
    server: Server,
    user: CurrentUser,
    source_id: str = Query(),
    path: str = Query(default="."),
) -> object:
    call = partial(
        _filesystem(server).list,
        family_id,
        user,
        source_id=source_id,
        path=path,
    )
    return await asyncio.to_thread(call)


@router.get(
    "/{family_id}/filesystem/search",
    response_model=list[FilesystemEntryResponse],
    summary="Search within a registered family directory",
)
async def search_directory(
    family_id: str,
    server: Server,
    user: CurrentUser,
    source_id: str = Query(),
    query: str = Query(min_length=1, max_length=200),
    path: str = Query(default="."),
    limit: int = Query(default=100, ge=1, le=500),
) -> object:
    call = partial(
        _filesystem(server).search,
        family_id,
        user,
        source_id=source_id,
        query=query,
        path=path,
        limit=limit,
    )
    return await asyncio.to_thread(call)


@router.get(
    "/{family_id}/filesystem/read",
    response_model=FilesystemReadResponse,
    summary="Read a UTF-8 file from a registered family directory",
)
async def read_file(
    family_id: str,
    server: Server,
    user: CurrentUser,
    source_id: str = Query(),
    path: str = Query(min_length=1, max_length=4096),
    max_bytes: int = Query(default=1024 * 1024, ge=1, le=4 * 1024 * 1024),
) -> FilesystemReadResponse:
    call = partial(
        _filesystem(server).read,
        family_id,
        user,
        source_id=source_id,
        path=path,
        max_bytes=max_bytes,
    )
    content = await asyncio.to_thread(call)
    return FilesystemReadResponse(path=path, content=content)


@router.post(
    "/{family_id}/filesystem/actions",
    response_model=FilesystemMutationResponse,
    summary="Plan a permission-gated family file operation",
    description=(
        "Copy may execute immediately when allowed. Move, rename, and delete "
        "always require confirmation and use verified, recoverable operations."
    ),
)
async def mutate_file(
    family_id: str,
    body: FilesystemMutationBody,
    server: Server,
    user: CurrentUser,
) -> FilesystemMutationResponse:
    payload: dict[str, object] = {
        "source_id": body.source_id,
        "path": body.path,
    }
    if body.destination is not None:
        payload["destination"] = body.destination
    transaction, approval = _transactions(server).plan(
        family_id, user, action=body.action, payload=payload
    )
    return FilesystemMutationResponse(
        transaction=TransactionResponse.model_validate(transaction),
        approval=(ApprovalResponse.model_validate(approval) if approval is not None else None),
    )
