"""Unified search index: one row per searchable family entity (Stage 7).

Everything the search layer needs lives on ``homemind_search_documents``,
which means three things move out of Python and into SQL:

* **Permission filtering.** ``visibility`` / ``owner_member_id`` /
  ``status`` sit on the index row, so a private-space document can be
  excluded by the ``WHERE`` clause rather than by filtering a result
  list after the fact.
* **Cursor pagination.** ``(score, updated_at, kind, id)`` is walked with
  a signed cursor instead of an ``OFFSET``, so concurrent inserts
  cannot make page 2 repeat or skip a row.
* **Embedding provenance.** ``embedding_model`` / ``_dimensions`` /
  ``_version`` travel with the vector; comparing across models is
  refused rather than silently producing nonsense.

SQLite ranks with FTS5 ``bm25``; PostgreSQL ranks with ``ts_rank``.
Both wrap into the same normalised ``0..1`` score.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

KIND_MEMBER = "MEMBER"
KIND_EVENT = "EVENT"
KIND_MEMORY = "MEMORY"
KIND_ASSET = "ASSET"
KIND_ALBUM = "ALBUM"
KIND_OBJECT = "OBJECT"
KIND_SCENE = "SCENE"
KIND_LOCATION = "LOCATION"
KIND_PHOTO_DESCRIPTION = "PHOTO_DESCRIPTION"

SEARCH_KINDS: frozenset[str] = frozenset(
    {
        KIND_MEMBER,
        KIND_EVENT,
        KIND_MEMORY,
        KIND_ASSET,
        KIND_ALBUM,
        KIND_OBJECT,
        KIND_SCENE,
        KIND_LOCATION,
        KIND_PHOTO_DESCRIPTION,
    }
)

# Visibility ranks a member can see. ``PUBLIC`` is visible to the whole
# family, ``FAMILY`` to any member, ``PRIVATE`` only to the owner, and
# ``SENSITIVE`` only to managers. Ordering matters: it is what the SQL
# predicate compares against.
VISIBILITY_PUBLIC = "PUBLIC"
VISIBILITY_FAMILY = "FAMILY"
VISIBILITY_PRIVATE = "PRIVATE"
VISIBILITY_SENSITIVE = "SENSITIVE"

VISIBILITY_RANK: dict[str, int] = {
    VISIBILITY_PUBLIC: 0,
    VISIBILITY_FAMILY: 1,
    VISIBILITY_PRIVATE: 2,
    VISIBILITY_SENSITIVE: 3,
}

# Trigram indexes need at least three characters. A 1–2 character query
# (very common in Chinese — "清淡", "妈妈") cannot use the index and
# falls back to a LIKE scan.
_MIN_TRIGRAM_QUERY_LENGTH = 3


@dataclass(frozen=True)
class SearchDocumentRow:
    document_id: str
    family_id: str
    kind: str
    entity_id: str
    title: str
    text: str
    space_id: str | None
    owner_member_id: str | None
    visibility: str
    status: str
    importance: float
    captured_at: int | None
    updated_at: int
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    embedding_version: int | None = None
    embedding_content_hash: str | None = None
    embedding: bytes | None = None

    @classmethod
    def from_row(cls, row: DbRow) -> SearchDocumentRow:
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )

        def _opt_int(key: str) -> int | None:
            value = data.get(key)
            return int(value) if value is not None else None

        raw_embedding = data.get("embedding")
        if isinstance(raw_embedding, memoryview):
            raw_embedding = raw_embedding.tobytes()
        return cls(
            document_id=str(data["document_id"]),
            family_id=str(data["family_id"]),
            kind=str(data["kind"]),
            entity_id=str(data["entity_id"]),
            title=str(data["title"]),
            text=str(data["text"]),
            space_id=data["space_id"],
            owner_member_id=data["owner_member_id"],
            visibility=str(data["visibility"]),
            status=str(data["status"]),
            importance=float(data["importance"]),
            captured_at=_opt_int("captured_at"),
            updated_at=int(data["updated_at"]),
            embedding_model=data["embedding_model"],
            embedding_dimensions=_opt_int("embedding_dimensions"),
            embedding_version=_opt_int("embedding_version"),
            embedding_content_hash=data["embedding_content_hash"],
            embedding=bytes(raw_embedding) if raw_embedding else None,
        )


def _rank_value(row: Any) -> float:
    """Read the backend rank column off a raw result row.

    FTS5 ``bm25`` returns a negative float, ``similarity`` a positive
    one, and the LIKE fallback a small integer — the manager folds all
    three into ``0..1``.
    """
    data: dict[str, Any] = (
        {key: row[key] for key in row.keys()}  # noqa: SIM118
        if hasattr(row, "keys")
        else dict(row)
    )
    for key in ("rank", "similarity", "score"):
        if key in data and data[key] is not None:
            return float(data[key])
    return 0.0


def pack_embedding(vector: list[float]) -> bytes:
    """Little-endian float32 blob — compact and byte-order explicit."""
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_embedding(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob[: count * 4]))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity clamped to ``[-1, 1]``; ``0.0`` when either
    vector is degenerate rather than raising."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot: float = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm: float = sum(a * a for a in left) ** 0.5
    right_norm: float = sum(b * b for b in right) ** 0.5
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    similarity: float = dot / (left_norm * right_norm)
    return max(-1.0, min(1.0, similarity))


class SearchIndexRepo:
    """CRUD over the unified search index plus cursor storage."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db
        self.dialect = db.dialect

    # ------------------------------------------------------------- indexing

    def upsert_document(
        self,
        family_id: str,
        *,
        kind: str,
        entity_id: str,
        title: str,
        text: str,
        visibility: str = VISIBILITY_FAMILY,
        status: str = "ACTIVE",
        space_id: str | None = None,
        owner_member_id: str | None = None,
        importance: float = 0.5,
        captured_at: int | None = None,
        updated_at: int | None = None,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
        embedding_version: int | None = None,
    ) -> str:
        """Insert or refresh one document. Returns ``document_id``.

        The document id is derived from ``(family_id, kind, entity_id)``
        so re-indexing the same entity updates rather than duplicates —
        which is what keeps a re-scan from doubling the index.
        """
        if kind not in SEARCH_KINDS:
            raise ValueError(f"unknown search kind {kind!r}")
        document_id = document_id_for(family_id, kind, entity_id)
        timestamp = now_ts() if updated_at is None else updated_at
        content_hash = (
            hashlib.sha256(text.encode("utf-8")).hexdigest() if embedding is not None else None
        )
        with self._db.transaction() as conn:
            existing = conn.execute(
                "SELECT document_id FROM homemind_search_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO homemind_search_documents(document_id, family_id, kind, "
                    "entity_id, title, text, space_id, owner_member_id, visibility, status, "
                    "importance, captured_at, updated_at, embedding_model, "
                    "embedding_dimensions, embedding_version, embedding_content_hash, "
                    "embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        document_id,
                        family_id,
                        kind,
                        entity_id,
                        title,
                        text,
                        space_id,
                        owner_member_id,
                        visibility,
                        status,
                        importance,
                        captured_at,
                        timestamp,
                        embedding_model,
                        embedding_dimensions,
                        embedding_version,
                        content_hash,
                        pack_embedding(embedding) if embedding is not None else None,
                    ),
                )
            else:
                sets = [
                    "title = ?",
                    "text = ?",
                    "space_id = ?",
                    "owner_member_id = ?",
                    "visibility = ?",
                    "status = ?",
                    "importance = ?",
                    "captured_at = ?",
                    "updated_at = ?",
                    "embedding_model = ?",
                    "embedding_dimensions = ?",
                    "embedding_version = ?",
                    "embedding_content_hash = ?",
                    "embedding = ?",
                ]
                params: list[Any] = [
                    title,
                    text,
                    space_id,
                    owner_member_id,
                    visibility,
                    status,
                    importance,
                    captured_at,
                    timestamp,
                    embedding_model,
                    embedding_dimensions,
                    embedding_version,
                    content_hash,
                    pack_embedding(embedding) if embedding is not None else None,
                    document_id,
                ]
                conn.execute(
                    f"UPDATE homemind_search_documents SET {', '.join(sets)} "
                    "WHERE document_id = ?",
                    params,
                )
        return document_id

    def remove_document(self, family_id: str, kind: str, entity_id: str) -> bool:
        document_id = document_id_for(family_id, kind, entity_id)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_search_documents "
                "WHERE document_id = ? AND family_id = ?",
                (document_id, family_id),
            )
        return int(cursor.rowcount or 0) > 0

    def mark_document_status(self, document_id: str, status: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_search_documents SET status = ?, updated_at = ? "
                "WHERE document_id = ?",
                (status, now_ts(), document_id),
            )
        return int(cursor.rowcount or 0) > 0

    def count_documents(self, family_id: str | None = None) -> int:
        if family_id is None:
            with self._db.connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM homemind_search_documents"
                ).fetchone()
        else:
            with self._db.connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM homemind_search_documents "
                    "WHERE family_id = ?",
                    (family_id,),
                ).fetchone()
        data = {key: row[key] for key in row.keys()}  # noqa: SIM118
        return int(data["n"])

    def clear_embeddings(
        self, family_id: str | None = None, *, model: str | None = None,
    ) -> int:
        """Drop vectors so a REINDEX job can rebuild them cleanly.

        Scoped by ``model`` when given, so switching one provider does
        not force a full re-embed of every document.
        """
        clauses = ["embedding IS NOT NULL"]
        params: list[Any] = []
        if family_id is not None:
            clauses.append("family_id = ?")
            params.append(family_id)
        if model is not None:
            clauses.append("embedding_model = ?")
            params.append(model)
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_search_documents SET embedding = NULL, "
                "embedding_model = NULL, embedding_dimensions = NULL, "
                "embedding_version = NULL, embedding_content_hash = NULL "
                f"WHERE {' AND '.join(clauses)}",
                params,
            )
            cursor = conn.execute(
                "SELECT COUNT(*) AS n FROM homemind_search_documents "
                f"WHERE {' AND '.join(clauses)}",
                params,
            )
            row = cursor.fetchone()
        data = {key: row[key] for key in row.keys()}  # noqa: SIM118
        return int(data["n"])

    # -------------------------------------------------------------- queries

    def keyword_search(
        self,
        family_id: str,
        *,
        query: str,
        kinds: tuple[str, ...],
        visible_visibility: tuple[str, ...],
        owner_member_id: str | None,
        allow_private: bool,
        start_at: int | None = None,
        end_at: int | None = None,
        limit: int = 50,
    ) -> list[tuple[SearchDocumentRow, float]]:
        """Keyword search with the permission predicate in SQL.

        ``visible_visibility`` is the set of visibility levels the caller
        may see; private-space documents are additionally restricted to
        their owner unless ``allow_private``. The filter is applied by
        the database, not by a post-pass in Python — that is the whole
        point of the index.

        Trigram indexes need at least three characters, so a 1–2
        character query (very common in Chinese: "清淡", "妈妈") falls
        back to a LIKE scan. That is slower but correct, and the scope
        is already narrowed by family + kinds + visibility, so the scan
        stays bounded.
        """
        needle = query.strip()
        if not needle:
            return []
        if len(needle) < _MIN_TRIGRAM_QUERY_LENGTH:
            return self._like_search(
                family_id,
                needle=needle,
                kinds=kinds,
                visible_visibility=visible_visibility,
                owner_member_id=owner_member_id,
                allow_private=allow_private,
                start_at=start_at,
                end_at=end_at,
                limit=limit,
            )
        params: list[Any] = [family_id]
        kind_placeholders = ", ".join("?" for _ in kinds)
        params.extend(kinds)
        params.append(needle)
        time_clause = ""
        if start_at is not None:
            params.append(start_at)
            time_clause = " AND (d.captured_at IS NULL OR d.captured_at >= ?)"
        if end_at is not None:
            params.append(end_at)
            time_clause += " AND (d.captured_at IS NULL OR d.captured_at <= ?)"

        visibility_clause, visibility_params = self._visibility_predicate(
            visible_visibility, owner_member_id, allow_private,
        )
        params.extend(visibility_params)
        params.append(limit)

        if self.dialect == "postgresql":
            sql = (
                "SELECT d.*, similarity(coalesce(d.text, '') || ' ' || coalesce(d.title, ''), ?) AS rank "
                "FROM homemind_search_documents d "
                f"WHERE d.family_id = ? AND d.kind IN ({kind_placeholders}) "
                "  AND d.status = 'ACTIVE' "
                "  AND (coalesce(d.text, '') || ' ' || coalesce(d.title, '')) % ? "
                + time_clause
                + f" AND {visibility_clause} "
                "ORDER BY rank DESC, d.updated_at DESC, d.id DESC LIMIT ?"
            )
            with self._db.connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            return self._rows_with_rank(rows)

        sql = (
            "SELECT d.*, fts.rank AS rank "
            "FROM homemind_search_documents d "
            "JOIN homemind_search_fts fts ON fts.document_id = d.document_id "
            f"WHERE d.family_id = ? AND d.kind IN ({kind_placeholders}) "
            "  AND d.status = 'ACTIVE' "
            "  AND homemind_search_fts MATCH ? "
            + time_clause
            + f" AND {visibility_clause} "
            "ORDER BY fts.rank LIMIT ?"
        )
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return self._rows_with_rank(rows)

    def _like_search(
        self,
        family_id: str,
        *,
        needle: str,
        kinds: tuple[str, ...],
        visible_visibility: tuple[str, ...],
        owner_member_id: str | None,
        allow_private: bool,
        start_at: int | None,
        end_at: int | None,
        limit: int,
    ) -> list[tuple[SearchDocumentRow, float]]:
        """Substring search for queries too short to use the index."""
        params: list[Any] = [family_id]
        kind_placeholders = ", ".join("?" for _ in kinds)
        params.extend(kinds)
        params.append(f"%{needle}%")
        time_clause = ""
        if start_at is not None:
            params.append(start_at)
            time_clause = " AND (d.captured_at IS NULL OR d.captured_at >= ?)"
        if end_at is not None:
            params.append(end_at)
            time_clause += " AND (d.captured_at IS NULL OR d.captured_at <= ?)"
        visibility_clause, visibility_params = self._visibility_predicate(
            visible_visibility, owner_member_id, allow_private,
        )
        params.extend(visibility_params)
        params.append(limit)
        sql = (
            "SELECT d.*, CASE WHEN d.text LIKE ? THEN 2 ELSE 1 END AS rank "
            "FROM homemind_search_documents d "
            f"WHERE d.family_id = ? AND d.kind IN ({kind_placeholders}) "
            "  AND d.status = 'ACTIVE' "
            "  AND (d.text LIKE ? OR d.title LIKE ?) "
            + time_clause
            + f" AND {visibility_clause} "
            "ORDER BY rank DESC, d.updated_at DESC, d.id DESC LIMIT ?"
        )
        # The leading CASE placeholder, then family / kinds, then the
        # two LIKEs, then time, visibility, limit.
        prefix: list[Any] = [f"%{needle}%"]
        rest: list[Any] = [family_id, *kinds, f"%{needle}%", f"%{needle}%"]
        if start_at is not None:
            rest.append(start_at)
        if end_at is not None:
            rest.append(end_at)
        rest.extend(visibility_params)
        rest.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(sql, [*prefix, *rest]).fetchall()
        return self._rows_with_rank(rows)

    def _visibility_predicate(
        self,
        visible_visibility: tuple[str, ...],
        owner_member_id: str | None,
        allow_private: bool,
    ) -> tuple[str, list[Any]]:
        """Build the SQL fragment that keeps hidden rows out.

        Returns the predicate and its bind params. The caller-supplied
        ``visible_visibility`` is validated against the known enum in
        :func:`sanitize_visibility` before it reaches here, so this
        method can inline the literals without escaping concerns.
        """
        if allow_private:
            return "1 = 1", []
        placeholders = ", ".join("?" for _ in visible_visibility)
        params: list[Any] = list(visible_visibility)
        predicate = f"d.visibility IN ({placeholders})"
        if owner_member_id is not None:
            predicate += " OR d.owner_member_id = ?"
            params.append(owner_member_id)
        return predicate, params

    @staticmethod
    def _rows_with_rank(rows: list[Any]) -> list[tuple[SearchDocumentRow, float]]:
        """Hydrate rows and carry the backend rank alongside them."""
        out: list[tuple[SearchDocumentRow, float]] = []
        for row in rows:
            document = SearchDocumentRow.from_row(row)
            out.append((document, _rank_value(row)))
        return out

    def list_documents(
        self,
        family_id: str,
        *,
        kinds: tuple[str, ...] = (),
        status: str | None = None,
        limit: int = 500,
    ) -> list[SearchDocumentRow]:
        clauses = ["family_id = ?"]
        params: list[Any] = [family_id]
        if kinds:
            clauses.append(f"kind IN ({', '.join('?' for _ in kinds)})")
            params.extend(kinds)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM homemind_search_documents WHERE {' AND '.join(clauses)} "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return map_rows(rows, SearchDocumentRow)

    def embedding_candidates(
        self,
        family_id: str,
        *,
        model: str,
        dimensions: int,
        version: int,
        limit: int = 500,
    ) -> list[SearchDocumentRow]:
        """Rows whose vector provenance matches exactly.

        Mixing vectors from different models or dimensions produces
        meaningless similarities, so the filter is mandatory rather than
        advisory.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_search_documents WHERE family_id = ? "
                "AND embedding IS NOT NULL AND embedding_model = ? "
                "AND embedding_dimensions = ? AND embedding_version = ? "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (family_id, model, dimensions, version, limit),
            ).fetchall()
        return map_rows(rows, SearchDocumentRow)

    # -------------------------------------------------------------- cursors

    def store_cursor(
        self, family_id: str, *, created_by: int, ttl_seconds: int = 3600,
    ) -> str:
        """Persist an opaque cursor key so the client cannot forge one.

        The client receives the key and hands it back; the sort position
        itself stays server-side, which is what stops a caller from
        editing a cursor to skip or replay rows.
        """
        cursor_key = new_ulid()
        timestamp = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_search_cursors(cursor_key, family_id, created_by, "
                "created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (cursor_key, family_id, created_by, timestamp, timestamp + ttl_seconds),
            )
        return cursor_key

    def load_cursor(self, cursor_key: str, family_id: str) -> dict[str, Any] | None:
        timestamp = now_ts()
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT c.cursor_key, c.expires_at FROM homemind_search_cursors c "
                "WHERE c.cursor_key = ? AND c.family_id = ? AND c.expires_at > ?",
                (cursor_key, family_id, timestamp),
            ).fetchone()
        if row is None:
            return None
        data = {key: row[key] for key in row.keys()}  # noqa: SIM118
        return {"cursor_key": str(data["cursor_key"]), "expires_at": int(data["expires_at"])}

    def delete_cursor(self, cursor_key: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM homemind_search_cursors WHERE cursor_key = ?",
                (cursor_key,),
            )

    def purge_expired_cursors(self) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_search_cursors WHERE expires_at <= ?", (now_ts(),),
            )
        return int(cursor.rowcount or 0)


def sanitize_visibility(requested: str | None) -> tuple[str, ...]:
    """Validate a caller-supplied visibility filter.

    Unknown values are dropped rather than interpolated, so this cannot
    become an injection vector into the inlined predicate.
    """
    if not requested:
        return (VISIBILITY_PUBLIC, VISIBILITY_FAMILY)
    wanted: list[str] = []
    for raw in requested.split(","):
        value = raw.strip().upper()
        if value in VISIBILITY_RANK:
            wanted.append(value)
    return tuple(wanted) or (VISIBILITY_PUBLIC, VISIBILITY_FAMILY)


def document_id_for(family_id: str, kind: str, entity_id: str) -> str:
    """Stable document id so re-indexing updates in place."""
    digest = hashlib.sha256(f"{family_id}:{kind}:{entity_id}".encode()).hexdigest()
    return f"{kind.lower()}_{digest[:40]}"


def encode_position(score: float, updated_at: int, kind: str, entity_id: str) -> str:
    """Encode a sort position for transport inside a cursor token."""
    payload = json.dumps(
        {
            "s": round(float(score), 6),
            "u": int(updated_at),
            "k": kind,
            "i": entity_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_position(token: str) -> dict[str, Any] | None:
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + padding)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "u" not in payload or "i" not in payload:
        return None
    return payload


def sign_token(payload: str, *, secret: bytes) -> str:
    """Attach an HMAC so a client cannot tamper with a cursor."""
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{digest}"


def verify_token(token: str, *, secret: bytes) -> str | None:
    """Return the payload when the signature checks out, else ``None``."""
    if "." not in token:
        return None
    payload, _, signature = token.rpartition(".")
    expected = hmac.new(
        secret, payload.encode("utf-8"), hashlib.sha256,
    ).hexdigest()[:32]
    if not hmac.compare_digest(signature, expected):
        return None
    return payload


__all__ = [
    "KIND_ALBUM",
    "KIND_ASSET",
    "KIND_EVENT",
    "KIND_LOCATION",
    "KIND_MEMBER",
    "KIND_MEMORY",
    "KIND_OBJECT",
    "KIND_PHOTO_DESCRIPTION",
    "KIND_SCENE",
    "SEARCH_KINDS",
    "VISIBILITY_FAMILY",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_PUBLIC",
    "VISIBILITY_RANK",
    "VISIBILITY_SENSITIVE",
    "SearchDocumentRow",
    "SearchIndexRepo",
    "cosine_similarity",
    "decode_position",
    "document_id_for",
    "encode_position",
    "pack_embedding",
    "sanitize_visibility",
    "sign_token",
    "unpack_embedding",
    "verify_token",
]
