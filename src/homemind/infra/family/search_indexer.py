"""Keep the unified search index in sync with the domain tables.

The index is a *projection*: every write path that changes a searchable
entity must call the matching ``index_*`` method, and archive / delete
must remove the document. ``REFRESHED_DOCUMENT_STATUS`` records when a
document was last confirmed current so a background sweep can re-index
rows that drifted.

Nothing here rebuilds from scratch — a full ``REINDEX`` job does that.
These methods are the incremental path and are all idempotent, so
calling one twice is a no-op the second time.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from homemind.infra.db.repos.families import FamilyMemberRow, FamilyRepo
from homemind.infra.db.repos.family_albums import FamilyAlbumRepo
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.db.repos.family_context import (
    FamilyContextRepo,
    FamilyEventRow,
    FamilyMemoryRow,
)
from homemind.infra.db.repos.photo_intelligence import PhotoIntelligenceRepo
from homemind.infra.db.repos.search_index import (
    KIND_ALBUM,
    KIND_ASSET,
    KIND_EVENT,
    KIND_LOCATION,
    KIND_MEMBER,
    KIND_MEMORY,
    KIND_OBJECT,
    KIND_PHOTO_DESCRIPTION,
    KIND_SCENE,
    VISIBILITY_FAMILY,
    VISIBILITY_PRIVATE,
    SearchIndexRepo,
)

logger = logging.getLogger(__name__)

STATUS_INDEXED = "ACTIVE"
STATUS_ARCHIVED = "ARCHIVED"
STATUS_DELETED = "DELETED"
# ``STATUS_INDEXED`` is the *query* vocabulary ("this document is in
# the default result set"), not the domain one. An indexed event stays
# ``ACTIVE`` because that is what the family tables call it; keeping
# the two namespaces aligned is what stops a freshly-written document
# from being invisible to every search.



def _member_visibility(member: FamilyMemberRow) -> str:
    return VISIBILITY_PRIVATE if member.status != "ACTIVE" else VISIBILITY_FAMILY


class FamilySearchIndexer:
    """Incremental index maintenance for one family's searchable data."""

    def __init__(
        self,
        index: SearchIndexRepo,
        *,
        family_repo: FamilyRepo,
        context_repo: FamilyContextRepo,
        asset_repo: FamilyAssetRepo | None = None,
        album_repo: FamilyAlbumRepo | None = None,
        photo_repo: PhotoIntelligenceRepo | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
        embedding_version: int = 1,
    ) -> None:
        self.index = index
        self.family_repo = family_repo
        self.context_repo = context_repo
        self.asset_repo = asset_repo
        self.album_repo = album_repo
        self.photo_repo = photo_repo
        self.embedding_model = embedding_model
        self.embedding_dimensions = embedding_dimensions
        self.embedding_version = embedding_version

    # -------------------------------------------------------------- members

    def index_member(self, family_id: str, member: FamilyMemberRow) -> str:
        return self.index.upsert_document(
            family_id,
            kind=KIND_MEMBER,
            entity_id=member.id,
            title=member.display_name,
            text=f"{member.display_name} {member.role} {member.birthday or ''}".strip(),
            visibility=_member_visibility(member),
            status=STATUS_INDEXED if member.status == "ACTIVE" else STATUS_ARCHIVED,
            updated_at=member.updated_at,
        )

    def remove_member(self, family_id: str, member_id: str) -> bool:
        return self.index.remove_document(family_id, KIND_MEMBER, member_id)

    # --------------------------------------------------------------- events

    def index_event(self, family_id: str, event: FamilyEventRow) -> str:
        return self.index.upsert_document(
            family_id,
            kind=KIND_EVENT,
            entity_id=event.id,
            title=event.title,
            text=" ".join(
                part
                for part in (
                    event.title,
                    event.description,
                    event.location or "",
                    event.event_type,
                )
                if part
            ),
            visibility=VISIBILITY_FAMILY,
            # ``FamilyEventRow`` has no status column; ``list_events``
            # already filters to ACTIVE rows in SQL.
            status=STATUS_INDEXED,
            captured_at=event.start_at,
            importance=0.6,
            updated_at=event.updated_at,
        )

    def remove_event(self, family_id: str, event_id: str) -> bool:
        return self.index.remove_document(family_id, KIND_EVENT, event_id)

    # -------------------------------------------------------------- memories

    def index_memory(self, family_id: str, memory: FamilyMemoryRow) -> str:
        visibility = (
            VISIBILITY_PRIVATE
            if memory.visibility == "PRIVATE"
            else VISIBILITY_FAMILY
        )
        return self.index.upsert_document(
            family_id,
            kind=KIND_MEMORY,
            entity_id=memory.id,
            title=memory.memory_type,
            text=memory.content,
            visibility=visibility,
            status=STATUS_INDEXED if memory.status == "ACTIVE" else STATUS_ARCHIVED,
            owner_member_id=memory.subject_id,
            importance=memory.importance,
            captured_at=memory.created_at,
            updated_at=memory.updated_at,
        )

    def remove_memory(self, family_id: str, memory_id: str) -> bool:
        return self.index.remove_document(family_id, KIND_MEMORY, memory_id)

    # --------------------------------------------------------------- assets

    def index_asset(self, family_id: str, asset: Any) -> str:
        metadata: dict[str, Any] = {}
        try:
            metadata = json.loads(asset.metadata_json)
        except (TypeError, ValueError):
            metadata = {}
        text = " ".join(
            part
            for part in (
                asset.name,
                str(metadata.get("relative_path") or ""),
                asset.mime_type,
                asset.asset_type,
            )
            if part
        )
        document_id = self.index.upsert_document(
            family_id,
            kind=KIND_ASSET,
            entity_id=asset.id,
            title=asset.asset_type,
            text=text,
            visibility=(
                VISIBILITY_PRIVATE if asset.visibility == "PRIVATE" else VISIBILITY_FAMILY
            ),
            status=STATUS_INDEXED if asset.status == "INDEXED" else STATUS_ARCHIVED,
            space_id=asset.space_id,
            captured_at=asset.captured_at,
            updated_at=asset.updated_at,
        )
        self._index_photo_documents(family_id, asset, document_id)
        return document_id

    def _index_photo_documents(self, family_id: str, asset: Any, _: str) -> None:
        """Fan a photo out into the object / scene / location kinds.

        Those are separate search kinds in the spec, so each gets its
        own document rather than being folded into the asset row — a
        query restricted to ``kinds=LOCATION`` should not drag every
        photo back with it.
        """
        if self.photo_repo is None:
            return
        analysis = self.photo_repo.get(asset.id)
        if analysis is None:
            return
        shared = {
            "visibility": (
                VISIBILITY_PRIVATE if asset.visibility == "PRIVATE" else VISIBILITY_FAMILY
            ),
            "status": STATUS_INDEXED,
            "space_id": asset.space_id,
            "captured_at": asset.captured_at,
            "updated_at": asset.updated_at,
        }
        if analysis.description:
            self.index.upsert_document(
                family_id,
                kind=KIND_PHOTO_DESCRIPTION,
                entity_id=f"{asset.id}:description",
                title=asset.name,
                text=analysis.description,
                **shared,
            )
        objects = _as_list(getattr(analysis, "objects", None))
        if objects:
            self.index.upsert_document(
                family_id,
                kind=KIND_OBJECT,
                entity_id=f"{asset.id}:objects",
                title="objects",
                text=" ".join(str(item) for item in objects),
                **shared,
            )
        scenes = _as_list(getattr(analysis, "scenes", None))
        if scenes:
            self.index.upsert_document(
                family_id,
                kind=KIND_SCENE,
                entity_id=f"{asset.id}:scenes",
                title="scenes",
                text=" ".join(str(item) for item in scenes),
                **shared,
            )
        location_name = getattr(analysis, "location_name", None)
        if location_name:
            self.index.upsert_document(
                family_id,
                kind=KIND_LOCATION,
                entity_id=f"{asset.id}:location",
                title=str(location_name),
                text=str(location_name),
                **shared,
            )

    def remove_asset(self, family_id: str, asset_id: str) -> bool:
        """Remove the asset and every photo-derived document it owns."""
        removed = self.index.remove_document(family_id, KIND_ASSET, asset_id)
        for kind in (KIND_PHOTO_DESCRIPTION, KIND_OBJECT, KIND_SCENE, KIND_LOCATION):
            self.index.remove_document(family_id, kind, f"{asset_id}:{kind.split('_')[-1].lower()}")
        return removed

    def archive_asset(self, family_id: str, asset_id: str) -> bool:
        """Take an archived asset out of the default result set without
        deleting its row — a restore puts it back."""
        from homemind.infra.db.repos.search_index import document_id_for  # noqa: PLC0415

        return self.index.mark_document_status(
            document_id_for(family_id, KIND_ASSET, asset_id), STATUS_ARCHIVED,
        )

    # --------------------------------------------------------------- albums

    def index_album(self, family_id: str, album: Any) -> str:
        return self.index.upsert_document(
            family_id,
            kind=KIND_ALBUM,
            entity_id=album.id,
            title=str(album.name),
            text=" ".join(
                part for part in (album.name, album.description or "") if part
            ),
            visibility=VISIBILITY_FAMILY,
            status=STATUS_INDEXED,
            updated_at=getattr(album, "updated_at", 0),
        )

    def remove_album(self, family_id: str, album_id: str) -> bool:
        return self.index.remove_document(family_id, KIND_ALBUM, album_id)

    # ------------------------------------------------------------ embeddings

    def attach_embedding(
        self,
        family_id: str,
        *,
        kind: str,
        entity_id: str,
        vector: list[float],
        model: str | None = None,
        dimensions: int | None = None,
        version: int | None = None,
    ) -> str:
        """Store a vector together with its provenance.

        Provenance is not optional metadata: without it a later query
        would happily compare vectors from two different models.
        """
        resolved_model = model or self.embedding_model
        resolved_dimensions = dimensions or self.embedding_dimensions or len(vector)
        resolved_version = version if version is not None else self.embedding_version
        document_id = self.index.upsert_document(
            family_id,
            kind=kind,
            entity_id=entity_id,
            title="",
            text="",
            embedding=vector,
            embedding_model=resolved_model,
            embedding_dimensions=resolved_dimensions,
            embedding_version=resolved_version,
        )
        return document_id

    def drop_embeddings_for_model(self, family_id: str, model: str) -> int:
        """Invalidate one provider's vectors so a REINDEX job can rebuild
        them, leaving other models intact."""
        return self.index.clear_embeddings(family_id, model=model)

    # ------------------------------------------------------------- rebuild

    def reindex_family(self, family_id: str) -> int:
        """Rebuild every document for one family.

        This is the body of a ``REINDEX`` job: it is safe to interrupt
        because each ``upsert_document`` is idempotent.
        """
        written = 0
        for member in self.family_repo.list_members(family_id):
            self.index_member(family_id, member)
            written += 1
        for event in self.context_repo.list_events(family_id):
            self.index_event(family_id, event)
            written += 1
        for memory in self.context_repo.list_all_memories(family_id):
            self.index_memory(family_id, memory)
            written += 1
        if self.asset_repo is not None:
            for asset in self.asset_repo.search(family_id, limit=10000):
                self.index_asset(family_id, asset)
                written += 1
        logger.info("FamilySearchIndexer: reindexed %d documents", written)
        return written


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return [value] if value else []
        return list(parsed) if isinstance(parsed, list) else [str(parsed)]
    return [str(value)]


__all__ = [
    "STATUS_ARCHIVED",
    "STATUS_DELETED",
    "STATUS_INDEXED",
    "FamilySearchIndexer",
]
