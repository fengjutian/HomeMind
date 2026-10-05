"""HTTP approval, transaction, and audit surface for HomeMind."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

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


class _RowModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TransactionResponse(_RowModel):
    id: str
    family_id: str
    requested_by: int
    action: str
    payload_json: str
    status: str
    result_json: str | None
    error: str | None
    created_at: int
    updated_at: int


class ApprovalResponse(_RowModel):
    id: str
    transaction_id: str
    family_id: str
    status: str
    requested_by: int
    decided_by: int | None
    reason: str | None
    created_at: int
    decided_at: int | None


class ApprovalDecisionBody(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class AuditResponse(_RowModel):
    id: str
    family_id: str
    user_id: int
    transaction_id: str | None
    action: str
    target: str | None
    result: str
    approval: str | None
    detail_json: str
    created_at: int


def _manager(server: OctopServer) -> FamilyTransactionManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    family = FamilyManager(services.family_repo)
    return FamilyTransactionManager(
        family,
        FamilyContextManager(family, services.family_context_repo),
        FamilyTaskManager(family, services.family_task_repo),
        services.family_transaction_repo,
        server.services.user_repo,
        FamilyFilesystemManager(
            family,
            services.family_asset_repo,
            services.family_transaction_repo,
        ),
    )


@router.get(
    "/{family_id}/approvals",
    response_model=list[ApprovalResponse],
    summary="List family approvals",
)
async def list_approvals(
    family_id: str,
    server: Server,
    user: CurrentUser,
    status: Literal["PENDING", "APPROVED", "REJECTED"] | None = Query(default="PENDING"),
) -> object:
    return _manager(server).list_approvals(family_id, user, status)


@router.post(
    "/{family_id}/approvals/{approval_id}/approve",
    response_model=TransactionResponse,
    summary="Approve and execute a family transaction",
)
async def approve(
    family_id: str,
    approval_id: str,
    body: ApprovalDecisionBody,
    server: Server,
    user: CurrentUser,
) -> object:
    return _manager(server).approve(family_id, approval_id, user, body.reason)


@router.post(
    "/{family_id}/approvals/{approval_id}/reject",
    response_model=TransactionResponse,
    summary="Reject a family transaction",
)
async def reject(
    family_id: str,
    approval_id: str,
    body: ApprovalDecisionBody,
    server: Server,
    user: CurrentUser,
) -> object:
    return _manager(server).reject(family_id, approval_id, user, body.reason)


@router.get(
    "/{family_id}/transactions/{transaction_id}",
    response_model=TransactionResponse,
    summary="Get a family transaction",
)
async def get_transaction(
    family_id: str, transaction_id: str, server: Server, user: CurrentUser
) -> object:
    return _manager(server).get_transaction(family_id, transaction_id, user)


@router.get(
    "/{family_id}/audit-log",
    response_model=list[AuditResponse],
    summary="List family agent audit records",
)
async def list_audit(family_id: str, server: Server, user: CurrentUser) -> object:
    return _manager(server).list_audit(family_id, user)
