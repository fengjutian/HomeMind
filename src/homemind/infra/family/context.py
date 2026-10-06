"""Family events, durable memories, and deterministic context resolution."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from homemind.infra.db.repos.family_context import (
    FamilyContextRepo,
    FamilyEventRow,
    FamilyMemoryRow,
)
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.permissions import (
    FamilyPermissionEvaluator,
    PermissionDecision,
    PermissionEffect,
)
from homemind.infra.family.resolvers import (
    AssetQueryResolver,
    EventResolver,
    RelationshipResolver,
    ResolutionCandidate,
    ResolvedTimeRange,
    TimeRangeResolver,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User


class MemoryType(StrEnum):
    FACT = "FACT"
    PREFERENCE = "PREFERENCE"
    EVENT = "EVENT"
    RELATIONSHIP = "RELATIONSHIP"
    HABIT = "HABIT"
    DECISION = "DECISION"
    EXPERIENCE = "EXPERIENCE"


@dataclass(frozen=True)
class ResolvedFamilyContext:
    family_id: str
    current_member_id: str
    member_ids: list[str]
    relationship_ids: list[str]
    event_ids: list[str]
    memory_ids: list[str]
    asset_ids: list[str]
    permission_decisions: list[PermissionDecision] = field(default_factory=list)
    time_range: ResolvedTimeRange | None = None
    ambiguities: list[ResolutionCandidate] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)


class FamilyContextManager:
    def __init__(
        self,
        family: FamilyManager,
        repo: FamilyContextRepo,
        *,
        permission_evaluator: FamilyPermissionEvaluator | None = None,
        asset_repo: FamilyAssetRepo | None = None,
    ) -> None:
        self.family = family
        self.repo = repo
        self.permissions = permission_evaluator or FamilyPermissionEvaluator(family.repo)
        # Optional asset repo: when supplied, asset candidates are resolved
        # via the Stage 2 ``AssetQueryResolver``. Otherwise asset_ids stays
        # empty and ``asset_ids`` is simply not populated.
        self.asset_repo = asset_repo
        if asset_repo is not None:
            self.asset_resolver = AssetQueryResolver(asset_repo)
        else:
            self.asset_resolver = None

    def create_event(self, family_id: str, user: User, **values: object) -> FamilyEventRow:
        self.family.require_manager(family_id, user)
        start_at, end_at = int(values["start_at"]), int(values["end_at"])
        if end_at < start_at:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "event end_at must not precede start_at",
            )
        values["title"] = str(values["title"]).strip()
        values["created_by"] = user.id
        values["metadata_json"] = json.dumps(
            values.pop("metadata", {}), ensure_ascii=False, sort_keys=True
        )
        return self.repo.create_event(family_id, **values)

    def list_events(
        self, family_id: str, user: User, **filters: int | None
    ) -> list[FamilyEventRow]:
        self.family.require_access(family_id, user)
        return self.repo.list_events(family_id, **filters)

    def update_event(
        self,
        family_id: str,
        event_id: str,
        user: User,
        changes: dict[str, object],
    ) -> FamilyEventRow:
        self.family.require_manager(family_id, user)
        event = self._event(family_id, event_id)
        start_at = int(changes.get("start_at", event.start_at))
        end_at = int(changes.get("end_at", event.end_at))
        if end_at < start_at:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "event end_at must not precede start_at",
            )
        if "metadata" in changes:
            changes["metadata_json"] = json.dumps(
                changes.pop("metadata") or {}, ensure_ascii=False, sort_keys=True
            )
        return self.repo.update_event(event_id, **changes)  # type: ignore[return-value]

    def delete_event(self, family_id: str, event_id: str, user: User) -> None:
        self.family.require_manager(family_id, user)
        self._event(family_id, event_id)
        self.repo.delete_event(event_id)

    def create_memory(self, family_id: str, user: User, **values: object) -> FamilyMemoryRow:
        self.family.require_access(family_id, user)
        subject_type, subject_id = str(values["subject_type"]), values.get("subject_id")
        if subject_type == "MEMBER" and subject_id is not None:
            member = self.family.repo.get_member(str(subject_id))
            if member is None or member.family_id != family_id:
                raise OctopError(ErrorCode.NOT_FOUND, "family member not found")
        values["content"] = str(values["content"]).strip()
        values["created_by"] = user.id
        return self.repo.create_memory(family_id, **values)

    def search_memories(
        self, family_id: str, user: User, query: str | None = None
    ) -> list[FamilyMemoryRow]:
        self.family.require_access(family_id, user)
        memories = self.repo.search_memories(family_id, query=query)
        return [memory for memory in memories if self._can_read_memory(memory, user)]

    def search_memories_fts(
        self,
        family_id: str,
        user: User,
        query: str,
        *,
        limit: int = 20,
    ) -> list[FamilyMemoryRow]:
        """FTS5-backed memory search (stage 8).

        Postgres falls through to the LIKE-based ``search_memories``
        because the FTS virtual table only exists on SQLite.
        """
        self.family.require_access(family_id, user)
        memories = self.repo.search_memories_fts(
            family_id, query=query, limit=limit,
        )
        if memories:
            return [
                memory for memory in memories
                if self._can_read_memory(memory, user)
            ]
        return self.search_memories(family_id, user, query=query)

    def update_memory(
        self,
        family_id: str,
        memory_id: str,
        user: User,
        changes: dict[str, object],
    ) -> FamilyMemoryRow:
        self.family.require_manager(family_id, user)
        self._memory(family_id, memory_id)
        if "content" in changes:
            changes["content"] = str(changes["content"]).strip()
        return self.repo.update_memory(memory_id, **changes)  # type: ignore[return-value]

    def delete_memory(self, family_id: str, memory_id: str, user: User) -> None:
        self.family.require_manager(family_id, user)
        self._memory(family_id, memory_id)
        self.repo.delete_memory(memory_id)

    def resolve(self, family_id: str, user: User, query: str) -> ResolvedFamilyContext:
        family = self.family.require_access(family_id, user)
        membership = self.family.repo.get_membership(family_id, user.id)
        if membership is None and not user.is_admin:
            raise OctopError(ErrorCode.FORBIDDEN, "family membership required")
        current_member_id = (
            str(membership["member_id"]) if membership is not None else ""
        )

        relationship_resolver = RelationshipResolver(self.family.repo)
        time_resolver = TimeRangeResolver(timezone=family.timezone)
        event_resolver = EventResolver(self.repo)

        normalized = query.casefold()
        members = self.family.repo.list_members(family_id)
        relationships = self.family.repo.list_relationships(family_id)

        # 1. Relationship resolution: prefer family-term matches over plain
        # display-name lookups. Record every candidate so multiple matches
        # become an ambiguity rather than an arbitrary choice.
        member_candidates: list[ResolutionCandidate] = []
        for term in _extract_relation_terms(query):
            for resolved in relationship_resolver.find(term, current_member_id=current_member_id):
                if resolved.candidate not in member_candidates:
                    member_candidates.append(resolved.candidate)

        # Fall back to display-name matching if no term was recognized.
        if not member_candidates:
            for member in members:
                if member.display_name.casefold() in normalized:
                    member_candidates.append(
                        ResolutionCandidate(
                            entity_type="MEMBER",
                            entity_id=member.id,
                            label=member.display_name,
                            confidence=0.6,
                            reason="display_name_substring",
                        )
                    )

        ambiguities: list[ResolutionCandidate] = []
        if len(member_candidates) > 1:
            ambiguities.extend(member_candidates)

        matched_member_ids = {c.entity_id for c in member_candidates}
        matched_relationships = [
            relationship
            for relationship in relationships
            if relationship.from_member_id in matched_member_ids
            or relationship.to_member_id in matched_member_ids
            or relationship.relationship_type.casefold() in normalized
        ]

        # 2. Time resolution.
        time_range = time_resolver.resolve(query)
        start_at = time_range.start_at
        end_at = time_range.end_at

        # 3. Event resolution: structured, no silent "return all".
        event_matches = event_resolver.search(
            family_id,
            text=query,
            member_ids=matched_member_ids,
            time_range=time_range,
        )
        # Fall back: when no time range is set, still include events whose
        # title / location / type contains query terms.
        if not event_matches and start_at is None:
            event_matches = event_resolver.search(
                family_id, text=query, member_ids=matched_member_ids,
            )
        events = [match.event for match in event_matches]

        # 4. Memory resolution: keep legacy "fall back to all memories"
        # semantics so context blocks don't lose global signals when no
        # token matches. Per Stage 2 spec the strict-no-fallback rule
        # applies to *events*, not memories.
        memories = self.search_memories(family_id, user)
        memory_matches = [
            memory
            for memory in memories
            if memory.content.casefold() in normalized
            or any(
                token in memory.content.casefold()
                for token in normalized.split()
                if len(token) > 1
            )
        ]
        if not memory_matches:
            memory_matches = memories

        # 5. Asset resolution (optional, requires injection).
        asset_ids: list[str] = []
        if self.asset_resolver is not None:
            asset_matches = self.asset_resolver.search(
                family_id,
                text=query,
                member_ids=matched_member_ids,
                time_range=time_range,
            )
            asset_ids = [match.asset.id for match in asset_matches]

        # 6. Permission decisions: surface every action we may want to
        # take as part of this context query.
        decisions = self._permission_decisions_for(
            family_id=family_id,
            user=user,
            member_id=current_member_id,
            asset_ids=asset_ids,
        )

        permissions = self.family.repo.list_permissions(family_id)
        return ResolvedFamilyContext(
            family_id=family_id,
            current_member_id=current_member_id,
            member_ids=[c.entity_id for c in member_candidates],
            relationship_ids=[r.id for r in matched_relationships],
            event_ids=[event.id for event in events],
            memory_ids=[memory.id for memory in memory_matches],
            asset_ids=asset_ids,
            permission_decisions=decisions,
            time_range=time_range if time_range.confidence > 0 else None,
            ambiguities=ambiguities,
            permissions=[
                permission.action
                for permission in permissions
                if permission.expires_at is None
                or permission.expires_at > int(time.time())
            ],
        )

    def _permission_decisions_for(
        self,
        *,
        family_id: str,
        user: User,
        member_id: str,
        asset_ids: list[str],
    ) -> list[PermissionDecision]:
        """Surface the read/search decisions the Agent is most likely to act on.

        For each private space in the family we evaluate the per-space
        ``filesystem.read`` so private-space denials surface in the
        resolved context for follow-up Agent reasoning.
        """
        decisions = [
            self.permissions.evaluate(
                family_id=family_id, user=user, action=action,
            )
            for action in ("family.read", "family.search")
        ]
        private_space_ids = [
            space.id
            for space in self.family.repo.list_spaces(family_id)
            if space.space_type == "PRIVATE"
        ]
        if private_space_ids:
            for space_id in private_space_ids:
                decisions.append(
                    self.permissions.evaluate(
                        family_id=family_id,
                        user=user,
                        action="filesystem.read",
                        space_id=space_id,
                    )
                )
        else:
            decisions.append(
                self.permissions.evaluate(
                    family_id=family_id,
                    user=user,
                    action="filesystem.read",
                )
            )
        return decisions

    def _event(self, family_id: str, event_id: str) -> FamilyEventRow:
        event = self.repo.get_event(event_id)
        if event is None or event.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family event not found")
        return event

    def _memory(self, family_id: str, memory_id: str) -> FamilyMemoryRow:
        memory = self.repo.get_memory(memory_id)
        if memory is None or memory.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family memory not found")
        return memory

    def _can_read_memory(self, memory: FamilyMemoryRow, user: User) -> bool:
        decision = self.permissions.evaluate(
            family_id=memory.family_id,
            user=user,
            action="memory.read",
            asset=memory,
        )
        return decision.effect is PermissionEffect.ALLOW


def _extract_relation_terms(query: str) -> list[str]:
    """Return Chinese family terms present in ``query`` in their surface order."""
    from homemind.infra.family.resolvers.relationship import (
        SUPPORTED_RELATION_TERMS,
    )

    found: list[str] = []
    cursor = 0
    while cursor < len(query):
        matched_term = None
        for term in SUPPORTED_RELATION_TERMS:
            if query.startswith(term, cursor):
                matched_term = term
                break
        if matched_term is None:
            cursor += 1
            continue
        if not found or found[-1] != matched_term:
            found.append(matched_term)
        cursor += len(matched_term)
    return found
