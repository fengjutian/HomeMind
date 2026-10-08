"""UploadBlobStore — oversized uploads that cannot use the bytes-only workspace API."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.uploads.blobstore import _STREAM_BUFFER, UploadBlobStore
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def store(tmp_path: Path) -> UploadBlobStore:
    return UploadBlobStore(PathLayout(root=tmp_path / "home"))


def _make_assembled(store: UploadBlobStore, upload_id: str, size: int) -> Path:
    """Write a sparse assembled file and return its staging path."""
    staging = store._paths.ensure_upload_staging_dir(upload_id)
    assembled = staging / "assembled.bin"
    with assembled.open("wb") as handle:
        handle.truncate(size)
    return assembled


def test_promote_moves_the_file_without_copying(store: UploadBlobStore) -> None:
    uid = "01HQ0000000000000000000001"
    assembled = _make_assembled(store, uid, 5 * 1024 * 1024)
    target = store.promote(uid, assembled)

    assert target.is_file()
    assert target == store.blob_path(uid)
    assert not assembled.exists(), "promote must move, not copy"
    assert store.exists(uid) is True


def test_promote_keeps_content_intact(store: UploadBlobStore) -> None:
    uid = "01HQ0000000000000000000002"
    staging = store._paths.ensure_upload_staging_dir(uid)
    assembled = staging / "assembled.bin"
    payload = bytes(range(256)) * 4096  # 1 MiB
    assembled.write_bytes(payload)

    store.promote(uid, assembled)
    assert store.blob_path(uid).read_bytes() == payload


def test_size_and_missing_blob(store: UploadBlobStore) -> None:
    uid = "01HQ0000000000000000000003"
    assembled = _make_assembled(store, uid, 4096)
    store.promote(uid, assembled)
    assert store.size(uid) == 4096

    with pytest.raises(OctopError) as excinfo:
        store.size("01HQ0000000000000000009999")
    assert excinfo.value.code is ErrorCode.UPLOAD_SESSION_NOT_FOUND


def test_iter_chunks_streams_without_loading_everything(store: UploadBlobStore) -> None:
    """A blob far larger than the buffer must still stream in bounded pieces."""
    uid = "01HQ0000000000000000000004"
    total = _STREAM_BUFFER * 3 + 7
    _make_assembled(store, uid, total)
    store.promote(uid, store._paths.upload_staging_dir(uid) / "assembled.bin")

    chunks = list(store.iter_chunks(uid))
    assert sum(len(c) for c in chunks) == total
    assert all(len(c) <= _STREAM_BUFFER for c in chunks)
    assert len(chunks) == 4  # 3 full buffers plus the 7-byte tail


@pytest.mark.asyncio
async def test_stream_chunks_yields_the_whole_blob(store: UploadBlobStore) -> None:
    uid = "01HQ0000000000000000000005"
    payload = bytes(range(256)) * 512
    assembled = _make_assembled(store, uid, len(payload))
    assembled.write_bytes(payload)
    store.promote(uid, assembled)

    streamed = b"".join([chunk async for chunk in store.stream_chunks(uid)])
    assert streamed == payload


def test_blob_ids_cannot_escape_the_blob_root(store: UploadBlobStore) -> None:
    """blob_dir goes through the same id validation as staging."""
    for bad in ["../escape", "/abs", "a/b", ""]:
        with pytest.raises(ValueError):
            store.blob_dir(bad)


def test_delete_reclaims_the_blob(store: UploadBlobStore) -> None:
    uid = "01HQ0000000000000000000006"
    _make_assembled(store, uid, 1024)
    store.promote(uid, store._paths.upload_staging_dir(uid) / "assembled.bin")
    assert store.blob_dir(uid).exists()

    store.delete(uid)
    assert not store.blob_dir(uid).exists()
    assert store.exists(uid) is False