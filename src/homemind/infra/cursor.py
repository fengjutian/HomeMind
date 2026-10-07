"""Cursor pagination for the mobile API contract (Stage 7).

A phone is a worse pager than a desktop, for two reasons that offset
pagination handles badly:

* **Depth.** ``LIMIT 20 OFFSET 2000`` makes the database walk and
  discard two thousand rows to return twenty.
* **Consistency.** If a notification arrives mid-page, every offset
  shifts and the client skips an item or shows one twice.

The cursor is an opaque, base64url encoding of ``(created_at, id)``.
Because ``id`` is a ULID it breaks ties in insertion order, which makes
the pair *unique* — and a unique pair is what lets the database resume
with a precise range comparison instead of a sort.

Base64 is deliberately **not** relied on for ordering (it preserves
none); the resume query compares the decoded numbers in SQL. The
encoding exists so a client cannot read a timestamp out of a token and
construct one of its own.

**The cursor is opaque on purpose.** A client must not be able to
construct one, and a future index change must be able to alter its
shape without breaking a shipped app. So it is base64url and validated
on the way back in.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: A cursor that cannot possibly be one we issued is rejected rather
#: than decoded; anything shorter than this is not a real cursor.
_MIN_TOKEN_LENGTH = 8


class InvalidCursor(ValueError):
    """The caller sent something that is not a cursor we issued."""


def encode_cursor(created_at: int, row_id: str) -> str:
    """Encode ``(created_at, id)`` into an opaque token.

    The timestamp is zero-padded to 12 digits purely so the token has a
    fixed width, which keeps cursors uniform in size and makes a
    malformed one obvious.
    """
    raw = f"{created_at:012d}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str) -> tuple[int, str]:
    """Recover ``(created_at, id)``, or refuse the token.

    Refusing rather than best-effort parsing: a malformed cursor
    should send the client back to page one with a clear error, not
    silently return a page from an unexpected position.
    """
    if not token or len(token) < _MIN_TOKEN_LENGTH:
        raise InvalidCursor("cursor is too short to be one we issued")
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise InvalidCursor("cursor is not valid base64url") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidCursor("cursor does not decode as text") from exc
    created_at, separator, row_id = text.partition("|")
    # The id half is a ULID, which contains letters; only its presence
    # is checked here. Validating its shape would tie this decoder to the
    # id format, and the contract says the token is opaque.
    if not separator or not row_id:
        raise InvalidCursor("cursor is not in the expected shape")
    if not created_at.isdigit():
        raise InvalidCursor("cursor carries a non-numeric timestamp")
    return int(created_at), row_id


@dataclass(frozen=True)
class Page:
    """One page of results plus the token for the next one."""

    items: list[Any]
    next_cursor: str | None
    has_more: bool

    def as_dict(self) -> dict[str, object]:
        """The JSON shape every mobile list endpoint returns.

        ``has_more`` exists alongside the cursor so a client can show
        "load more" without a probe request, and ``next_cursor`` is
        ``None`` on the last page rather than an empty string, so a
        client that checks it cannot accidentally loop forever.
        """
        return {
            "items": self.items,
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
        }


def build_page(rows: list[Any], *, to_item: Callable[[Any], Any], limit: int) -> Page:
    """Turn an over-fetched row list into a page.

    Callers fetch ``limit + 1`` rows; the extra one proves there is a
    next page without a second query, and is dropped here.
    """
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = (
        encode_cursor(visible[-1].created_at, visible[-1].id) if has_more and visible else None
    )
    return Page(items=[to_item(row) for row in visible], next_cursor=next_cursor, has_more=has_more)


__all__ = [
    "InvalidCursor",
    "Page",
    "build_page",
    "decode_cursor",
    "encode_cursor",
]
