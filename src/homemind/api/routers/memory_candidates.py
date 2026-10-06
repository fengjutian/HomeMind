"""HTTP adapters for memory candidates and per-memory evidence/archive.

All approve/reject/merge endpoints require manager access; the
list / get endpoints accept any family member. Approval writes a new
``FamilyMemoryRow`` and anchors existing evidence to the new id;
rejection marks the candidate as REJECTED; merge folds the evidence
into an existing memory without duplicating content.

Cross-family access is blocked at the lookup level — the route
re-loads the candidate and verifies ``family_id`` matches the path
parameter so an attacker with a guessed candidate id cannot inspect
or operate on another family's data.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.repos.memory_candidates import MemoryCandidateRow, MemoryEvidenceRow
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.memory_lifecycle import MemoryLifecycleManager
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class _RowModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MemoryCandidateResponse(_RowModel):
    id: str
    family_id: str
    subject_type: str
    subject_id: str | None
    content: str
    memory_type: str
    importance: float
    confidence: float
    visibility: str
    source_type: str
    source_id: str | None
    status: str
    created_by: int
    created_at: int
    reviewed_by: int | None
    reviewed_at: int | None
    rejection_reason: str | None
    merged_into: str | None


class MemoryEvidenceResponse(_RowModel):
    id: str
    candidate_id: str
    memory_id: str | None
    source_type: str
    source_id: str | None
    content_hash: str
    observed_at: int
    confidence_delta: float


class MemoryCandidateApproveResponse(_RowModel):
    candidate: MemoryCandidateResponse
    memory_id: str


class MemoryCandidateRejectBody(BaseModel):
    reason: str | None = Field(default=None, max_length=400)


class MemoryCandidateMergeBody(BaseModel):
    target_memory_id: str = Field(min_length=1, max_length=128)


def _services(server: OctopServer) -> HomeMindServices:
    assert server.services is not None
    run_migrations(server.services.db)
    return HomeMindServices.from_pool(server.services.db)


def _family(srv: HomeMindServices) -> FamilyManager:
    return FamilyManager(srv.family_repo)


def _lifecycle(srv: HomeMindServices) -> MemoryLifecycleManager:
    family = _family(srv)
    from homemind.infra.family.context import FamilyContextManager

    context = FamilyContextManager(
        family,
        srv.family_context_repo,
        asset_repo=srv.family_asset_repo,
    )
    return MemoryLifecycleManager(
        family,
        context,
        srv.memory_candidate_repo,
        srv.memory_evidence_repo,
    )


def _candidate_response(row: MemoryCandidateRow) -> MemoryCandidateResponse:
    return MemoryCandidateResponse(
        id=row.id,
        family_id=row.family_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        content=row.content,
        memory_type=row.memory_type,
        importance=row.importance,
        confidence=row.confidence,
        visibility=row.visibility,
        source_type=row.source_type,
        source_id=row.source_id,
        status=row.status,
        created_by=row.created_by,
        created_at=row.created_at,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        rejection_reason=row.rejection_reason,
        merged_into=row.merged_into,
    )


def _evidence_response(row: MemoryEvidenceRow) -> MemoryEvidenceResponse:
    return MemoryEvidenceResponse(
        id=row.id,
        candidate_id=row.candidate_id,
        memory_id=row.memory_id,
        source_type=row.source_type,
        source_id=row.source_id,
        content_hash=row.content_hash,
        observed_at=row.observed_at,
        confidence_delta=row.confidence_delta,
    )


def _load_candidate(
    lifecycle: MemoryLifecycleManager, family_id: str, candidate_id: str
) -> MemoryCandidateRow:
    """Load a candidate and refuse cross-family access."""
    candidate = lifecycle.candidates.get(candidate_id)
    if candidate is None or candidate.family_id != family_id:
        raise OctopError(ErrorCode.NOT_FOUND, "memory candidate not found")
    return candidate


@router.get(
    "/{family_id}/memory-candidates",
    response_model=list[MemoryCandidateResponse],
    summary="List memory candidates in a family",
    description=(
        "Returns candidates filtered by status. Family members can "
        "list candidates they have permission to see; private-space "
        "candidates stay invisible to anyone but their owner."
    ),
)
async def list_memory_candidates(
    family_id: str,
    server: Server,
    user: CurrentUser,
    status: str | None = Query(default=None, description="PENDING / APPROVED / REJECTED / MERGED / EXPIRED"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[MemoryCandidateResponse]:
    services = _services(server)
    lifecycle = _lifecycle(services)
    family = _family(services)
    family.require_access(family_id, user)
    permissions = FamilyPermissionEvaluator(services.family_repo)
    rows = lifecycle.candidates.list_for_family(family_id, status=status, limit=limit)
    visible: list[MemoryCandidateResponse] = []
    for row in rows:
        if not permissions.can_see_candidate(family_id, user, row):
            continue
        visible.append(_candidate_response(row))
    return visible


@router.get(
    "/{family_id}/memory-candidates/{candidate_id}",
    response_model=MemoryCandidateResponse,
    summary="Fetch a single memory candidate",
)
async def get_memory_candidate(
    family_id: str, candidate_id: str, server: Server, user: CurrentUser,
) -> MemoryCandidateResponse:
    services = _services(server)
    lifecycle = _lifecycle(services)
    family = _family(services)
    family.require_access(family_id, user)
    candidate = _load_candidate(lifecycle, family_id, candidate_id)
    permissions = FamilyPermissionEvaluator(services.family_repo)
    if not permissions.can_see_candidate(family_id, user, candidate):
        raise OctopError(ErrorCode.FORBIDDEN, "candidate not visible")
    return _candidate_response(candidate)


@router.post(
    "/{family_id}/memory-candidates/{candidate_id}/approve",
    response_model=MemoryCandidateApproveResponse,
    summary="Approve a memory candidate and promote it to a real memory",
)
async def approve_memory_candidate(
    family_id: str, candidate_id: str, server: Server, user: CurrentUser,
) -> MemoryCandidateApproveResponse:
    lifecycle = _lifecycle(_services(server))
    family = _family(_services(server))
    family.require_manager(family_id, user)
    _load_candidate(lifecycle, family_id, candidate_id)
    updated, memory = lifecycle.approve_candidate(
        family_id, candidate_id, reviewer=user,
    )
    return MemoryCandidateApproveResponse(
        candidate=_candidate_response(updated),
        memory_id=memory.id,
    )


@router.post(
    "/{family_id}/memory-candidates/{candidate_id}/reject",
    response_model=MemoryCandidateResponse,
    summary="Reject a memory candidate",
)
async def reject_memory_candidate(
    family_id: str,
    candidate_id: str,
    body: MemoryCandidateRejectBody,
    server: Server,
    user: CurrentUser,
) -> MemoryCandidateResponse:
    lifecycle = _lifecycle(_services(server))
    family = _family(_services(server))
    family.require_manager(family_id, user)
    _load_candidate(lifecycle, family_id, candidate_id)
    updated = lifecycle.reject_candidate(
        family_id, candidate_id, reviewer=user, reason=body.reason,
    )
    return _candidate_response(updated)


@router.post(
    "/{family_id}/memory-candidates/{candidate_id}/merge",
    response_model=MemoryCandidateApproveResponse,
    summary="Merge a candidate into an existing memory",
)
async def merge_memory_candidate(
    family_id: str,
    candidate_id: str,
    body: MemoryCandidateMergeBody,
    server: Server,
    user: CurrentUser,
) -> MemoryCandidateApproveResponse:
    lifecycle = _lifecycle(_services(server))
    family = _family(_services(server))
    family.require_manager(family_id, user)
    _load_candidate(lifecycle, family_id, candidate_id)
    updated, target = lifecycle.merge_candidate(
        family_id, candidate_id, body.target_memory_id, reviewer=user,
    )
    return MemoryCandidateApproveResponse(
        candidate=_candidate_response(updated),
        memory_id=target.id,
    )


@router.get(
    "/{family_id}/memories/{memory_id}/evidence",
    response_model=list[MemoryEvidenceResponse],
    summary="List evidence rows that produced or reinforced a memory",
)
async def list_memory_evidence(
    family_id: str, memory_id: str, server: Server, user: CurrentUser,
) -> list[MemoryEvidenceResponse]:
    lifecycle = _lifecycle(_services(server))
    family = _family(_services(server))
    family.require_access(family_id, user)
    memory = lifecycle.context.repo.get_memory(memory_id)
    if memory is None or memory.family_id != family_id:
        raise OctopError(ErrorCode.NOT_FOUND, "memory not found")
    return [
        _evidence_response(row) for row in lifecycle.evidence.list_for_memory(memory_id)
    ]


@router.post(
    "/{family_id}/memories/{memory_id}/archive",
    response_model=MemoryCandidateResponse,
    summary="Archive an active memory (manager only)",
    description=(
        "Marks the canonical ``FamilyMemoryRow`` as ARCHIVED so the "
        "default search and context resolver ignore it. Evidence rows "
        "remain for audit. Physical delete is reserved for the daily "
        "cron task after the retention window."
    ),
)
async def archive_memory(
    family_id: str, memory_id: str, server: Server, user: CurrentUser,
) -> MemoryCandidateResponse:
    family = _family(_services(server))
    family.require_manager(family_id, user)
    services = _services(server)
    memory = services.family_context_repo.get_memory(memory_id)
    if memory is None or memory.family_id != family_id:
        raise OctopError(ErrorCode.NOT_FOUND, "memory not found")
    updated = services.family_context_repo.update_memory(
        memory_id, status="ARCHIVED",
    )
    return _candidate_response(updated) if updated is not None else _candidate_response(memory)  # type: ignore[arg-type]


@router.post(
    "/{family_id}/memories/{memory_id}/restore",
    response_model=MemoryCandidateResponse,
    summary="Restore an archived memory to ACTIVE",
)
async def restore_memory(
    family_id: str, memory_id: str, server: Server, user: CurrentUser,
) -> MemoryCandidateResponse:
    family = _family(_services(server))
    family.require_manager(family_id, user)
    services = _services(server)
    memory = services.family_context_repo.get_memory(memory_id)
    if memory is None or memory.family_id != family_id:
        raise OctopError(ErrorCode.NOT_FOUND, "memory not found")
    updated = services.family_context_repo.update_memory(
        memory_id, status="ACTIVE",
    )
    return _candidate_response(updated) if updated is not None else _candidate_response(memory)  # type: ignore[arg-type]


# Re-exported so tests can import the helpers above without going
# through the router.
__all__ = [
    "router",
    "MemoryCandidateResponse",
    "MemoryEvidenceResponse",
    "MemoryCandidateApproveResponse",
    "MemoryCandidateRejectBody",
    "MemoryCandidateMergeBody",
]
