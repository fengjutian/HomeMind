"""Index a household's own files, and answer questions against them.

The contract that matters here is the boundary between *citing* and
*asserting*. A document's contents are never written into
``homemind_family_memories``. An agent answering "where is the warranty?"
cites a chunk; an agent answering "when is Dad's birthday?" reads a
memory the family actually recorded. Merging the two would let a receipt
or a forwarded message become a "fact" with no author.

Search re-applies visibility at query time. Filtering only at index time
would mean a member who later loses access keeps finding the content
through a previously built index.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from homemind.infra.db.repos.knowledge_documents import (
    DOC_STATUS_FAILED,
    DOC_STATUS_INDEXED,
    DOC_STATUS_UNSUPPORTED,
    KnowledgeDocumentRow,
    KnowledgeRepo,
    chunk_hash,
)
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.knowledge_parsers import (
    PARSER_VERSION,
    ParseFailure,
    UnsupportedDocument,
    parse,
    parser_for,
)
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.ocr import OcrProvider
from homemind.infra.family.privacy import (
    DataCategory,
    ExternalOperation,
    ExternalProcessingGuard,
)
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)


class TextEmbeddingProvider(Protocol):
    """The subset of an embedding provider this pipeline needs."""

    name: str

    def embed_text(self, text: str) -> Any:  # noqa: ANN401 - provider shape varies
        ...


@dataclass(frozen=True)
class KnowledgeHit:
    """One search result, carrying enough context to cite it."""

    document_id: str
    chunk_id: str
    asset_id: str
    asset_name: str | None
    text: str
    locator: str | None
    page_number: int | None
    score: int

    def as_citation(self) -> dict[str, Any]:
        """The shape an agent should quote back to the user."""
        return {
            "asset_id": self.asset_id,
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "file_name": self.asset_name,
            "locator": self.locator,
            "page": self.page_number,
            "excerpt": self.text[:300],
        }


class KnowledgeManager:
    """Parse, index, and search a family's documents."""

    def __init__(
        self,
        family: FamilyManager,
        assets: FamilyAssetManager,
        repo: KnowledgeRepo,
    ) -> None:
        self.family = family
        self.assets = assets
        self.repo = repo

    # ------------------------------------------------------------ indexing

    def index_asset(
        self,
        family_id: str,
        asset_id: str,
        user: User,
        *,
        embedding: TextEmbeddingProvider | None = None,
        ocr_provider: OcrProvider | None = None,
        privacy_guard: ExternalProcessingGuard | None = None,
    ) -> KnowledgeDocumentRow:
        """Parse one registered asset into chunks.

        Skips work that is already done: an unchanged file with the same
        parser version keeps its chunks and its vectors. The failure modes
        are recorded rather than raised, because a family's library will
        always contain something this build cannot read, and a single
        unreadable file must not look like an indexing outage.

        ``ocr_provider`` enables scanned PDFs. An *external* engine is
        gated through ``privacy_guard`` before a single page leaves the
        household — recognition sends the family's documents to a third
        party, which is exactly the thing the family setting exists to
        prevent.
        """
        asset = self.assets.get(family_id, asset_id, user)
        path = _local_path(asset.uri)

        if parser_for(path) is None:
            return self.repo.upsert_document(
                family_id=family_id,
                asset_id=asset_id,
                content_hash=asset.content_hash,
                parser_version=PARSER_VERSION,
                mime_type=asset.mime_type,
                name=asset.name,
                status=DOC_STATUS_UNSUPPORTED,
                error=f"{path.suffix or 'unknown'} is not a supported format",
            )

        engine = self._authorize_ocr(family_id, user, asset_id, ocr_provider, privacy_guard)
        try:
            parsed = parse(path, ocr_provider=engine)
        except UnsupportedDocument as exc:
            return self.repo.upsert_document(
                family_id=family_id,
                asset_id=asset_id,
                content_hash=asset.content_hash,
                parser_version=PARSER_VERSION,
                mime_type=asset.mime_type,
                name=asset.name,
                status=DOC_STATUS_UNSUPPORTED,
                error=str(exc),
            )
        except ParseFailure as exc:
            return self.repo.upsert_document(
                family_id=family_id,
                asset_id=asset_id,
                content_hash=asset.content_hash,
                parser_version=PARSER_VERSION,
                mime_type=asset.mime_type,
                name=asset.name,
                status=DOC_STATUS_FAILED,
                error=str(exc),
            )

        existing = self.repo.get_document_by_asset(asset_id)
        if (
            existing is not None
            and existing.content_hash == asset.content_hash
            and existing.parser_version == parsed.parser_version
            and existing.status == DOC_STATUS_INDEXED
        ):
            # Nothing changed since the last pass.
            return existing

        document = self.repo.upsert_document(
            family_id=family_id,
            asset_id=asset_id,
            content_hash=asset.content_hash,
            parser_version=parsed.parser_version,
            mime_type=asset.mime_type,
            name=asset.name,
            status=DOC_STATUS_INDEXED,
        )
        reusable = self.repo.existing_chunk_vectors(document.id)
        self.repo.replace_chunks(
            document_id=document.id,
            family_id=family_id,
            asset_id=asset_id,
            chunks=[_to_row(chunk, embedding, reusable) for chunk in parsed.chunks],
        )
        logger.info(
            "KnowledgeManager: indexed %s as %d chunk(s)",
            asset.name,
            len(parsed.chunks),
        )
        return self.repo.get_document(document.id)  # type: ignore[return-value]

    def forget_asset(self, family_id: str, asset_id: str) -> bool:
        """Drop a document and its chunks when its file goes away."""
        document = self.repo.get_document_by_asset(asset_id)
        if document is None or document.family_id != family_id:
            return False
        return self.repo.delete_document(document.id)

    def _authorize_ocr(
        self,
        family_id: str,
        user: User,
        asset_id: str,
        ocr_provider: OcrProvider | None,
        privacy_guard: ExternalProcessingGuard | None,
    ) -> OcrProvider | None:
        """Decide whether recognition may run on this document.

        A local engine is simply allowed. An external one has to clear
        the family's privacy settings first, and is recorded when it
        does — a family that opted in should be able to see that their
        documents left the household.

        Returns ``None`` when recognition is refused or no engine was
        supplied, which the parser turns into an honest ``UNSUPPORTED``
        rather than an empty index entry.
        """
        if ocr_provider is None:
            return None
        if not getattr(ocr_provider, "is_external", False):
            return ocr_provider
        if privacy_guard is None:
            logger.warning(
                "KnowledgeManager: external OCR provider %s refused; no privacy guard wired",
                ocr_provider.name,
            )
            return None
        decision = privacy_guard.authorize_external_call(
            family_id,
            user,
            operation=ExternalOperation.OCR,
            provider_id=ocr_provider.name,
            data_categories=frozenset({DataCategory.DOCUMENT.value}),
            asset_id=asset_id,
        )
        if not decision.allowed:
            logger.info(
                "KnowledgeManager: external OCR refused for asset %s: %s",
                asset_id,
                decision.reason,
            )
            return None
        return ocr_provider

    # -------------------------------------------------------------- search

    def search(
        self,
        family_id: str,
        user: User,
        *,
        query: str,
        limit: int = 10,
    ) -> list[KnowledgeHit]:
        """Search a family's documents, filtered to what the user may read.

        Visibility is resolved *here*, not at index time: a member who
        loses access to a private space must stop finding its contents in
        a previously built index.
        """
        self.family.require_access(family_id, user)
        visible = [
            asset.id
            for asset in self.assets.search(
                family_id,
                user,
                limit=1000,
            )
            if asset.asset_type == "DOCUMENT"
        ]
        if not visible:
            return []
        names = {asset.id: asset.name for asset in self.assets.search(family_id, user, limit=1000)}
        results = self.repo.search_chunks(
            family_id,
            query,
            asset_ids=visible,
            limit=limit,
        )
        return [
            KnowledgeHit(
                document_id=chunk.document_id,
                chunk_id=chunk.id,
                asset_id=chunk.asset_id,
                asset_name=names.get(chunk.asset_id),
                text=chunk.text,
                locator=chunk.locator,
                page_number=chunk.page_number,
                score=score,
            )
            for chunk, score in results
        ]

    def list_documents(
        self,
        family_id: str,
        user: User,
        *,
        limit: int = 100,
    ) -> list[KnowledgeDocumentRow]:
        """Index status for the Files page."""
        self.family.require_access(family_id, user)
        visible = {asset.id for asset in self.assets.search(family_id, user, limit=1000)}
        return [
            document
            for document in self.repo.list_documents(family_id, limit=limit * 4)
            if document.asset_id in visible
        ]


def _to_row(
    chunk: Any,
    embedding: TextEmbeddingProvider | None,
    reusable: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Turn a parsed chunk into a row payload.

    Reuses a stored vector when the chunk's text is unchanged, so editing
    one paragraph of a document does not re-embed every other chunk.
    """
    digest = chunk_hash(chunk.text)
    row: dict[str, Any] = {
        "ordinal": chunk.ordinal,
        "text": chunk.text,
        "locator": chunk.locator,
        "page_number": chunk.page_number,
        "content_hash": digest,
    }
    cached = reusable.get(digest)
    if cached is not None:
        row.update(cached)
        return row
    if embedding is not None:
        vector = [float(value) for value in embedding.embed_text(chunk.text)]
        if vector:
            row.update(
                embedding=vector,
                embedding_model=embedding.name,
                embedding_dimensions=len(vector),
                embedding_version=1,
            )
    return row


def _local_path(uri: str) -> Path:
    """Resolve an asset URI to a local file.

    Only ``file://`` is accepted: fetching a remote asset belongs to the
    storage backend, not to a parser that assumes a path on this host.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID,
            "knowledge indexing currently requires a local file asset",
        )
    return Path(url2pathname(unquote(parsed.path)))


__all__ = [
    "KnowledgeHit",
    "KnowledgeManager",
    "TextEmbeddingProvider",
]
