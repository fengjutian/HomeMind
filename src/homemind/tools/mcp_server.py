"""HomeMind MCP server surface (Stage 10).

The Stage 11 toolkit in :mod:`homemind.tools.mcp_family_tools` is a set
of LangChain ``StructuredTool`` wrappers. That is *not* an MCP server:
an MCP client speaks JSON-RPC to a server that owns a session and
authenticates it. This module supplies that missing layer.

The security rules the spec sets out, and where each is enforced:

* **Identity is never a parameter.** ``user_id`` is not accepted by any
  tool input model — ``extra="forbid"`` plus no such field means a
  client cannot smuggle one. The principal comes from
  :class:`McpPrincipal`, which the transport builds from the
  authenticated session.
* **No active family, no family.** Every call resolves the caller's
  active family through ``ActiveFamilyResolver``. A client may pass
  ``family_id``, but it must match the active family or the caller's
  authorised set; a mismatch is refused rather than silently honoured.
* **Writes go through transactions.** ``create_task`` /
  ``create_event`` / ``create_memory_candidate`` / ``plan_transaction``
  call the transaction manager, never a repo directly.
* **Everything is audited.** Each call records client id, session id,
  user id, family id, tool name, and result — with no tokens or
  secrets in the payload.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from homemind.infra.active_family import ActiveFamilyResolver
from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.context import FamilyContextManager, MemoryType
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.memory_lifecycle import (
    CandidateSpec,
    MemoryLifecycleManager,
    is_sensitive,
)
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import OctopError
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)


class McpErrorCode(StrEnum):
    """HomeMind MCP error codes, namespaced so a client can branch."""

    ACTIVE_FAMILY_REQUIRED = "HOMEMIND_ACTIVE_FAMILY_REQUIRED"
    FAMILY_MISMATCH = "HOMEMIND_FAMILY_ACCESS_DENIED"
    IDENTITY_REQUIRED = "HOMEMIND_IDENTITY_REQUIRED"
    TRANSACTION_CONFLICT = "HOMEMIND_TRANSACTION_CONFLICT"
    INVALID_REQUEST = "HOMEMIND_INVALID_REQUEST"


@dataclass(frozen=True)
class McpPrincipal:
    """Who is calling, established by the transport before dispatch.

    ``user_id`` is required; ``client_id`` / ``session_id`` are recorded
    for audit. Nothing here is accepted as a tool argument.
    """

    user_id: int
    client_id: str = "unknown"
    session_id: str = "unknown"


@dataclass(frozen=True)
class McpResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            payload["data"] = self.data
        else:
            payload["error"] = {
                "code": self.error_code,
                "message": self.error_message,
            }
        return payload


class HomeMindMcpServer:
    """Transport-agnostic MCP surface for one family installation.

    A transport (stdio, HTTP, or an in-process agent session) creates
    the server, authenticates the caller into an :class:`McpPrincipal`,
    and then calls :meth:`call_tool`.
    """

    TOOL_NAMES: tuple[str, ...] = (
        "family.list_members",
        "family.get_member",
        "family.search_assets",
        "family.search_memory",
        "family.list_events",
        "family.list_tasks",
        "family.get_devices",
        "family.resolve_context",
        "family.create_task",
        "family.create_event",
        "family.create_memory_candidate",
        "family.plan_transaction",
    )

    def __init__(
        self,
        services: HomeMindServices,
        *,
        active_family_resolver: ActiveFamilyResolver,
    ) -> None:
        self._services = services
        self._active_family = active_family_resolver
        self._user_repo = UserRepo(services.db)
        self._family = FamilyManager(services.family_repo)
        self._context = FamilyContextManager(
            self._family, services.family_context_repo,
            asset_repo=services.family_asset_repo,
        )
        self._lifecycle = MemoryLifecycleManager(
            self._family,
            self._context,
            services.memory_candidate_repo,
            services.memory_evidence_repo,
        )

    # ------------------------------------------------------------- dispatch

    def list_tools(self) -> list[dict[str, Any]]:
        """Tool descriptors with an argument schema per tool.

        No descriptor carries ``user_id``: identity is not an argument.
        """
        return [
            {
                "name": name,
                "description": _TOOL_DESCRIPTIONS[name],
                "inputSchema": _TOOL_SCHEMAS[name],
            }
            for name in self.TOOL_NAMES
        ]

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        principal: McpPrincipal,
    ) -> McpResult:
        """Run one tool under ``principal`` and audit the outcome."""
        if name not in self.TOOL_NAMES:
            return McpResult(
                ok=False,
                error_code=McpErrorCode.INVALID_REQUEST,
                error_message=f"unknown tool {name!r}",
            )
        user, family_id, failure = self._resolve(principal, arguments)
        if failure is not None or user is None or family_id is None:
            if failure is None:
                # Identity resolved but no family scope; treat as a
                # missing active family rather than crashing.
                failure = McpResult(
                    ok=False,
                    error_code=McpErrorCode.ACTIVE_FAMILY_REQUIRED,
                    error_message="no active family selected",
                )
            self._audit(
                principal, family_id, name, {"rejected": failure.error_message},
            )
            return failure
        try:
            data = self._dispatch(name, arguments, user, family_id)
        except HomeMindError as exc:
            self._audit(principal, family_id, name, {"error": exc.message})
            return McpResult(
                ok=False, error_code=exc.code.value, error_message=exc.message,
            )
        except OctopError as exc:
            self._audit(principal, family_id, name, {"error": str(exc)})
            return McpResult(
                ok=False, error_code=exc.code.value, error_message=str(exc),
            )
        self._audit(principal, family_id, name, {"ok": True})
        return McpResult(ok=True, data=data)

    # ------------------------------------------------------------- identity

    def _resolve(
        self, principal: McpPrincipal, arguments: dict[str, Any],
    ) -> tuple[User | None, str | None, McpResult | None]:
        """Bind the principal to a user and an authorised family.

        Returns ``(user, family_id, failure)``. ``failure`` is non-None
        when the call must be refused; ``family_id`` is ``None`` for the
        ``family.list_*`` tools, which need no family scope.
        """
        row = self._user_repo.get(principal.user_id)
        if row is None or row.disabled:
            return None, None, McpResult(
                ok=False,
                error_code=McpErrorCode.IDENTITY_REQUIRED,
                error_message="MCP principal is not a live user",
            )
        user = User(
            id=row.id,
            username=row.username,
            role=row.role,
            display_name=row.display_name,
            locale=row.locale,
            permissions=row.permissions,
        )
        try:
            active_family = self._active_family.get(user)
        except OctopError:
            active_family = None
        if active_family is None:
            return (
                user,
                None,
                McpResult(
                    ok=False,
                    error_code=McpErrorCode.ACTIVE_FAMILY_REQUIRED,
                    error_message="no active family selected",
                ),
            )
        requested = arguments.get("family_id")
        if requested is not None and str(requested) != active_family:
            # A client may name a family only when it matches the one
            # the caller selected; anything else is an attempt to reach
            # across the boundary.
            self._family.require_access(active_family, user)
            return (
                user,
                None,
                McpResult(
                    ok=False,
                    error_code=McpErrorCode.FAMILY_MISMATCH,
                    error_message=(
                        "family_id does not match the caller's active family"
                    ),
                ),
            )
        return user, active_family, None

    # -------------------------------------------------------------- dispatch

    def _dispatch(
        self, name: str, arguments: dict[str, Any], user: User, family_id: str,
    ) -> dict[str, Any]:
        if name == "family.list_members":
            return {
                "members": [
                    {
                        "id": member.id,
                        "display_name": member.display_name,
                        "role": member.role,
                    }
                    for member in self._family.repo.list_members(family_id)
                ],
            }
        if name == "family.get_member":
            member_id = str(arguments.get("member_id", ""))
            member = self._family.repo.get_member(member_id)
            if member is None or member.family_id != family_id:
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_INVALID, "member not found in family",
                )
            return {
                "member": {
                    "id": member.id,
                    "display_name": member.display_name,
                    "role": member.role,
                    "status": member.status,
                },
            }
        if name == "family.search_assets":
            from homemind.infra.family.assets import FamilyAssetManager  # noqa: PLC0415

            assets = FamilyAssetManager(
                self._family.repo, self._services.family_asset_repo,
            ).search(family_id, user, limit=int(arguments.get("limit", 20)))
            return {
                "assets": [
                    {
                        "id": asset.id,
                        "name": asset.name,
                        "asset_type": asset.asset_type,
                        "captured_at": asset.captured_at,
                    }
                    for asset in assets
                ],
            }
        if name == "family.search_memory":
            memories = self._context.search_memories(
                family_id, user, query=arguments.get("query"),
            )
            return {
                "memories": [
                    {
                        "id": memory.id,
                        "content": memory.content,
                        "memory_type": memory.memory_type,
                    }
                    for memory in memories[: int(arguments.get("limit", 20))]
                ],
            }
        if name == "family.list_events":
            return {
                "events": [
                    {
                        "id": event.id,
                        "title": event.title,
                        "event_type": event.event_type,
                        "start_at": event.start_at,
                    }
                    for event in self._context.list_events(family_id, user)
                ],
            }
        if name == "family.list_tasks":
            tasks = self._services.family_task_repo.list(family_id)
            return {
                "tasks": [
                    {"id": task.id, "title": task.title, "status": task.status}
                    for task in tasks
                ],
            }
        if name == "family.get_devices":
            devices = self._services.family_device_repo.list_for_family(family_id)
            return {
                "devices": [
                    {
                        "id": device.id,
                        "name": device.name,
                        "status": device.status,
                        "last_seen": device.last_seen,
                    }
                    for device in devices
                ],
            }
        if name == "family.resolve_context":
            resolved = self._context.resolve(
                family_id, user, str(arguments.get("query", "")),
            )
            return {
                "context": {
                    "family_id": resolved.family_id,
                    "current_member_id": resolved.current_member_id,
                    "member_ids": resolved.member_ids,
                    "event_ids": resolved.event_ids,
                    "memory_ids": resolved.memory_ids,
                    "ambiguities": [
                        {
                            "entity_id": item.entity_id,
                            "label": item.label,
                            "confidence": item.confidence,
                        }
                        for item in resolved.ambiguities
                    ],
                },
            }
        if name == "family.create_task":
            return self._plan_write(
                family_id, user, action="task.create",
                payload={"title": str(arguments.get("title", ""))},
                idempotency_key=arguments.get("idempotency_key"),
            )
        if name == "family.create_event":
            return self._plan_write(
                family_id, user, action="event.create",
                payload={
                    "title": str(arguments.get("title", "")),
                    "event_type": str(arguments.get("event_type", "OTHER")),
                    "start_at": int(arguments.get("start_at", 0)),
                    "end_at": int(arguments.get("end_at", 0)),
                    "description": str(arguments.get("description", "")),
                },
                idempotency_key=arguments.get("idempotency_key"),
            )
        if name == "family.create_memory_candidate":
            return self._create_memory_candidate(family_id, user, arguments)
        if name == "family.plan_transaction":
            return self._plan_write(
                family_id, user,
                action=str(arguments.get("action", "")),
                payload=dict(arguments.get("payload") or {}),
                idempotency_key=arguments.get("idempotency_key"),
            )
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID, f"unhandled tool {name!r}",
        )

    # ---------------------------------------------------------------- writes

    def _plan_write(
        self,
        family_id: str,
        user: User,
        *,
        action: str,
        payload: dict[str, Any],
        idempotency_key: Any,
    ) -> dict[str, Any]:
        """Route a write through the transaction manager.

        The tool never touches a repo — an agent-initiated write must
        land in the approval state machine so a manager can see it.
        """
        from homemind.infra.family.tasks import FamilyTaskManager  # noqa: PLC0415
        from homemind.infra.family.transactions import (  # noqa: PLC0415
            FamilyTransactionManager,
        )

        transactions = FamilyTransactionManager(
            self._family,
            self._context,
            FamilyTaskManager(self._family, self._services.family_task_repo),
            self._services.family_transaction_repo,
            self._user_repo,
        )
        transaction, approval = transactions.plan(
            family_id,
            user,
            action=action,
            payload=payload,
            idempotency_key=(
                str(idempotency_key) if idempotency_key is not None else None
            ),
        )
        return {
            "transaction_id": transaction.id,
            "status": transaction.status,
            "approval_id": approval.id if approval is not None else None,
        }

    def _create_memory_candidate(
        self, family_id: str, user: User, arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a memory candidate.

        Memory candidates are *not* transactions: they are proposals
        awaiting human review, which is the same lifecycle the Stage 3
        post-turn extractor uses.
        """
        content = str(arguments.get("content", "")).strip()
        if not content:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "candidate content is required",
            )
        candidate = self._lifecycle.create_candidate(
            CandidateSpec(
                family_id=family_id,
                subject_type=str(arguments.get("subject_type", "FAMILY")),
                subject_id=arguments.get("subject_id"),
                content=content,
                memory_type=str(
                    arguments.get("memory_type", MemoryType.EXPERIENCE.value)
                ),
                importance=float(arguments.get("importance", 0.5)),
                confidence=float(arguments.get("confidence", 0.5)),
                visibility=str(arguments.get("visibility", "FAMILY")),
                source_type="MCP",
                source_id=None,
            ),
            creator=user,
        )
        return {
            "candidate_id": candidate.id,
            "status": candidate.status,
            "sensitive": is_sensitive(content),
        }

    # ---------------------------------------------------------------- audit

    def _audit(
        self,
        principal: McpPrincipal,
        family_id: str | None,
        tool_name: str,
        result: dict[str, Any],
    ) -> None:
        """Record one MCP call.

        Only identifiers and outcomes are recorded — never a token,
        never a credential, never a secret payload.
        """
        with self._services.db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_external_processing_audit("
                "audit_id, family_id, user_id, provider_id, model, operation, "
                "data_categories, result, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _new_audit_id(),
                    family_id or "",
                    principal.user_id,
                    f"mcp:{principal.client_id}",
                    "home-mind-mcp",
                    tool_name,
                    "[]",
                    json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
                    int(time.time()),
                ),
            )


def _new_audit_id() -> str:
    from octop.infra.utils.ulid import new_ulid  # noqa: PLC0415

    return new_ulid()


_TOOL_DESCRIPTIONS: dict[str, str] = {
    "family.list_members": "List every member in the caller's active family.",
    "family.get_member": "Fetch one member by id from the active family.",
    "family.search_assets": "Search indexed assets visible to the caller.",
    "family.search_memory": "Search durable family memories visible to the caller.",
    "family.list_events": "List family events in the active family.",
    "family.list_tasks": "List family tasks in the active family.",
    "family.get_devices": "List registered devices and their online state.",
    "family.resolve_context": (
        "Resolve a natural-language question against the family graph; "
        "returns ambiguities when a term matches more than one member."
    ),
    "family.create_task": "Plan a task creation through the approval workflow.",
    "family.create_event": "Plan an event creation through the approval workflow.",
    "family.create_memory_candidate": (
        "Propose a family memory for human review. Sensitive content is "
        "flagged and never auto-approved."
    ),
    "family.plan_transaction": (
        "Plan an arbitrary write transaction. Requires manager rights "
        "when the action is privileged."
    ),
}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Build a JSON schema that forbids unknown properties.

    ``additionalProperties: false`` is what makes smuggling a
    ``user_id`` a protocol error rather than a silently ignored field.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_FAMILY = {"type": "string", "description": "Must match the caller's active family."}

_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "family.list_members": _schema({}),
    "family.get_member": _schema(
        {"member_id": _STRING}, ["member_id"],
    ),
    "family.search_assets": _schema(
        {"family_id": _FAMILY, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
    ),
    "family.search_memory": _schema(
        {"family_id": _FAMILY, "query": _STRING, "limit": {"type": "integer"}},
    ),
    "family.list_events": _schema({"family_id": _FAMILY}),
    "family.list_tasks": _schema({"family_id": _FAMILY}),
    "family.get_devices": _schema({"family_id": _FAMILY}),
    "family.resolve_context": _schema(
        {"family_id": _FAMILY, "query": _STRING}, ["query"],
    ),
    "family.create_task": _schema(
        {
            "family_id": _FAMILY,
            "title": _STRING,
            "description": _STRING,
            "assigned_member_id": _STRING,
            "due_at": _INTEGER,
            "idempotency_key": _STRING,
        },
        ["title"],
    ),
    "family.create_event": _schema(
        {
            "family_id": _FAMILY,
            "title": _STRING,
            "event_type": _STRING,
            "start_at": _INTEGER,
            "end_at": _INTEGER,
            "description": _STRING,
            "idempotency_key": _STRING,
        },
        ["title", "start_at", "end_at"],
    ),
    "family.create_memory_candidate": _schema(
        {
            "family_id": _FAMILY,
            "content": _STRING,
            "memory_type": _STRING,
            "subject_type": _STRING,
            "subject_id": _STRING,
            "importance": {"type": "number"},
            "confidence": {"type": "number"},
            "visibility": _STRING,
        },
        ["content"],
    ),
    "family.plan_transaction": _schema(
        {
            "family_id": _FAMILY,
            "action": _STRING,
            "payload": {"type": "object"},
            "idempotency_key": _STRING,
        },
        ["action"],
    ),
}


__all__ = [
    "HomeMindMcpServer",
    "McpErrorCode",
    "McpPrincipal",
    "McpResult",
]
