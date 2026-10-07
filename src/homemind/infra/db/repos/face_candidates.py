"""Face-match candidates awaiting a human decision.

A face-recognition provider returns a confidence, not a fact. This repo
stores the *proposal* — "this photo may show member X" — and keeps it in a
review queue until a family manager decides. Only ``CONFIRMED`` rows may be
used as a label; ``PENDING`` is a suggestion and ``REJECTED`` is never
re-offered automatically.

No biometric data is stored. The candidate records who was matched, how
confident the provider was, and who decided.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

#: A match the provider proposed; a manager has not looked at it yet.
CANDIDATE_PENDING = "PENDING"
#: A manager confirmed the match. Only these may be used as a label.
CANDIDATE_CONFIRMED = "CONFIRMED"
#: A manager rejected the match. Never re-offered automatically.
CANDIDATE_REJECTED = "REJECTED"

CANDIDATE_STATUSES: frozenset[str] = frozenset(
    {CANDIDATE_PENDING, CANDIDATE_CONFIRMED, CANDIDATE_REJECTED}
)


@dataclass(frozen=True)
class FaceCandidateRow:
    id: str
    pk: int
    family_id: str
    asset_id: str
    member_id: str
    confidence: float
    status: str
    decided_by: int | None
    decided_at: int | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FaceCandidateRow:
        # ``sqlite3.Row`` iterates *values*; column names come from
        # ``.keys()``. ruff SIM118's suggestion is wrong here.
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )

        def _opt_int(key: str) -> int | None:
            value = data.get(key)
            return int(value) if value is not None else None

        return cls(
            id=str(data["candidate_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            asset_id=str(data["asset_id"]),
            member_id=str(data["member_id"]),
            confidence=float(data["confidence"]),
            status=str(data["status"]),
            decided_by=_opt_int("decided_by"),
            decided_at=_opt_int("decided_at"),
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
        )


class FaceCandidateRepo:
    """SQL access for ``homemind_family_face_candidates``."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def upsert_candidate(
        self,
        *,
        family_id: str,
        asset_id: str,
        member_id: str,
        confidence: float,
    ) -> FaceCandidateRow:
        """Record a provider's proposal, or refresh the pending one.

        Idempotent by ``(asset_id, member_id)``: re-running a FACE_MATCH
        job must not pile up duplicate candidates. An already-decided row is
        left alone — a manager's decision outranks a later re-run, and
        silently reopening a rejected match would defeat the point of
        rejecting it.
        """
        candidate_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            existing = conn.execute(
                "SELECT candidate_id FROM homemind_family_face_candidates "
                "WHERE asset_id = ? AND member_id = ?",
                (asset_id, member_id),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "UPDATE homemind_family_face_candidates "
                    "SET confidence = ?, updated_at = ? "
                    "WHERE asset_id = ? AND member_id = ? AND status = 'PENDING'",
                    (float(confidence), ts, asset_id, member_id),
                )
                return self.get(str(existing["candidate_id"]))  # type: ignore[return-value]
            conn.execute(
                "INSERT INTO homemind_family_face_candidates("
                "candidate_id, family_id, asset_id, member_id, confidence, "
                "status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?)",
                (
                    candidate_id,
                    family_id,
                    asset_id,
                    member_id,
                    float(confidence),
                    ts,
                    ts,
                ),
            )
        return self.get(candidate_id)  # type: ignore[return-value]

    def get(self, candidate_id: str) -> FaceCandidateRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_face_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        return FaceCandidateRow.from_row(row) if row else None

    def list_candidates(
        self,
        family_id: str,
        *,
        status: str | None = None,
        asset_id: str | None = None,
        limit: int = 100,
    ) -> list[FaceCandidateRow]:
        clauses = ["family_id = ?"]
        params: list[Any] = [family_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if asset_id is not None:
            clauses.append("asset_id = ?")
            params.append(asset_id)
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_face_candidates WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return map_rows(rows, FaceCandidateRow)

    def confirm(
        self,
        candidate_id: str,
        decided_by: int,
        *,
        now: int | None = None,
    ) -> FaceCandidateRow | None:
        """Move a pending candidate to ``CONFIRMED``.

        Only a pending row can be confirmed: re-confirming a rejected match
        would let an API caller bypass the review queue entirely.
        """
        return self._decide(
            candidate_id,
            CANDIDATE_CONFIRMED,
            decided_by,
            now=now,
        )

    def reject(
        self,
        candidate_id: str,
        decided_by: int,
        *,
        now: int | None = None,
    ) -> FaceCandidateRow | None:
        return self._decide(
            candidate_id,
            CANDIDATE_REJECTED,
            decided_by,
            now=now,
        )

    def _decide(
        self,
        candidate_id: str,
        to_status: str,
        decided_by: int,
        *,
        now: int | None = None,
    ) -> FaceCandidateRow | None:
        timestamp = now_ts() if now is None else now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_face_candidates "
                "SET status = ?, decided_by = ?, decided_at = ?, updated_at = ? "
                "WHERE candidate_id = ? AND status = 'PENDING'",
                (to_status, decided_by, timestamp, timestamp, candidate_id),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(candidate_id)

    def confirmed_member_for(
        self,
        family_id: str,
        asset_id: str,
    ) -> str | None:
        """The confirmed member for an asset, if any.

        This is the only read path a caller may use to label a face: a
        pending or rejected candidate is not an answer.
        """
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT member_id FROM homemind_family_face_candidates "
                "WHERE family_id = ? AND asset_id = ? AND status = 'CONFIRMED' "
                "ORDER BY confidence DESC, created_at ASC LIMIT 1",
                (family_id, asset_id),
            ).fetchone()
        return str(row["member_id"]) if row else None

    def count_pending(self, family_id: str) -> int:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM homemind_family_face_candidates "
                "WHERE family_id = ? AND status = 'PENDING'",
                (family_id,),
            ).fetchone()
        return int(row["n"]) if row else 0


__all__ = [
    "CANDIDATE_CONFIRMED",
    "CANDIDATE_PENDING",
    "CANDIDATE_REJECTED",
    "CANDIDATE_STATUSES",
    "FaceCandidateRepo",
    "FaceCandidateRow",
]
