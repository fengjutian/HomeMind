"""Asynchronous device asset transfers: control plane plus data plane.

A device does not stream family bytes through the logged-in asset route.
It asks the control plane for a *transfer*, the control plane authorizes
one (device, family, asset) triple and mints a **short-lived** credential,
and only that credential may read bytes. Two planes, two credentials:

* control plane -- long-lived device bearer token, creates / refreshes /
  reports on a task;
* data plane -- ``Transfer <token>``, reads byte ranges and nothing else.

Why the split: a download URL that outlives the job would let anyone who
observed it keep pulling a 5 GiB family video. The data-plane credential
expires in minutes, is revocable on its own, and is never valid for
creating anything.

Invariants worth stating because they are easy to break later:

* Device identity comes from the credential, never from a request field.
* Every path is resolved from the asset row; a client can never name a
  local path.
* Authorization is re-checked on **every** data request, not once at task
  creation -- a transfer created before a device was revoked must stop
  working immediately.
* A resumed download is only valid against the same resource *version*
  (size + mtime + content hash), surfaced to the device as an ETag.
* Bytes are served by a bounded, streaming read. Memory use must not grow
  with file size.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import os
import re
import secrets
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Literal, Protocol
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from starlette.concurrency import iterate_in_threadpool

from homemind.infra.db.repos.asset_transfers import (
    ACTIVE_STATUSES,
    AssetTransferRepo,
    AssetTransferRow,
)
from homemind.infra.db.repos.family_assets import FamilyAssetRepo, FamilyAssetRow
from homemind.infra.db.repos.family_devices import FamilyDeviceRepo, FamilyDeviceRow
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.device_runtime import DeviceRuntimeManager
from homemind.infra.metrics import inc as _hm_inc

# ---------------------------------------------------------------- defaults
# The device is a separate project, so these values are the contract: the
# manifest ships them per task and the client is expected to follow the
# manifest rather than its own build-time constants.

DEFAULT_CHUNK_SIZE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_TOKEN_TTL_SECONDS = 600
DEFAULT_IDLE_TTL_SECONDS = 86400
DEFAULT_MAX_ACTIVE_PER_DEVICE = 3
DEFAULT_READ_BLOCK_BYTES = 512 * 1024

#: Concurrent single-range requests allowed against one transfer. The
#: client is told to use ``max_concurrency``; this is the server-side
#: backstop so a client that ignores the manifest cannot exhaust the
#: process file-handle budget.
DEFAULT_MAX_CONCURRENT_RANGES_PER_TRANSFER = 8

#: Concurrent download file handles allowed across the whole process.
#: The per-transfer cap bounds one device; this bounds the household. A
#: desktop server can hold a few hundred descriptors comfortably, and a
#: refused request (429 + Retry-After) is far better than a process that
#: cannot open any more files at all.
DEFAULT_MAX_OPEN_FILE_HANDLES = 64

#: Create rate per device, sliding window.
DEFAULT_CREATE_RATE_LIMIT = 30
DEFAULT_CREATE_RATE_WINDOW_SECONDS = 60

#: Cap on the device-reported failure text kept for triage.
MAX_FAILURE_DETAIL_LENGTH = 200

#: Asset visibilities a shared device runtime may download. ``PRIVATE``
#: and ``SENSITIVE`` are scoped to a member and have no device-level
#: meaning, so they are refused rather than silently widened.
DEVICE_READABLE_VISIBILITIES: frozenset[str] = frozenset({"PUBLIC", "FAMILY"})

_SHA256_HEX_LENGTH = 64


class RangeNotSatisfiable(Exception):
    """The ``Range`` header is unsatisfiable for this resource.

    Carries a short reason for logs only; the wire response is a bare
    ``416`` plus ``Content-Range: bytes * /<size>``.
    """


@dataclass(frozen=True)
class ByteRange:
    """A resolved, single-byte interval. ``length`` is always > 0."""

    start: int
    length: int

    @property
    def end(self) -> int:
        """Inclusive last byte offset."""
        return self.start + self.length - 1


def parse_byte_range(header: str | None, size_bytes: int) -> ByteRange | None:
    """Resolve a single byte range against ``size_bytes``.

    Returns ``None`` when no ``Range`` header was sent (the caller serves
    the whole body). Raises :class:`RangeNotSatisfiable` for anything the
    first version refuses.

    Deliberately narrow. Only three forms are accepted::

        bytes=0-99      closed
        bytes=100-      open-ended
        bytes=-100      suffix

    Everything else -- multiple ranges, a non-``bytes`` unit, non-numeric
    offsets, a reversed pair, a start past EOF, a zero-length result --
    is refused. ``multipart/byteranges`` in particular would hand a
    client interleaved boundaries for a feature it does not need, so a
    multi-range request is answered ``416`` and the client re-asks for one
    range at a time. See ``test_asset_transfer_api.py`` for the locked-in
    behaviour.
    """
    if header is None:
        return None
    raw = header.strip()
    if not raw:
        raise RangeNotSatisfiable("empty Range header")
    unit, sep, spec = raw.partition("=")
    if not sep or unit.strip().lower() != "bytes":
        raise RangeNotSatisfiable(f"unsupported range unit {unit!r}")
    spec = spec.strip()
    if not spec:
        raise RangeNotSatisfiable("empty range spec")
    if "," in spec:
        raise RangeNotSatisfiable("multiple ranges are not supported")
    start_raw, dash, end_raw = spec.partition("-")
    if not dash:
        raise RangeNotSatisfiable(f"malformed range spec {spec!r}")
    start_raw, end_raw = start_raw.strip(), end_raw.strip()
    if size_bytes <= 0:
        raise RangeNotSatisfiable("resource is empty")

    if not start_raw:
        # Suffix form: the last ``len(end_raw)`` bytes.
        if not end_raw.isdigit():
            raise RangeNotSatisfiable("malformed suffix length")
        suffix_len = int(end_raw)
        if suffix_len == 0:
            raise RangeNotSatisfiable("zero-length suffix range")
        start = max(0, size_bytes - suffix_len)
        length = size_bytes - start
    else:
        if not start_raw.isdigit():
            raise RangeNotSatisfiable("malformed range start")
        if end_raw and not end_raw.isdigit():
            raise RangeNotSatisfiable("malformed range end")
        start = int(start_raw)
        if start >= size_bytes:
            raise RangeNotSatisfiable("range start is past end of resource")
        if end_raw:
            end = int(end_raw)
            if end < start:
                raise RangeNotSatisfiable("range end precedes start")
            # A closed range may run past EOF; that is clamped, not an error.
            length = min(end, size_bytes - 1) - start + 1
        else:
            length = size_bytes - start
    if length <= 0:
        raise RangeNotSatisfiable("range resolves to zero bytes")
    return ByteRange(start=start, length=length)


def compute_asset_etag(
    asset_id: str,
    size_bytes: int,
    mtime_ns: int | None,
    sha256: str,
) -> str:
    """Strong, opaque, quoted ETag for one resource version.

    Derived from the identity plus the three version inputs, so it never
    leaks a local path, and it changes if the size, the mtime, or the
    content hash changes. The quotes are part of the value -- an RFC 9110
    ``If-Match`` sends them back verbatim.
    """
    material = "|".join(
        [asset_id, str(size_bytes), "" if mtime_ns is None else str(mtime_ns), sha256]
    )
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return '"' + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=") + '"'


def hash_transfer_token(token: str) -> str:
    """Fingerprint of a data-plane credential; matches the device scheme."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_transfer_token() -> str:
    """256 bits of entropy, URL-safe so it survives a header round-trip."""
    return secrets.token_urlsafe(32)


_SANITIZE_URL_QUERY = re.compile(r"(?P<base>[^\s?\"']+)\?[^\s\"']*")
_SANITIZE_ABS_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/)[^\s\"',;)]+")
_SANITIZE_OPAQUE_SECRET = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")


def sanitize_failure_detail(
    detail: str | None, *, limit: int = MAX_FAILURE_DETAIL_LENGTH
) -> str | None:
    """Strip anything sensitive out of device-reported failure text.

    The text is a client's own error string, so it routinely carries the
    download URL with its token, or the absolute path it was writing to.
    Neither may be persisted, so query strings, absolute paths, and
    long opaque secrets are replaced before the value reaches the
    database.
    """
    if not detail:
        return None
    text = _SANITIZE_URL_QUERY.sub(lambda m: m.group("base"), detail)
    text = _SANITIZE_ABS_PATH.sub("<path>", text)
    text = _SANITIZE_OPAQUE_SECRET.sub("<redacted>", text)
    text = " ".join(text.split())
    return text[:limit] if text else None


@dataclass(frozen=True)
class TransferSource:
    """Where the bytes actually come from.

    Deliberately not ``LOCAL_FILE``-shaped: a future object-storage
    provider fills ``url`` with a short-lived signed URL and HomeMind
    stops proxying bytes entirely. The first version ships only the local
    provider, but nothing in the control plane assumes that.
    """

    kind: Literal["LOCAL_FILE", "SIGNED_URL"]
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    expires_at: int = 0
    supports_range: bool = True


class TransferSourceProvider(Protocol):
    """Turns an indexed asset into a concrete byte source."""

    async def prepare_source(
        self,
        *,
        asset: FamilyAssetRow,
        transfer: AssetTransferRow,
    ) -> TransferSource: ...


def resolve_local_asset_path(asset: FamilyAssetRow) -> Path:
    """Local filesystem path for a ``file://`` indexed asset.

    The path comes from the asset row only -- there is no request field
    that can influence it, which is what keeps this endpoint from turning
    into an arbitrary-file-read primitive.
    """
    parsed = urlparse(asset.uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise HomeMindError(
            HomeMindErrorCode.ASSET_TRANSFER_SOURCE_UNAVAILABLE,
            "asset content is not stored in a local file",
        )
    raw_path = url2pathname(unquote(parsed.path))
    if os.name == "nt" and raw_path.startswith("\\") and raw_path[2:3] == ":":
        raw_path = raw_path[1:]
    return Path(raw_path)


def _stat_or_unavailable(path: Path) -> os.stat_result:
    try:
        return path.stat()
    except OSError as exc:
        raise HomeMindError(
            HomeMindErrorCode.ASSET_TRANSFER_SOURCE_UNAVAILABLE,
            f"asset content is not readable: {exc.strerror or 'stat failed'}",
        ) from exc


def compute_file_sha256(path: Path, *, block_bytes: int = 1024 * 1024) -> str:
    """Streaming SHA-256 of a whole file.

    Block-at-a-time on purpose: a 5 GiB asset must never be materialised
    in memory, and this runs on a worker thread, never on the event loop.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(block_bytes)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def open_verified_file(path: Path, expected_size: int) -> BinaryIO:
    """Open ``path`` and confirm the handle still describes that size.

    ``stat`` then ``open`` is a TOCTOU window in which the file could be
    swapped or replaced by a symlink, so ``O_NOFOLLOW`` is set where the
    platform has it and the size is re-checked with ``fstat`` on the *open
    handle* -- that is the check that actually governs the bytes served.
    """
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise HomeMindError(
            HomeMindErrorCode.ASSET_TRANSFER_SOURCE_UNAVAILABLE,
            f"asset content could not be opened: {exc.strerror or 'open failed'}",
        ) from exc
    handle = os.fdopen(fd, "rb", buffering=0)
    try:
        size = os.fstat(handle.fileno()).st_size
    except OSError as exc:  # pragma: no cover - platform dependent
        handle.close()
        raise HomeMindError(
            HomeMindErrorCode.ASSET_TRANSFER_SOURCE_UNAVAILABLE,
            "asset content could not be inspected",
        ) from exc
    if expected_size >= 0 and size != expected_size:
        handle.close()
        raise HomeMindError(
            HomeMindErrorCode.ASSET_TRANSFER_SOURCE_CHANGED,
            "asset size changed after the transfer was created",
        )
    return handle


def iter_file_range(
    handle: BinaryIO,
    start: int,
    length: int,
    block_bytes: int,
) -> Iterator[bytes]:
    """Yield ``length`` bytes from ``start``, never more.

    The loop is bounded by the remaining count rather than by EOF, so a
    file that grew mid-download cannot inflate the response past the
    length promised in ``Content-Length``.
    """
    handle.seek(start)
    remaining = length
    while remaining > 0:
        block = handle.read(min(block_bytes, remaining))
        if not block:
            # Truncated underneath us. Yielding a short body makes the
            # device see a failed range rather than a silent hole.
            return
        remaining -= len(block)
        yield block


@dataclass(frozen=True)
class ResolvedContent:
    """An authorized data-plane read, ready to be turned into a response."""

    transfer: AssetTransferRow
    asset: FamilyAssetRow
    path: Path
    mime_type: str
    filename: str
    range: ByteRange | None

    @property
    def size_bytes(self) -> int:
        return self.transfer.size_bytes

    @property
    def etag(self) -> str:
        return self.transfer.etag

    @property
    def response_length(self) -> int:
        return self.range.length if self.range is not None else self.size_bytes


@dataclass(frozen=True)
class TransferHandle:
    """Control-plane view of a task: the row plus its one live credential."""

    transfer: AssetTransferRow
    asset: FamilyAssetRow
    token: str
    token_expires_at: int
    chunk_size: int
    max_concurrency: int
    created: bool


class _Admission:
    """Bounded concurrency and handle guards, in process memory.

    Correctness first: a cache would need invalidation on every revoke,
    and this is the cheap half of the answer. The manager is rebuilt per
    request by the router, so all state lives here at module scope.

    Two independent limits, because they defend against different things:

    * per-transfer range slots -- one device going wild;
    * a process-wide open-handle budget -- several devices doing it at
      once, which is the case that actually exhausts file descriptors
      and is invisible to a per-transfer cap.

    Deliberately *not* implemented: a per-device bandwidth ceiling. The
    specification marks it optional, and a real one has to charge actual
    served bytes mid-stream and abort a response that is already 2 GiB in
    -- which trades a throttled download for a corrupt-looking partial
    one. The handle budget plus the per-transfer cap already bound the
    damage a single household can do; revisit when a real uplink
    contention problem exists.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._range_slots: dict[str, int] = {}
        self._creates: dict[str, list[float]] = {}
        self._open_handles = 0

    def acquire_range_slot(
        self,
        transfer_id: str,
        *,
        limit: int,
        max_open_handles: int,
    ) -> tuple[bool, str]:
        """Take one range slot plus one open-handle reservation.

        Returns ``(granted, reason)``. Both reservations are taken under
        one lock, so a granted slot always has a handle budget behind it;
        the caller must give both back through :meth:`release_range_slot`.
        """
        with self._lock:
            current = self._range_slots.get(transfer_id, 0)
            if current >= limit:
                return False, "TOO_MANY_RANGES"
            if self._open_handles >= max_open_handles:
                return False, "SERVER_BUSY"
            self._range_slots[transfer_id] = current + 1
            self._open_handles += 1
        return True, ""

    def release_range_slot(self, transfer_id: str) -> None:
        with self._lock:
            current = self._range_slots.get(transfer_id, 0)
            if current <= 1:
                self._range_slots.pop(transfer_id, None)
            else:
                self._range_slots[transfer_id] = current - 1
            if self._open_handles > 0:
                self._open_handles -= 1

    def allow_create(self, device_id: str, *, limit: int, window: int, now: float) -> bool:
        with self._lock:
            stamps = [t for t in self._creates.get(device_id, []) if now - t < window]
            if len(stamps) >= limit:
                self._creates[device_id] = stamps
                return False
            stamps.append(now)
            self._creates[device_id] = stamps
            return True


_ADMISSION = _Admission()


class AssetTransferManager:
    """Domain service for the device download protocol."""

    def __init__(
        self,
        *,
        device_repo: FamilyDeviceRepo,
        asset_repo: FamilyAssetRepo,
        transfer_repo: AssetTransferRepo,
        device_runtime: DeviceRuntimeManager,
        source_provider: TransferSourceProvider | None = None,
        chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
        idle_ttl_seconds: int = DEFAULT_IDLE_TTL_SECONDS,
        max_active_per_device: int = DEFAULT_MAX_ACTIVE_PER_DEVICE,
        read_block_bytes: int = DEFAULT_READ_BLOCK_BYTES,
        max_concurrent_ranges: int = DEFAULT_MAX_CONCURRENT_RANGES_PER_TRANSFER,
        max_open_handles: int = DEFAULT_MAX_OPEN_FILE_HANDLES,
        create_rate_limit: int = DEFAULT_CREATE_RATE_LIMIT,
        create_rate_window_seconds: int = DEFAULT_CREATE_RATE_WINDOW_SECONDS,
        now: int | None = None,
    ) -> None:
        self.device_repo = device_repo
        self.asset_repo = asset_repo
        self.transfer_repo = transfer_repo
        self.device_runtime = device_runtime
        self.source_provider: TransferSourceProvider = (
            source_provider or LocalFileTransferSourceProvider()
        )
        self.chunk_size_bytes = chunk_size_bytes
        self.max_concurrency = max_concurrency
        self.token_ttl_seconds = token_ttl_seconds
        self.idle_ttl_seconds = idle_ttl_seconds
        self.max_active_per_device = max_active_per_device
        self.read_block_bytes = read_block_bytes
        self.max_concurrent_ranges = max_concurrent_ranges
        self.max_open_handles = max_open_handles
        self.create_rate_limit = create_rate_limit
        self.create_rate_window_seconds = create_rate_window_seconds
        self._now = now

    def _now_ts(self) -> int:
        return int(self._now) if self._now is not None else int(time.time())

    # ------------------------------------------------------------- helpers

    def _authenticate(self, token: str) -> FamilyDeviceRow:
        """Device identity comes from the credential and nowhere else."""
        return self.device_runtime.authenticate_device(token)

    def _own_transfer(self, transfer_id: str, device: FamilyDeviceRow) -> AssetTransferRow:
        """Fetch a transfer the authenticated device is allowed to see.

        A missing row and someone else's row are reported identically --
        the error must not confirm that a transfer id exists at all.
        """
        transfer = self.transfer_repo.get(transfer_id)
        if transfer is None or transfer.device_id != device.id:
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_NOT_FOUND,
                "asset transfer not found",
            )
        return transfer

    @staticmethod
    def _assert_live(transfer: AssetTransferRow, now: int) -> None:
        if transfer.status == "EXPIRED" or (
            transfer.status in ACTIVE_STATUSES and transfer.expires_at <= now
        ):
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_EXPIRED,
                "asset transfer has expired; create a new one",
            )
        if transfer.status not in ACTIVE_STATUSES:
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_TERMINAL,
                f"asset transfer is {transfer.status}",
            )

    def _resolve_asset(self, device: FamilyDeviceRow, asset_id: str) -> FamilyAssetRow:
        asset = self.asset_repo.get(asset_id)
        if asset is None or asset.family_id != device.family_id:
            # Same shape for "no such asset" and "someone else's asset" --
            # a device must not be able to probe another family's library.
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_NOT_FOUND,
                "asset not found",
            )
        if asset.visibility not in DEVICE_READABLE_VISIBILITIES:
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_FORBIDDEN,
                "asset is not available to devices",
            )
        if asset.status != "INDEXED":
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_SOURCE_UNAVAILABLE,
                "asset is not currently available",
            )
        return asset

    async def _asset_version(self, asset: FamilyAssetRow) -> tuple[int, int | None, str]:
        """``(size, mtime_ns, sha256)`` for the asset as it exists now.

        The indexed content hash is reused when it really is a SHA-256
        digest; anything else falls back to a streaming re-hash on a
        worker thread rather than trusting a foreign algorithm.
        """
        path = resolve_local_asset_path(asset)
        stat = await asyncio.to_thread(_stat_or_unavailable, path)
        indexed_hash = (asset.content_hash or "").strip().lower()
        if len(indexed_hash) == _SHA256_HEX_LENGTH and all(
            char in "0123456789abcdef" for char in indexed_hash
        ):
            return int(stat.st_size), int(stat.st_mtime_ns), indexed_hash
        digest = await asyncio.to_thread(compute_file_sha256, path)
        return int(stat.st_size), int(stat.st_mtime_ns), digest

    def _issue_credential(self, transfer_id: str, now: int) -> tuple[str, int]:
        """Mint one data-plane credential, revoking the previous one.

        Refresh revokes first: a rotated credential must stop working
        *before* the replacement is handed out, not after.
        """
        self.transfer_repo.revoke_tokens(transfer_id, now)
        plaintext = mint_transfer_token()
        expires_at = now + self.token_ttl_seconds
        self.transfer_repo.issue_token(
            transfer_id,
            hash_transfer_token(plaintext),
            expires_at=expires_at,
            now=now,
        )
        _hm_inc("asset_transfer_token_issued_total")
        return plaintext, expires_at

    # ------------------------------------------------------- control plane

    async def create_transfer(
        self,
        device_token: str,
        *,
        asset_id: str,
        request_key: str,
    ) -> TransferHandle:
        """Authorize one download and hand back its manifest.

        Idempotent on ``request_key``: a device that retries a lost POST
        gets the *same* transfer rather than a second multi-gigabyte job.
        A retry for an asset whose version has since changed is refused,
        because resuming onto different bytes would produce a corrupt
        file the device has no way to detect.
        """
        now = self._now_ts()
        device = self._authenticate(device_token)
        if not _ADMISSION.allow_create(
            device.id,
            limit=self.create_rate_limit,
            window=self.create_rate_window_seconds,
            now=float(now),
        ):
            _hm_inc("asset_transfer_failed_total")
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_LIMIT_EXCEEDED,
                "too many transfer requests; slow down",
            )

        asset = self._resolve_asset(device, asset_id)
        existing = self.transfer_repo.get_by_request_key(device.id, request_key)
        if existing is not None:
            if existing.asset_id != asset.id:
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_INVALID,
                    "request_key is already bound to a different asset",
                )
            self._assert_live(existing, now)
            if existing.status == "PENDING":
                existing = self.transfer_repo.activate(existing.id, now) or existing
            size, mtime_ns, sha256 = await self._asset_version(asset)
            if not self._version_matches(existing, size, mtime_ns, sha256):
                self._fail_version_changed(existing.id, now)
                raise HomeMindError(
                    HomeMindErrorCode.ASSET_TRANSFER_SOURCE_CHANGED,
                    "asset changed since this transfer was created",
                )
            plaintext, expires_at = self._issue_credential(existing.id, now)
            return TransferHandle(
                transfer=existing,
                asset=asset,
                token=plaintext,
                token_expires_at=expires_at,
                chunk_size=existing.chunk_size,
                max_concurrency=self.max_concurrency,
                created=False,
            )

        active = self.transfer_repo.count_active_for_device(device.id, now=now)
        if active >= self.max_active_per_device:
            _hm_inc("asset_transfer_failed_total")
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_LIMIT_EXCEEDED,
                "too many active asset transfers for this device",
            )

        size, mtime_ns, sha256 = await self._asset_version(asset)
        etag = compute_asset_etag(asset.id, size, mtime_ns, sha256)
        created = self.transfer_repo.create(
            family_id=device.family_id,
            asset_id=asset.id,
            device_id=device.id,
            request_key=request_key,
            size_bytes=size,
            sha256=sha256,
            source_mtime_ns=mtime_ns,
            etag=etag,
            chunk_size=self.chunk_size_bytes,
            expires_at=now + self.idle_ttl_seconds,
            now=now,
        )
        if created.status == "PENDING":
            created = self.transfer_repo.activate(created.id, now) or created
        plaintext, expires_at = self._issue_credential(created.id, now)
        _hm_inc("asset_transfer_created_total")
        return TransferHandle(
            transfer=created,
            asset=asset,
            token=plaintext,
            token_expires_at=expires_at,
            chunk_size=created.chunk_size,
            max_concurrency=self.max_concurrency,
            created=True,
        )

    async def get_transfer(self, device_token: str, transfer_id: str) -> AssetTransferRow:
        device = self._authenticate(device_token)
        return self._own_transfer(transfer_id, device)

    async def list_transfers(
        self,
        device_token: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AssetTransferRow]:
        device = self._authenticate(device_token)
        return self.transfer_repo.list_for_device(device.id, status=status, limit=limit)

    async def refresh_token(self, device_token: str, transfer_id: str) -> TransferHandle:
        """Rotate the data-plane credential for a live transfer."""
        now = self._now_ts()
        device = self._authenticate(device_token)
        transfer = self._own_transfer(transfer_id, device)
        self._assert_live(transfer, now)
        asset = self._resolve_asset(device, transfer.asset_id)
        size, mtime_ns, sha256 = await self._asset_version(asset)
        if not self._version_matches(transfer, size, mtime_ns, sha256):
            self._fail_version_changed(transfer.id, now)
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_SOURCE_CHANGED,
                "asset changed since this transfer was created",
            )
        plaintext, expires_at = self._issue_credential(transfer.id, now)
        _hm_inc("asset_transfer_token_refresh_total")
        return TransferHandle(
            transfer=transfer,
            asset=asset,
            token=plaintext,
            token_expires_at=expires_at,
            chunk_size=transfer.chunk_size,
            max_concurrency=self.max_concurrency,
            created=False,
        )

    async def report_progress(
        self,
        device_token: str,
        transfer_id: str,
        *,
        bytes_downloaded: int,
    ) -> AssetTransferRow:
        now = self._now_ts()
        device = self._authenticate(device_token)
        transfer = self._own_transfer(transfer_id, device)
        self._assert_live(transfer, now)
        clamped = max(0, min(int(bytes_downloaded), transfer.size_bytes))
        updated = self.transfer_repo.advance_progress(transfer.id, clamped, now)
        if updated is None:  # pragma: no cover - row vanished mid-request
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_NOT_FOUND,
                "asset transfer not found",
            )
        return updated

    async def complete(
        self,
        device_token: str,
        transfer_id: str,
        *,
        size_bytes: int,
        sha256: str,
    ) -> AssetTransferRow:
        """Accept the device's completion report, once the hash agrees.

        The device is the only side that can compute the digest of the
        bytes it assembled, so this is the trust boundary: a report that
        disagrees with the manifest means the assembled file is corrupt,
        and marking it COMPLETED would record a lie.
        """
        now = self._now_ts()
        device = self._authenticate(device_token)
        transfer = self._own_transfer(transfer_id, device)
        if transfer.status == "COMPLETED":
            return transfer  # idempotent replay
        self._assert_live(transfer, now)
        digest = (sha256 or "").strip().lower()
        if int(size_bytes) != transfer.size_bytes or digest != transfer.sha256.lower():
            _hm_inc("asset_transfer_hash_mismatch_total")
            self.transfer_repo.fail(
                transfer.id,
                "HASH_MISMATCH",
                "device-reported digest does not match",
                now,
            )
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_HASH_MISMATCH,
                "reported size or digest does not match the manifest",
            )
        updated = self.transfer_repo.complete(transfer.id, now)
        self.transfer_repo.revoke_tokens(transfer.id, now)
        _hm_inc("asset_transfer_completed_total")
        if updated is None:  # pragma: no cover - row vanished mid-request
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_NOT_FOUND,
                "asset transfer not found",
            )
        return updated

    async def fail(
        self,
        device_token: str,
        transfer_id: str,
        *,
        code: str,
        detail: str | None,
    ) -> AssetTransferRow:
        now = self._now_ts()
        device = self._authenticate(device_token)
        transfer = self._own_transfer(transfer_id, device)
        if transfer.is_terminal:
            return transfer  # idempotent: a terminal state is final
        updated = self.transfer_repo.fail(
            transfer.id,
            code or "DEVICE_REPORTED",
            sanitize_failure_detail(detail),
            now,
        )
        self.transfer_repo.revoke_tokens(transfer.id, now)
        _hm_inc("asset_transfer_failed_total")
        if updated is None:  # pragma: no cover - row vanished mid-request
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_NOT_FOUND,
                "asset transfer not found",
            )
        return updated

    def cancel(self, family_id: str, transfer_id: str) -> AssetTransferRow | None:
        """Family-side manual cancel."""
        now = self._now_ts()
        transfer = self.transfer_repo.get(transfer_id)
        if transfer is None or transfer.family_id != family_id:
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_NOT_FOUND,
                "asset transfer not found",
            )
        if transfer.is_terminal:
            return transfer
        self.transfer_repo.cancel(transfer_id, now)
        updated = self.transfer_repo.get(transfer_id)
        self.transfer_repo.revoke_tokens(transfer_id, now)
        _hm_inc("asset_transfer_cancelled_total")
        return updated

    def cancel_for_asset(self, asset_id: str) -> list[AssetTransferRow]:
        """Called when the index entry disappears."""
        cancelled = self.transfer_repo.cancel_for_asset(asset_id, self._now_ts())
        if cancelled:
            _hm_inc("asset_transfer_cancelled_total", len(cancelled))
        return cancelled

    def cancel_for_device(self, device_id: str) -> list[AssetTransferRow]:
        """Called when a device is revoked or removed."""
        cancelled = self.transfer_repo.cancel_for_device(device_id, self._now_ts())
        if cancelled:
            _hm_inc("asset_transfer_cancelled_total", len(cancelled))
        return cancelled

    def expire_stale(self, *, limit: int = 200) -> list[AssetTransferRow]:
        """Idle-task sweep. Terminal tasks keep their row for audit."""
        expired = self.transfer_repo.expire_before(self._now_ts(), limit=limit)
        if expired:
            _hm_inc("asset_transfer_expired_total", len(expired))
        return expired

    # --------------------------------------------------------- data plane

    @staticmethod
    def _version_matches(
        transfer: AssetTransferRow,
        size: int,
        mtime_ns: int | None,
        sha256: str,
    ) -> bool:
        if transfer.size_bytes != size:
            return False
        if transfer.sha256.lower() != sha256.lower():
            return False
        if transfer.source_mtime_ns is not None and mtime_ns is not None:
            return transfer.source_mtime_ns == mtime_ns
        return True

    def _fail_version_changed(self, transfer_id: str, now: int) -> None:
        self.transfer_repo.fail(transfer_id, "SOURCE_CHANGED", "asset version changed", now)
        self.transfer_repo.revoke_tokens(transfer_id, now)

    def _authorize_data_request(
        self,
        transfer_token: str,
        *,
        now: int,
    ) -> ResolvedContent:
        """Every field a byte read needs, re-checked on every request.

        Ordering matters: credential, then transfer, then device, then
        asset, then the file itself. A transfer created before the device
        was revoked must fail here, not only at creation time.
        """
        token = self.transfer_repo.resolve_active_token(
            hash_transfer_token(transfer_token),
            now=now,
        )
        if token is None:
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_TOKEN_INVALID,
                "transfer credential rejected",
            )
        transfer = self.transfer_repo.get(token.transfer_id)
        if transfer is None or transfer.status not in ACTIVE_STATUSES:
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_TERMINAL,
                "asset transfer is no longer active",
            )
        if transfer.expires_at <= now:
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_EXPIRED,
                "asset transfer has expired",
            )
        device = self.device_repo.get(transfer.device_id)
        if device is None or device.family_id != transfer.family_id:
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_FORBIDDEN,
                "device no longer owns this transfer",
            )
        if not self.device_repo.has_active_credential(device.id, now=now):
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_FORBIDDEN,
                "device credential has been revoked",
            )
        asset = self._resolve_asset(device, transfer.asset_id)
        path = resolve_local_asset_path(asset)
        stat = _stat_or_unavailable(path)
        if not self._version_matches(
            transfer,
            int(stat.st_size),
            int(stat.st_mtime_ns),
            transfer.sha256,
        ):
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_SOURCE_CHANGED,
                "asset changed after the transfer was created",
            )
        return ResolvedContent(
            transfer=transfer,
            asset=asset,
            path=path,
            mime_type=asset.mime_type or "application/octet-stream",
            filename=asset.name,
            range=None,
        )

    def authorize_content(
        self,
        transfer_token: str,
        *,
        if_match: str | None = None,
        range_header: str | None = None,
    ) -> ResolvedContent:
        """Resolve one HEAD / GET into an authorized byte source.

        ``If-Match`` is checked *before* the range so a client whose file
        changed is told to restart rather than being handed a 416 for a
        range computed against stale geometry.
        """
        now = self._now_ts()
        resolved = self._authorize_data_request(transfer_token, now=now)
        if (
            if_match is not None
            and if_match.strip() not in ("", "*")
            and if_match.strip() != resolved.etag
        ):
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_SOURCE_CHANGED,
                "If-Match does not describe this resource version",
            )
        if not _ADMISSION.acquire_range_slot(
            resolved.transfer.id,
            limit=self.max_concurrent_ranges,
            max_open_handles=self.max_open_handles,
        ):
            _hm_inc("asset_transfer_range_rejected_total")
            raise HomeMindError(
                HomeMindErrorCode.ASSET_TRANSFER_LIMIT_EXCEEDED,
                "too many concurrent range requests for this transfer",
            )
        try:
            parsed = parse_byte_range(range_header, resolved.size_bytes)
        except RangeNotSatisfiable:
            _ADMISSION.release_range_slot(resolved.transfer.id)
            _hm_inc("asset_transfer_range_rejected_total")
            raise
        return ResolvedContent(
            transfer=resolved.transfer,
            asset=resolved.asset,
            path=resolved.path,
            mime_type=resolved.mime_type,
            filename=resolved.filename,
            range=parsed,
        )

    def release_content(self, resolved: ResolvedContent) -> None:
        """Give the range-admission slot back; always pair with authorize."""
        _ADMISSION.release_range_slot(resolved.transfer.id)

    async def stream_bytes(self, resolved: ResolvedContent) -> AsyncIterator[bytes]:
        """Stream the authorized bytes, opening the handle inside the loop.

        Opening *inside* the generator is what makes the handle safe: the
        ``finally`` runs when the client disconnects mid-transfer, so an
        aborted 5 GiB download does not strand a descriptor.
        """
        byte_range = resolved.range
        start = byte_range.start if byte_range is not None else 0
        length = byte_range.length if byte_range is not None else resolved.size_bytes
        block_bytes = self.read_block_bytes
        handle = await asyncio.to_thread(open_verified_file, resolved.path, resolved.size_bytes)
        try:
            async for chunk in iterate_in_threadpool(
                iter_file_range(handle, start, length, block_bytes),
            ):
                yield chunk
                _hm_inc("asset_transfer_bytes_served_total", len(chunk))
        finally:
            with contextlib.suppress(OSError):
                handle.close()
            _hm_inc(
                "asset_transfer_range_requests_total"
                if byte_range is not None
                else "asset_transfer_full_requests_total",
            )


class LocalFileTransferSourceProvider:
    """First-version provider: bytes come off this server's disk."""

    kind = "LOCAL_FILE"

    async def prepare_source(
        self,
        *,
        asset: FamilyAssetRow,
        transfer: AssetTransferRow,
    ) -> TransferSource:
        path = resolve_local_asset_path(asset)
        await asyncio.to_thread(_stat_or_unavailable, path)
        return TransferSource(
            kind="LOCAL_FILE",
            url=str(path),
            headers={},
            expires_at=0,
            supports_range=True,
        )


__all__ = [
    "AssetTransferManager",
    "ByteRange",
    "DEFAULT_CHUNK_SIZE_BYTES",
    "DEFAULT_IDLE_TTL_SECONDS",
    "DEFAULT_MAX_CONCURRENCY",
    "DEFAULT_READ_BLOCK_BYTES",
    "DEFAULT_TOKEN_TTL_SECONDS",
    "LocalFileTransferSourceProvider",
    "RangeNotSatisfiable",
    "ResolvedContent",
    "TransferHandle",
    "TransferSource",
    "TransferSourceProvider",
    "compute_asset_etag",
    "compute_file_sha256",
    "hash_transfer_token",
    "iter_file_range",
    "mint_transfer_token",
    "open_verified_file",
    "parse_byte_range",
    "resolve_local_asset_path",
    "sanitize_failure_detail",
]
