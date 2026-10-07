"""HTTP surface for family document indexing and search.

Read paths (``search``, ``list``) are open to any family member subject
to the usual family-access check; ``index`` and ``forget`` are
manager-only, because they write rows on behalf of the whole family.

The search response returns citation-shaped hits rather than raw text, so
a caller can point a user at a page or paragraph instead of quoting a
blob with no origin.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.knowledge import KnowledgeManager
from homemind.infra.family.manager import FamilyManager
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class KnowledgeCitation(BaseModel):
    """Where a search hit came from, in a form a user can follow."""

    asset_id: str
    document_id: str
    chunk_id: str
    file_name: str | None = None
    locator: str | None = Field(
        default=None,
        description="Human locator, e.g. 'page 3' or 'paragraph 2'.",
    )
    page: int | None = None
    excerpt: str = Field(description="First 300 characters of the matching chunk.")


class KnowledgeSearchResponse(BaseModel):
    query: str
    hits: list[KnowledgeCitation]


class KnowledgeDocumentResponse(BaseModel):
    document_id: str
    asset_id: str
    content_hash: str
    parser_version: str
    name: str | None = None
    mime_type: str | None = None
    status: str = Field(
        description="INDEXED | UNSUPPORTED | FAILED. A file this build "
        "cannot read is recorded, not silently omitted.",
    )
    error: str | None = None
    chunk_count: int
    indexed_at: int


def _services(server: OctopServer) -> HomeMindServices:
    assert server.services is not None
    run_migrations(server.services.db)
    return HomeMindServices.from_pool(server.services.db)


def _manager(server: OctopServer) -> KnowledgeManager:
    services = _services(server)
    families = FamilyManager(services.family_repo)
    return KnowledgeManager(
        families,
        FamilyAssetManager(services.family_repo, services.family_asset_repo),
        services.knowledge_repo,
    )


@router.post(
    "/{family_id}/knowledge/index/{asset_id}",
    response_model=KnowledgeDocumentResponse,
    summary="Parse and index one family document",
    description=(
        "Reads the file behind a registered asset id — never a "
        "caller-supplied path — splits it into chunks, and records the "
        "outcome. A format this build cannot parse is stored as "
        "UNSUPPORTED with a reason instead of raising, so one unreadable "
        "file in a large library is visible rather than an outage. "
        "Manager-only."
    ),
)
async def index_document(
    family_id: str,
    asset_id: str,
    server: Server,
    user: CurrentUser,
) -> KnowledgeDocumentResponse:
    services = _services(server)
    families = FamilyManager(services.family_repo)
    families.require_manager(family_id, user)
    document = _manager(server).index_asset(family_id, asset_id, user)
    return KnowledgeDocumentResponse(
        document_id=document.id,
        asset_id=document.asset_id,
        content_hash=document.content_hash,
        parser_version=document.parser_version,
        name=document.name,
        mime_type=document.mime_type,
        status=document.status,
        error=document.error,
        chunk_count=document.chunk_count,
        indexed_at=document.indexed_at,
    )


@router.delete(
    "/{family_id}/knowledge/documents/{document_id}",
    status_code=204,
    summary="Forget an indexed document",
    description=(
        "Removes the document and its chunks. Use when the underlying "
        "file is gone; chunks cascade with the document row."
    ),
)
async def forget_document(
    family_id: str,
    document_id: str,
    server: Server,
    user: CurrentUser,
) -> None:
    services = _services(server)
    families = FamilyManager(services.family_repo)
    families.require_manager(family_id, user)
    if not _manager(server).repo.delete_document(document_id):
        from octop.infra.errors import ErrorCode, OctopError

        raise OctopError(ErrorCode.NOT_FOUND, "knowledge document not found")


@router.get(
    "/{family_id}/knowledge/search",
    response_model=KnowledgeSearchResponse,
    summary="Search the family's own documents",
    description=(
        "Returns citation-shaped hits: each carries the asset id, file "
        "name and a locator so an answer can point back to the source. "
        "Visibility is re-checked at query time, so a member who loses "
        "access stops finding the content."
    ),
)
async def search_knowledge(
    family_id: str,
    server: Server,
    user: CurrentUser,
    q: Annotated[str, Query(min_length=1, description="Query text.")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> KnowledgeSearchResponse:
    hits = _manager(server).search(family_id, user, query=q, limit=limit)
    return KnowledgeSearchResponse(
        query=q,
        hits=[KnowledgeCitation(**hit.as_citation()) for hit in hits],
    )


@router.get(
    "/{family_id}/knowledge/documents",
    response_model=list[KnowledgeDocumentResponse],
    summary="List indexed documents and their status",
    description=("Index state for the Files page: what was parsed, when, and why anything failed."),
)
async def list_documents(
    family_id: str,
    server: Server,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[KnowledgeDocumentResponse]:
    return [
        KnowledgeDocumentResponse(
            document_id=document.id,
            asset_id=document.asset_id,
            content_hash=document.content_hash,
            parser_version=document.parser_version,
            name=document.name,
            mime_type=document.mime_type,
            status=document.status,
            error=document.error,
            chunk_count=document.chunk_count,
            indexed_at=document.indexed_at,
        )
        for document in _manager(server).list_documents(family_id, user, limit=limit)
    ]


__all__ = [
    "router",
    "KnowledgeCitation",
    "KnowledgeDocumentResponse",
    "KnowledgeSearchResponse",
]
