"""Stage 11: umbrella MCP-style family tools.

These four tools compose the existing managers (context resolver,
memory lifecycle, transaction state machine, family search) into
higher-level entry points that an MCP client or an agent call can
invoke with a single, well-typed payload.

Naming follows the V0.1 plan:
- ``family_query``        read across the family graph
- ``context_resolve``     answer a natural-language question with members/events/assets/memories
- ``memory_commit``       promote a memory candidate into the canonical family memory store
- ``transaction_plan``    plan a write that goes through the approval state machine

The tools return plain ``dict`` objects (not JSON strings) so callers
that speak MCP JSON-RPC can pass them through unchanged. The
:func:`wire_mcp_family_tools` helper returns them as ``StructuredTool``
instances for the agent runtime.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from homemind.infra.db.pool import DatabasePool
from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.device_runtime import DeviceRuntimeManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.memory_lifecycle import (
    CandidateSpec,
    MemoryLifecycleManager,
)
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transactions import FamilyTransactionManager
from homemind.tools.family import _build_managers

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- inputs


class FamilyQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family_id: str
    target: Literal[
        "members", "events", "memories", "assets", "tasks", "devices",
    ] = "members"
    query: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=20, ge=1, le=200)


class ContextResolveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family_id: str
    query: str = Field(min_length=1, max_length=500)


class MemoryCommitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family_id: str
    content: str = Field(min_length=1, max_length=4000)
    memory_type: str = Field(default="EXPERIENCE", max_length=50)
    subject_type: Literal["FAMILY", "MEMBER", "EVENT", "ASSET", "TASK", "OTHER"] = "OTHER"
    subject_id: str | None = Field(default=None, max_length=100)
    evidence: list[str] = Field(default_factory=list)


class TransactionPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family_id: str
    action: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=100)


# --------------------------------------------------------------------- outputs


def _envelope(tool: str, **payload: Any) -> dict[str, Any]:
    return {"tool": tool, "ts": int(time.time()), **payload}


def _serialise(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_serialise(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialise(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return _serialise(asdict(value))
    return str(value)


# --------------------------------------------------------------- implementations


class McpFamilyToolkit:
    """Stateless façade that builds the four umbrella tools on demand.

    The toolkit holds a single database handle and constructs the
    managers per call so a re-bind (e.g. after a control-plane hot
    swap) is picked up automatically.
    """

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ----------------------------------------------------------------- helpers

    def _services(self) -> HomeMindServices:
        from homemind.infra.db.migrate import run_migrations

        run_migrations(self._db)
        return HomeMindServices.from_pool(self._db)

    def _managers(self, services: HomeMindServices) -> dict[str, Any]:
        """Construct the full manager graph used by the umbrella tools."""
        return _build_managers(services)

    # --------------------------------------------------------------- family_query

    def family_query(self, payload: FamilyQueryInput) -> dict[str, Any]:
        services = self._services()
        managers = self._managers(services)
        family_manager = managers["family"]
        user = _resolve_user(services.user_repo)
        family_manager.require_access(payload.family_id, user)
        rows: list[Any] = []
        if payload.target == "members":
            rows = services.family_repo.list_members(payload.family_id)
        elif payload.target == "events":
            rows = managers["context"].list_events(payload.family_id, user)
        elif payload.target == "memories":
            rows = managers["context"].search_memories(
                payload.family_id, user, payload.query,
            )
        elif payload.target == "assets":
            rows = managers["assets"].search(
                payload.family_id, user,
                query=payload.query, limit=payload.limit,
            )
        elif payload.target == "tasks":
            rows = managers["tasks"].list(payload.family_id, user)
        elif payload.target == "devices":
            rows = managers["devices"].list_devices(payload.family_id, user)
        return _envelope(
            "family_query",
            target=payload.target,
            count=len(rows),
            items=_serialise(rows[: payload.limit]),
        )

    # ---------------------------------------------------------- context_resolve

    def context_resolve(self, payload: ContextResolveInput) -> dict[str, Any]:
        services = self._services()
        managers = self._managers(services)
        user = _resolve_user(services.user_repo)
        resolved = managers["context"].resolve(
            payload.family_id, user, payload.query,
        )
        return _envelope(
            "context_resolve",
            query=payload.query,
            member_ids=resolved.member_ids,
            event_ids=resolved.event_ids,
            memory_ids=resolved.memory_ids,
            asset_ids=resolved.asset_ids,
            time_range=resolved.time_range.as_dict() if resolved.time_range else None,
            ambiguities=resolved.ambiguities,
        )

    # ------------------------------------------------------------ memory_commit

    def memory_commit(self, payload: MemoryCommitInput) -> dict[str, Any]:
        services = self._services()
        managers = self._managers(services)
        family_manager = managers["family"]
        user = _resolve_user(services.user_repo)
        family_manager.require_manager(payload.family_id, user)
        lifecycle = MemoryLifecycleManager(
            family_manager,
            managers["context"],
            services.memory_candidate_repo,
            services.memory_evidence_repo,
        )
        spec = CandidateSpec(
            family_id=payload.family_id,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            content=payload.content,
            memory_type=payload.memory_type,
            source_type="AGENT",
        )
        try:
            candidate = lifecycle.create_candidate(spec, creator=user)
        except HomeMindError as exc:
            return _envelope(
                "memory_commit",
                status="REJECTED",
                reason=exc.message,
            )
        # Sensitive content is kept PENDING so a human manager has to
        # call ``approve_candidate`` explicitly. Non-sensitive content
        # is auto-promoted through ``approve_candidate`` so the tool
        # returns a single committed memory in one MCP call.
        if candidate.status == "PENDING" and getattr(candidate, "sensitive", False):
            return _envelope(
                "memory_commit",
                status="PENDING",
                candidate_id=candidate.id,
                reason="memory flagged sensitive; requires manager review",
            )
        lifecycle.approve_candidate(
            payload.family_id, candidate.id, reviewer=user,
        )
        return _envelope(
            "memory_commit",
            status="APPROVED",
            candidate_id=candidate.id,
        )

    # ----------------------------------------------------------- transaction_plan

    def transaction_plan(self, payload: TransactionPlanInput) -> dict[str, Any]:
        services = self._services()
        managers = self._managers(services)
        user = _resolve_user(services.user_repo)
        managers["family"].require_access(payload.family_id, user)
        transaction_manager = managers["transactions"]
        try:
            transaction = transaction_manager.plan(
                payload.family_id,
                user,
                action=payload.action,
                payload=payload.payload,
                idempotency_key=payload.idempotency_key,
            )
        except HomeMindError as exc:
            return _envelope(
                "transaction_plan",
                status="FAILED",
                code=exc.code.value,
                message=exc.message,
            )
        return _envelope(
            "transaction_plan",
            status=transaction.status,
            transaction_id=transaction.id,
            action=transaction.action,
        )


# ----------------------------------------------------------- wiring helpers


def _resolve_user(user_repo: Any) -> Any:
    """Resolve the calling user from the agent config."""
    try:
        from langgraph.config import get_config

        configurable = get_config().get("configurable") or {}
        raw_user_id = configurable.get("user")
        if raw_user_id is not None:
            row = user_repo.get(int(raw_user_id))
            if row is not None and not row.disabled:
                from octop.infra.users.identity import User

                return User(
                    id=row.id,
                    username=row.username,
                    role=row.role,
                    display_name=row.display_name,
                    locale=row.locale,
                    permissions=row.permissions,
                )
    except Exception:  # noqa: BLE001
        pass
    raise HomeMindError(
        HomeMindErrorCode.FAMILY_INVALID,
        "MCP family tools require a configured user; "
        "ensure the agent run carries configurable.user",
    )


def wire_mcp_family_tools(db: DatabasePool) -> list[StructuredTool]:
    """Build the four umbrella tools as ``StructuredTool`` instances."""
    toolkit = McpFamilyToolkit(db)

    return [
        StructuredTool.from_function(
            func=toolkit.family_query,
            name="family_query",
            description=(
                "Read family data (members, events, memories, assets, "
                "tasks, devices) by target + optional query string."
            ),
            args_schema=FamilyQueryInput,
        ),
        StructuredTool.from_function(
            func=toolkit.context_resolve,
            name="context_resolve",
            description=(
                "Resolve a natural-language question against the family "
                "graph: members, relationships, time range, events, "
                "memories, and assets."
            ),
            args_schema=ContextResolveInput,
        ),
        StructuredTool.from_function(
            func=toolkit.memory_commit,
            name="memory_commit",
            description=(
                "Propose a memory entry; sensitive content is held for "
                "manager review, otherwise it is committed directly."
            ),
            args_schema=MemoryCommitInput,
        ),
        StructuredTool.from_function(
            func=toolkit.transaction_plan,
            name="transaction_plan",
            description=(
                "Plan a write transaction; idempotency_key collapses "
                "replays and the resulting transaction goes through the "
                "approval state machine."
            ),
            args_schema=TransactionPlanInput,
        ),
    ]


__all__ = [
    "ContextResolveInput",
    "FamilyQueryInput",
    "MemoryCommitInput",
    "McpFamilyToolkit",
    "TransactionPlanInput",
    "wire_mcp_family_tools",
]
