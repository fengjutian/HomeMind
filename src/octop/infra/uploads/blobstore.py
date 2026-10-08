"""Blob store for assembled uploads too large for the bytes-based workspace API.

``octop-harness`` only exposes byte-oriented workspace writes
(``aupload_bytes`` / ``upload_files([(key, bytes)])``), so landing a multi-GB
file through the workspace would require the whole file in memory. Files above
the configured cap therefore live here instead, and are served by a streaming
route.

The assembled artifact is **moved** with an atomic rename, never copied, so
promoting it to a blob costs no extra I/O and no extra memory.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout

#: Read size for streaming downloads. Fixed, so a download costs this constant.
_STREAM_BUFFER = 1024 * 1024


class UploadBlobStore:
    """Octop-owned storage for oversized assembled uploads."""

    def __init__(self, paths: PathLayout) -> None:
        self._paths = paths

    @property
    def blobs_dir(self) -> Path:
        return self._paths.uploads_blobs_dir

    def blob_dir(self, upload_id: str) -> Path:
        """Blob dir for one upload; ``upload_id`` is validated by PathLayout."""
        return self._paths.upload_blob_dir(upload_id)

    def blob_path(self, upload_id: str) -> Path:
        return self.blob_dir(upload_id) / "payload.bin"

    def exists(self, upload_id: str) -> bool:
        return self.blob_path(upload_id).is_file()

    def size(self, upload_id: str) -> int:
        path = self.blob_path(upload_id)
        if not path.is_file():
            raise OctopError(ErrorCode.UPLOAD_SESSION_NOT_FOUND, "upload blob not found")
        return path.stat().st_size

    def promote(self, upload_id: str, assembled: Path) -> Path:
        """Move the assembled staging file into the blob store atomically."""
        target = self.blob_path(upload_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Same volume by construction, so this is an atomic metadata move.
            os.replace(assembled, target)
        except OSError:
            # Cross-device or locked: fall back to a copy, still without
            # loading the file into memory.
            shutil.copyfile(assembled, target)
            assembled.unlink(missing_ok=True)
        return target

    def delete(self, upload_id: str) -> None:
        shutil.rmtree(self.blob_dir(upload_id), ignore_errors=True)

    def iter_chunks(self, upload_id: str) -> Iterator[bytes]:
        """Synchronous chunk iterator over a blob."""
        path = self.blob_path(upload_id)
        if not path.is_file():
            raise OctopError(ErrorCode.UPLOAD_SESSION_NOT_FOUND, "upload blob not found")
        with path.open("rb") as handle:
            while True:
                buffer = handle.read(_STREAM_BUFFER)
                if not buffer:
                    return
                yield buffer

    async def stream_chunks(self, upload_id: str) -> AsyncIterator[bytes]:
        """Async chunk iterator for the streaming download route."""
        for buffer in self.iter_chunks(upload_id):
            yield buffer
