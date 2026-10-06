"""SQL access for memory candidates and evidence (Stage 4)."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts, optional_updates
from octop.infra.utils.ulid import new_ulid


# Status values for memory candidates. APPROVED = merged into a real memory.
# MERGED is recorded for clarity when an explicit merge happens; the canonical
# active memory remains the FamilyMemoryRow.
CANDIDATE_STATUS_PENDING = "PENDING"
CANDIDATE_STATUS_APPROVED = "APPROVED"
CANDIDATE_STATUS_REJECTED = "REJECTED"
CANDIDATE_STATUS_MERGED = "MERGED"
CANDIDATE_STATUS_EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class MemoryCandidateRow:
    id: str
    pk: int
    family_id: str
    subject_type: str
    subject_id: str | None
    content: str
    memory_type: str
    importance: float
    confidence: float
    visibility: str
    source_type: str
    source_id: str | None
    status: str
    created_by: int
    created_at: int
    reviewed_by: int | None
    reviewed_at: int | None
    rejection_reason: str | None
    merged_into: str | None

    @classmethod
    def from_row(cls, row: DbRow) -> MemoryCandidateRow:
        return cls(
            id=str(row["candidate_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            subject_type=str(row["subject_type"]),
            subject_id=row["subject_id"],
            content=str(row["content"]),
            memory_type=str(row["memory_type"]),
            importance=float(row["importance"]),
            confidence=float(row["confidence"]),
            visibility=str(row["visibility"]),
            source_type=str(row["source_type"]),
            source_id=row["source_id"],
            status=str(row["status"]),
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            reviewed_by=int(row["reviewed_by"]) if row["reviewed_by"] is not None else None,
            reviewed_at=int(row["reviewed_at"]) if row["reviewed_at"] is not None else None,
            rejection_reason=row["rejection_reason"],
            merged_into=row["merged_into"],
        )


@dataclass(frozen=True)
class MemoryEvidenceRow:
    id: str
    pk: int
    candidate_id: str
    memory_id: str | None
    source_type: str
    source_id: str | None
    content_hash: str
    observed_at: int
    confidence_delta: float

    @classmethod
    def from_row(cls, row: DbRow) -> MemoryEvidenceRow:
        return cls(
            id=str(row["evidence_id"]),
            pk=int(row["id"]),
            candidate_id=str(row["candidate_id"]),
            memory_id=row["memory_id"],
            source_type=str(row["source_type"]),
            source_id=row["source_id"],
            content_hash=str(row["content_hash"]),
            observed_at=int(row["observed_at"]),
            confidence_delta=float(row["confidence_delta"]),
        )


class MemoryCandidateRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        family_id: str,
        *,
        subject_type: str,
        subject_id: str | None,
        content: str,
        memory_type: str,
        importance: float,
        confidence: float,
        visibility: str,
        source_type: str,
        source_id: str | None,
        created_by: int,
    ) -> MemoryCandidateRow:
        candidate_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_memory_candidates("
                "candidate_id, family_id, subject_type, subject_id, content, memory_type, "
                "importance, confidence, visibility, source_type, source_id, status, "
                "created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)",
                (
                    candidate_id,
                    family_id,
                    subject_type,
                    subject_id,
                    content,
                    memory_type,
                    importance,
                    confidence,
                    visibility,
                    source_type,
                    source_id,
                    created_by,
                    ts,
                ),
            )
        return self.get(candidate_id)  # type: ignore[return-value]

    def get(self, candidate_id: str) -> MemoryCandidateRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_memory_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        return MemoryCandidateRow.from_row(row) if row else None

    def list_for_family(
        self,
        family_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[MemoryCandidateRow]:
        clauses = ["family_id = ?"]
        params: list[object] = [family_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_memory_candidates WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return map_rows(rows, MemoryCandidateRow)

    def decide(
        self,
        candidate_id: str,
        *,
        status: str,
        reviewer_id: int,
        rejection_reason: str | None = None,
        merged_into: str | None = None,
    ) -> MemoryCandidateRow | None:
        ts = now_ts()
        fields = ["status = ?", "reviewed_by = ?", "reviewed_at = ?"]
        params: list[object] = [status, reviewer_id, ts]
        if rejection_reason is not None:
            fields.append("rejection_reason = ?")
            params.append(rejection_reason)
        if merged_into is not None:
            fields.append("merged_into = ?")
            params.append(merged_into)
        params.append(candidate_id)
        with self._db.transaction() as conn:
            conn.execute(
                f"UPDATE homemind_memory_candidates SET {', '.join(fields)} "
                "WHERE candidate_id = ?",
                params,
            )
        return self.get(candidate_id)


class MemoryEvidenceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def add(
        self,
        candidate_id: str,
        *,
        memory_id: str | None,
        source_type: str,
        source_id: str | None,
        content_hash: str,
        confidence_delta: float = 0.0,
    ) -> MemoryEvidenceRow:
        evidence_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_memory_evidence("
                "evidence_id, candidate_id, memory_id, source_type, source_id, "
                "content_hash, observed_at, confidence_delta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    evidence_id,
                    candidate_id,
                    memory_id,
                    source_type,
                    source_id,
                    content_hash,
                    ts,
                    confidence_delta,
                ),
            )
        return self.get(evidence_id)  # type: ignore[return-value]

    def get(self, evidence_id: str) -> MemoryEvidenceRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_memory_evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
        return MemoryEvidenceRow.from_row(row) if row else None

    def list_for_memory(self, memory_id: str) -> list[MemoryEvidenceRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_memory_evidence WHERE memory_id = ? "
                "ORDER BY observed_at DESC, id DESC",
                (memory_id,),
            ).fetchall()
        return map_rows(rows, MemoryEvidenceRow)

    def list_for_candidate(self, candidate_id: str) -> list[MemoryEvidenceRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_memory_evidence WHERE candidate_id = ? "
                "ORDER BY observed_at DESC, id DESC",
                (candidate_id,),
            ).fetchall()
        return map_rows(rows, MemoryEvidenceRow)


__all__ = [
    "CANDIDATE_STATUS_APPROVED",
    "CANDIDATE_STATUS_EXPIRED",
    "CANDIDATE_STATUS_MERGED",
    "CANDIDATE_STATUS_PENDING",
    "CANDIDATE_STATUS_REJECTED",
    "MemoryCandidateRepo",
    "MemoryCandidateRow",
    "MemoryEvidenceRepo",
    "MemoryEvidenceRow",
]