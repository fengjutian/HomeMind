"""Family context resolvers (Stage 2).

Splits the historical ``FamilyContextManager.resolve`` heuristic into
focused resolvers:

* ``relationship`` — turns Chinese family terms ("妈妈", "老婆", "孩子") into
  concrete ``FamilyMemberRow`` candidates using the **current member's**
  relational perspective.
* ``time_range`` — turns natural-language time expressions ("去年", "上周",
  "今年春节", "某成员生日") into ``(start_at, end_at)`` pairs anchored to
  the family timezone.
* ``event`` — scores family events by title / event_type / location /
  description / members / time range and never silently falls back to
  "return everything".

All resolvers return ``ResolutionCandidate`` lists so the caller can flag
ambiguities (multiple matches) rather than picking arbitrarily.
"""

from homemind.infra.family.resolvers.asset_query import AssetQueryResolver
from homemind.infra.family.resolvers.event import EventResolver
from homemind.infra.family.resolvers.models import (
    ResolutionCandidate,
    ResolvedTimeRange,
)
from homemind.infra.family.resolvers.relationship import (
    RelationshipResolver,
    SUPPORTED_RELATION_TERMS,
)
from homemind.infra.family.resolvers.time_range import TimeRangeResolver

__all__ = [
    "AssetQueryResolver",
    "EventResolver",
    "RelationshipResolver",
    "ResolutionCandidate",
    "ResolvedTimeRange",
    "SUPPORTED_RELATION_TERMS",
    "TimeRangeResolver",
]