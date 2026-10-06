"""Resolve Chinese family terms to ``FamilyMemberRow`` candidates."""

from __future__ import annotations

from dataclasses import dataclass

from homemind.infra.db.repos.families import (
    FamilyMemberRow,
    FamilyRelationshipRow,
    FamilyRepo,
)
from homemind.infra.family.resolvers.models import ResolutionCandidate


# Term → list of relationship-type pairs that connect current→target.
# A relationship edge stored as (from=A, to=B, type=T) means A is T of B.
# Resolving from the current member's POV means we treat the relationship
# bidirectionally: a PARENT edge (A→B) connects current=B to target=A
# (and current=A to target=B in the other direction).
_RELATION_TERMS: dict[str, list[tuple[str, str]]] = {
    # term            → pairs of (direction-from-current, edge-type)
    # Convention: family edges are stored with the *senior* end as
    # ``from`` (e.g. parent → child, grandparent → grandchild, spouse ↔ spouse,
    # sibling ↔ sibling). Direction "up" looks at the *from* side of an edge
    # where the *to* side is the current member; direction "down" looks at
    # the *to* side of an edge where the *from* side is the current member.
    "妈妈": [("up", "PARENT")],
    "我妈": [("up", "PARENT")],
    "母亲": [("up", "PARENT")],
    "爸爸": [("up", "PARENT")],
    "我爸": [("up", "PARENT")],
    "父亲": [("up", "PARENT")],
    "老婆": [("side", "SPOUSE")],
    "妻子": [("side", "SPOUSE")],
    "太太": [("side", "SPOUSE")],
    "老公": [("side", "SPOUSE")],
    "丈夫": [("side", "SPOUSE")],
    "儿子": [("down", "PARENT")],
    "女儿": [("down", "PARENT")],
    "孩子": [("down", "PARENT")],
    "哥哥": [("side", "SIBLING")],
    "姐姐": [("side", "SIBLING")],
    "弟弟": [("side", "SIBLING")],
    "妹妹": [("side", "SIBLING")],
    "爷爷": [("up", "GRANDPARENT")],
    "奶奶": [("up", "GRANDPARENT")],
    "外公": [("up", "GRANDPARENT")],
    "外婆": [("up", "GRANDPARENT")],
    "孙子": [("down", "GRANDPARENT")],
    "孙女": [("down", "GRANDPARENT")],
}


# Public read-only tuple of all supported terms, longest first so substring
# extraction prefers "我的妈妈" before "我".
SUPPORTED_RELATION_TERMS: tuple[str, ...] = tuple(
    sorted(_RELATION_TERMS.keys(), key=len, reverse=True)
)


@dataclass(frozen=True)
class ResolvedMember:
    candidate: ResolutionCandidate
    member: FamilyMemberRow


class RelationshipResolver:
    """Resolve family-term references from the **current member's** perspective."""

    def __init__(self, repo: FamilyRepo) -> None:
        self.repo = repo

    def find(self, term: str, *, current_member_id: str) -> list[ResolvedMember]:
        """Return all member candidates that match ``term`` from ``current_member_id``'s view.

        Always returns a list. A single match is unambiguous; multiple
        matches (e.g. two children) are flagged as ambiguity by the caller.
        """
        cleaned = term.strip()
        if not cleaned:
            return []
        pairs = _RELATION_TERMS.get(cleaned.casefold()) or _RELATION_TERMS.get(cleaned)
        if pairs is None:
            # Plain display-name lookup (e.g. user typed "李雷").
            return self._by_display_name(cleaned)
        candidates: list[ResolvedMember] = []
        for direction, edge_type in pairs:
            candidates.extend(self._by_relationship(current_member_id, direction, edge_type))
        return candidates

    def list_terms(self) -> list[str]:
        return sorted(_RELATION_TERMS.keys())

    # ------------------------------------------------------------- internals

    def _by_display_name(self, name: str) -> list[ResolvedMember]:
        normalized = name.casefold()
        members = [
            member
            for member in self.repo.list_members_by_display_name(normalized)
        ]
        return [
            ResolvedMember(
                candidate=ResolutionCandidate(
                    entity_type="MEMBER",
                    entity_id=member.id,
                    label=member.display_name,
                    confidence=0.9,
                    reason="display_name_match",
                    metadata={"display_name": member.display_name},
                ),
                member=member,
            )
            for member in members
        ]

    def _by_relationship(
        self, current_member_id: str, direction: str, edge_type: str
    ) -> list[ResolvedMember]:
        relationships = self.repo.list_relationships_for_member(current_member_id)
        out: dict[str, ResolvedMember] = {}
        for relationship in relationships:
            if relationship.relationship_type != edge_type:
                continue
            target_id = self._target_id(relationship, current_member_id, direction)
            if target_id is None:
                continue
            member = self.repo.get_member(target_id)
            if member is None:
                continue
            existing = out.get(target_id)
            confidence = 0.95
            if existing is not None and existing.candidate.confidence >= confidence:
                continue
            out[target_id] = ResolvedMember(
                candidate=ResolutionCandidate(
                    entity_type="MEMBER",
                    entity_id=member.id,
                    label=member.display_name,
                    confidence=confidence,
                    reason=f"relationship:{edge_type}:{direction}",
                    metadata={
                        "current_member_id": current_member_id,
                        "edge_type": edge_type,
                        "direction": direction,
                        "relationship_id": relationship.id,
                    },
                ),
                member=member,
            )
        return list(out.values())

    @staticmethod
    def _target_id(
        relationship: FamilyRelationshipRow,
        current_member_id: str,
        direction: str,
    ) -> str | None:
        """Return the *other* end of the edge that satisfies ``direction``.

        A relationship edge ``(from=A, to=B, type=PARENT)`` means A is the
        parent of B. Resolving "current's parent" therefore needs the
        *from* side of any PARENT edge whose other end is ``current``.
        Resolving "current's child" needs the *to* side. Spouse / sibling
        edges are symmetric so either side works.
        """
        edge_type = relationship.relationship_type
        # Sibling and spouse edges are symmetric.
        if edge_type in {"SPOUSE", "SIBLING"}:
            if relationship.from_member_id == current_member_id:
                return relationship.to_member_id
            if relationship.to_member_id == current_member_id:
                return relationship.from_member_id
            return None
        if edge_type == "PARENT":
            # "current's parent" = the *from* side of a PARENT edge where
            # the *to* side is current. Equivalently, the *to* side of a
            # CHILD edge from current.
            if direction == "up" and relationship.to_member_id == current_member_id:
                return relationship.from_member_id
            if direction == "down" and relationship.from_member_id == current_member_id:
                return relationship.to_member_id
            return None
        if edge_type == "CHILD":
            if direction == "up" and relationship.from_member_id == current_member_id:
                return relationship.to_member_id
            if direction == "down" and relationship.to_member_id == current_member_id:
                return relationship.from_member_id
            return None
        if edge_type == "GRANDPARENT":
            # current's grandparent = the from-side of GRANDPARENT edge whose
            # to-side is current.
            if direction == "up" and relationship.to_member_id == current_member_id:
                return relationship.from_member_id
            return None
        if edge_type == "GRANDCHILD":
            if direction == "down" and relationship.from_member_id == current_member_id:
                return relationship.to_member_id
            return None
        return None


__all__ = [
    "RelationshipResolver",
    "ResolvedMember",
    "SUPPORTED_RELATION_TERMS",
]