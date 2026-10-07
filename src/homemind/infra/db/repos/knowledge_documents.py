"""Parsed family documents and their retrievable chunks.

Three tables, one idea: a document is the source of truth for "what this
file is", a chunk is the unit a query actually matches, and the embedding
provenance lives on the chunk so a re-chunk can never leave a stale
vector beside a fresh one.

Nothing here promotes document content into family memory. A document is
something the agent cites; a memory is something the family asserted.
Collapsing the two would make a receipt or a school notice indistinguishable
from a fact the parents chose to remember.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

#: Document lifecycle.
DOC_STATUS_INDEXED = "INDEXED"
DOC_STATUS_FAILED = "FAILED"
DOC_STATUS_UNSUPPORTED = "UNSUPPORTED"

DOC_STATUSES: frozenset[str] = frozenset(
    {DOC_STATUS_INDEXED, DOC_STATUS_FAILED, DOC_STATUS_UNSUPPORTED}
)


@dataclass(frozen=True)
class KnowledgeDocumentRow:
    id: str
    pk: int
    family_id: str
    asset_id: str
    content_hash: str
    parser_version: str
    mime_type: str | None
    name: str | None
    status: str
    error: str | None
    chunk_count: int
    indexed_at: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> KnowledgeDocumentRow:
        # ``sqlite3.Row`` iterates values, not column names.
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )

        def _opt_int(key: str) -> int | None:
            value = data.get(key)
            return int(value) if value is not None else None

        def _opt_str(key: str) -> str | None:
            value = data.get(key)
            return str(value) if value is not None else None

        return cls(
            id=str(data["document_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            asset_id=str(data["asset_id"]),
            content_hash=str(data["content_hash"]),
            parser_version=str(data["parser_version"]),
            mime_type=_opt_str("mime_type"),
            name=_opt_str("name"),
            status=str(data["status"]),
            error=_opt_str("error"),
            chunk_count=int(data["chunk_count"]),
            indexed_at=int(data["indexed_at"]),
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
        )


@dataclass(frozen=True)
class KnowledgeChunkRow:
    id: str
    pk: int
    document_id: str
    family_id: str
    asset_id: str
    ordinal: int
    text: str
    locator: str | None
    page_number: int | None
    content_hash: str
    embedding: tuple[float, ...] | None
    embedding_model: str | None
    embedding_dimensions: int | None
    embedding_version: int | None
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> KnowledgeChunkRow:
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )

        def _opt_int(key: str) -> int | None:
            value = data.get(key)
            return int(value) if value is not None else None

        embedding_raw = data.get("embedding")
        embedding = tuple(float(v) for v in json.loads(embedding_raw)) if embedding_raw else None
        return cls(
            id=str(data["chunk_id"]),
            pk=int(data["id"]),
            document_id=str(data["document_id"]),
            family_id=str(data["family_id"]),
            asset_id=str(data["asset_id"]),
            ordinal=int(data["ordinal"]),
            text=str(data["text"]),
            locator=data.get("locator"),
            page_number=_opt_int("page_number"),
            content_hash=str(data["content_hash"]),
            embedding=embedding,
            embedding_model=data.get("embedding_model"),
            embedding_dimensions=_opt_int("embedding_dimensions"),
            embedding_version=_opt_int("embedding_version"),
            created_at=int(data["created_at"]),
        )


def chunk_hash(text: str) -> str:
    """Stable content hash for one chunk, used to skip re-embedding."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class KnowledgeRepo:
    """SQL access for the document and chunk tables."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ---------------------------------------------------------- documents

    def get_document_by_asset(self, asset_id: str) -> KnowledgeDocumentRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_knowledge_documents WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
        return KnowledgeDocumentRow.from_row(row) if row else None

    def upsert_document(
        self,
        *,
        family_id: str,
        asset_id: str,
        content_hash: str,
        parser_version: str,
        mime_type: str | None,
        name: str | None,
        status: str = DOC_STATUS_INDEXED,
        error: str | None = None,
    ) -> KnowledgeDocumentRow:
        """Create or refresh the document row for an asset.

        One row per asset: re-indexing updates in place, so a file cannot
        acquire two documents and therefore two competing chunk sets.
        """
        ts = now_ts()
        with self._db.transaction() as conn:
            existing = conn.execute(
                "SELECT document_id FROM homemind_knowledge_documents WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            if existing is None:
                document_id = new_ulid()
                conn.execute(
                    "INSERT INTO homemind_knowledge_documents("
                    "document_id, family_id, asset_id, content_hash, "
                    "parser_version, mime_type, name, status, error, "
                    "chunk_count, indexed_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                    (
                        document_id,
                        family_id,
                        asset_id,
                        content_hash,
                        parser_version,
                        mime_type,
                        name,
                        status,
                        error,
                        ts,
                        ts,
                        ts,
                    ),
                )
            else:
                document_id = str(existing["document_id"])
                conn.execute(
                    "UPDATE homemind_knowledge_documents SET content_hash = ?, "
                    "parser_version = ?, mime_type = ?, name = ?, status = ?, "
                    "error = ?, indexed_at = ?, updated_at = ? "
                    "WHERE document_id = ?",
                    (
                        content_hash,
                        parser_version,
                        mime_type,
                        name,
                        status,
                        error,
                        ts,
                        ts,
                        document_id,
                    ),
                )
        return self.get_document(document_id)  # type: ignore[return-value]

    def get_document(self, document_id: str) -> KnowledgeDocumentRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_knowledge_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        return KnowledgeDocumentRow.from_row(row) if row else None

    def list_documents(
        self,
        family_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[KnowledgeDocumentRow]:
        clauses = ["family_id = ?"]
        params: list[Any] = [family_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_knowledge_documents WHERE "
                + " AND ".join(clauses)
                + " ORDER BY indexed_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return map_rows(rows, KnowledgeDocumentRow)

    def set_chunk_count(self, document_id: str, count: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_knowledge_documents SET chunk_count = ?, updated_at = ? "
                "WHERE document_id = ?",
                (count, now_ts(), document_id),
            )

    def delete_document(self, document_id: str) -> bool:
        """Chunks cascade with the document row."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_knowledge_documents WHERE document_id = ?",
                (document_id,),
            )
        return bool(cursor.rowcount)

    def count_documents(self, family_id: str) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM homemind_knowledge_documents WHERE family_id = ?",
                (family_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------- chunks

    def replace_chunks(
        self,
        *,
        document_id: str,
        family_id: str,
        asset_id: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        """Atomically swap a document's chunks for a new set.

        Delete-then-insert inside one transaction, so a crash cannot leave
        a document claiming chunks it no longer has. ``content_hash`` per
        chunk lets the caller reuse an existing vector instead of paying
        for an embedding that is already stored.
        """
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM homemind_knowledge_chunks WHERE document_id = ?",
                (document_id,),
            )
            for chunk in chunks:
                conn.execute(
                    "INSERT INTO homemind_knowledge_chunks("
                    "chunk_id, document_id, family_id, asset_id, ordinal, "
                    "text, locator, page_number, content_hash, embedding, "
                    "embedding_model, embedding_dimensions, embedding_version, "
                    "created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_ulid(),
                        document_id,
                        family_id,
                        asset_id,
                        int(chunk["ordinal"]),
                        str(chunk["text"]),
                        chunk.get("locator"),
                        chunk.get("page_number"),
                        str(chunk["content_hash"]),
                        json.dumps(chunk["embedding"]) if chunk.get("embedding") else None,
                        chunk.get("embedding_model"),
                        chunk.get("embedding_dimensions"),
                        chunk.get("embedding_version"),
                        ts,
                    ),
                )
            conn.execute(
                "UPDATE homemind_knowledge_documents SET chunk_count = ?, updated_at = ? "
                "WHERE document_id = ?",
                (len(chunks), ts, document_id),
            )
        return len(chunks)

    def existing_chunk_vectors(
        self,
        document_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Stored vectors keyed by chunk content hash.

        Used to skip re-embedding a chunk whose text did not change when
        only one part of a document was edited.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT content_hash, embedding, embedding_model, "
                "embedding_dimensions, embedding_version "
                "FROM homemind_knowledge_chunks "
                "WHERE document_id = ? AND embedding IS NOT NULL",
                (document_id,),
            ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            out[str(row["content_hash"])] = {
                "embedding": json.loads(row["embedding"]),
                "embedding_model": row["embedding_model"],
                "embedding_dimensions": row["embedding_dimensions"],
                "embedding_version": row["embedding_version"],
            }
        return out

    def search_chunks(
        self,
        family_id: str,
        query: str,
        *,
        asset_ids: list[str] | None = None,
        limit: int = 20,
    ) -> list[tuple[KnowledgeChunkRow, int]]:
        """Full-text search over chunks, best first.

        Permission is applied by the caller through ``asset_ids``: the
        repository has no user, so it cannot decide what a member may read.
        Passing a pre-filtered asset list keeps that check on the path
        every query takes.
        """
        if not query.strip():
            return []
        clauses = ["family_id = ?", "text LIKE ?"]
        params: list[Any] = [family_id, f"%{query.strip()}%"]
        if asset_ids is not None:
            if not asset_ids:
                return []
            placeholders = ", ".join("?" for _ in asset_ids)
            clauses.append(f"asset_id IN ({placeholders})")
            params.extend(asset_ids)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_knowledge_chunks WHERE "
                + " AND ".join(clauses)
                + " ORDER BY ordinal ASC LIMIT ?",
                [*params, limit * 5],
            ).fetchall()
        chunks = map_rows(rows, KnowledgeChunkRow)
        needle = query.strip().casefold()
        scored = [(chunk, _score(needle, chunk.text)) for chunk in chunks]
        scored.sort(key=lambda pair: (-pair[1], pair[0].ordinal))
        return [pair for pair in scored if pair[1] > 0][:limit]

    def list_chunks(
        self,
        document_id: str,
        *,
        limit: int = 200,
    ) -> list[KnowledgeChunkRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_knowledge_chunks WHERE document_id = ? "
                "ORDER BY ordinal ASC LIMIT ?",
                (document_id, limit),
            ).fetchall()
        return map_rows(rows, KnowledgeChunkRow)


def _score(needle: str, text: str) -> int:
    """Crude relevance: literal hit, plus a bonus for a tighter window.

    A real FTS index is the obvious next step; until then this is honest
    about being a LIKE scan, and it never returns a chunk with no hit,
    which is what makes ``> 0`` a valid filter.
    """
    folded = text.casefold()
    occurrences = folded.count(needle)
    if not occurrences:
        return 0
    density = len(needle) / max(len(folded), 1)
    return occurrences + int(density * 100)


__all__ = [
    "DOC_STATUSES",
    "DOC_STATUS_FAILED",
    "DOC_STATUS_INDEXED",
    "DOC_STATUS_UNSUPPORTED",
    "KnowledgeChunkRow",
    "KnowledgeDocumentRow",
    "KnowledgeRepo",
    "chunk_hash",
]
