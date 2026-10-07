"""Device runtime contract tests.

Two things are worth proving here, and they pull in opposite directions:

* **The wire contract** — the runtime must ACK before doing the work,
  report failures as failures rather than as transport errors, and stop
  on a 401 instead of retrying. A scripted fake server drives these
  without binding a socket.
* **The path contract** — nothing outside the authorised root may be
  read or written, whatever a command asks for. These run on Windows and
  POSIX, so no assertion may assume a ``/`` separator.

Cross-platform rules that shaped this file: paths are built with
``Path`` / ``tmp_path``, and comparisons use ``Path`` equality rather
than string prefixes (a string prefix would accept ``C:/data-evil`` for a
root of ``C:/data``).
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from homemind.device_runtime import (
    PROTOCOL_VERSION,
    AuthenticationLost,
    CommandContext,
    CommandRefused,
    DeviceCredential,
    PathEscapeError,
    RuntimeClient,
    UnsupportedCommand,
    execute,
    run_once,
)
from homemind.device_runtime.commands import SUPPORTED_CAPABILITIES
from homemind.device_runtime.path_guard import is_within, resolve_within

# ------------------------------------------------------------ path guard


def test_a_relative_path_resolves_under_the_root(tmp_path: Path) -> None:
    (tmp_path / "photos").mkdir()
    resolved = resolve_within("photos", str(tmp_path))
    assert resolved == (tmp_path / "photos").resolve()


def test_dot_dot_cannot_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(PathEscapeError):
        resolve_within("../secret.txt", str(root))


def test_deep_traversal_cannot_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PathEscapeError):
        resolve_within("a/b/c/../../../../etc/passwd", str(root))


def test_a_sibling_prefix_is_not_inside_the_root(tmp_path: Path) -> None:
    """``C:/data-evil`` starts with ``C:/data`` as text but is not inside.

    A ``startswith`` check would let this through; ``commonpath`` does not.
    """
    root = tmp_path / "data"
    root.mkdir()
    sibling = tmp_path / "data-evil"
    sibling.mkdir()
    with pytest.raises(PathEscapeError):
        resolve_within(str(sibling), str(root))


def test_a_symlink_out_of_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")
    with pytest.raises(PathEscapeError):
        resolve_within(str(link / "secret.txt"), str(root))


def test_an_empty_root_refuses_everything(tmp_path: Path) -> None:
    with pytest.raises(PathEscapeError):
        resolve_within("anything", "")


def test_a_missing_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PathEscapeError):
        resolve_within("x", str(tmp_path / "nope"))


def test_is_within_matches_the_server_side_predicate(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert is_within(".", str(root)) is True
    assert is_within("..", str(root)) is False
    assert is_within("x", None) is False


# --------------------------------------------------------------- commands


def _ctx(root: Path) -> CommandContext:
    return CommandContext(root=str(root), command_id="cmd-1")


def test_list_returns_entries_relative_to_the_root(tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_text("a", encoding="utf-8")
    (tmp_path / "b.jpg").write_text("b", encoding="utf-8")
    result = execute(_ctx(tmp_path), "filesystem.list", {"path": "."})
    assert result["count"] == 2
    assert sorted(e["name"] for e in result["entries"]) == ["a.jpg", "b.jpg"]


def test_hash_is_stable_and_matches_sha256(tmp_path: Path) -> None:
    import hashlib

    target = tmp_path / "a.bin"
    target.write_bytes(b"hello runtime")
    result = execute(_ctx(tmp_path), "filesystem.hash", {"path": "a.bin"})
    assert result["sha256"] == hashlib.sha256(b"hello runtime").hexdigest()


def test_scan_reports_relative_paths(tmp_path: Path) -> None:
    nested = tmp_path / "2024" / "july"
    nested.mkdir(parents=True)
    (nested / "IMG_1.jpg").write_text("x", encoding="utf-8")
    result = execute(_ctx(tmp_path), "filesystem.scan", {"path": "."})
    assert result["count"] == 1
    # POSIX separators in the wire payload regardless of host platform:
    # the server may parse it on another operating system.
    assert result["files"] == ["2024/july/IMG_1.jpg"]


def test_scan_skips_the_trash_directory(tmp_path: Path) -> None:
    trash = tmp_path / ".homemind-trash"
    trash.mkdir()
    (trash / "old.jpg").write_text("x", encoding="utf-8")
    (tmp_path / "keep.jpg").write_text("x", encoding="utf-8")
    result = execute(_ctx(tmp_path), "filesystem.scan", {"path": "."})
    assert result["files"] == ["keep.jpg"]


def test_mkdir_is_idempotent(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    first = execute(ctx, "filesystem.mkdir", {"path": "albums"})
    second = execute(ctx, "filesystem.mkdir", {"path": "albums"})
    assert first["created"] is True
    assert second["created"] is False
    assert (tmp_path / "albums").is_dir()


def test_copy_then_move(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    (tmp_path / "a.jpg").write_text("photo", encoding="utf-8")
    copied = execute(
        ctx,
        "filesystem.copy",
        {"source_path": "a.jpg", "destination_path": "backup/a.jpg"},
    )
    assert copied["changed"] is True
    assert (tmp_path / "backup" / "a.jpg").read_text(encoding="utf-8") == "photo"

    moved = execute(
        ctx,
        "filesystem.move",
        {"source_path": "backup/a.jpg", "destination_path": "final/a.jpg"},
    )
    assert moved["changed"] is True
    assert not (tmp_path / "backup" / "a.jpg").exists()
    assert (tmp_path / "final" / "a.jpg").exists()


def test_a_redelivered_move_is_not_an_error(tmp_path: Path) -> None:
    """A lease-lapsed command may be re-dispatched; the runtime must
    report success rather than failing on the second attempt."""
    ctx = _ctx(tmp_path)
    (tmp_path / "a.jpg").write_text("photo", encoding="utf-8")
    execute(
        ctx,
        "filesystem.move",
        {"source_path": "a.jpg", "destination_path": "b/a.jpg"},
    )
    again = execute(
        ctx,
        "filesystem.move",
        {"source_path": "a.jpg", "destination_path": "b/a.jpg"},
    )
    assert again["changed"] is False
    assert again["reason"] == "destination already present"


def test_a_command_that_escapes_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(CommandRefused):
        execute(
            _ctx(root),
            "filesystem.stat",
            {"path": str(outside)},
        )
    assert outside.read_text(encoding="utf-8") == "secret"


def test_delete_is_not_a_capability(tmp_path: Path) -> None:
    """A household's photos cannot be removed by an unattended process."""
    assert "filesystem.delete" not in SUPPORTED_CAPABILITIES
    with pytest.raises(UnsupportedCommand):
        execute(_ctx(tmp_path), "filesystem.delete", {"path": "a.jpg"})


def test_a_newer_protocol_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedCommand):
        execute(
            _ctx(tmp_path),
            "filesystem.list",
            {"path": "."},
            protocol_version=PROTOCOL_VERSION + 1,
        )


# ---------------------------------------------------------- fake server


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


class _FakeServer:
    """Scripted responses keyed by request path suffix."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, request: Any, timeout: float = 0.0) -> _FakeResponse:
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.calls.append((request.method, request.full_url, body))
        for suffix, response in self.routes.items():
            if request.full_url.endswith(suffix):
                if isinstance(response, Exception):
                    raise response
                return response(request) if callable(response) else response
        raise AssertionError(f"unexpected request: {request.full_url}")


def _credential(root: Path) -> DeviceCredential:
    return DeviceCredential(
        device_id="dev-1",
        token="secret-token",
        server="https://homemind.example",
        root=str(root),
    )


def _command(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"id": "cmd-1", "capability": capability, "payload": payload}


def test_the_loop_acks_before_reporting(tmp_path: Path) -> None:
    """A slow command must not look lost to the server's lease."""
    server = _FakeServer(
        {
            "/heartbeat": _FakeResponse({"ok": True}),
            "/commands": _FakeResponse(_command("filesystem.list", {"path": "."})),
            "/commands/cmd-1/ack": _FakeResponse({"ok": True}),
            "/commands/cmd-1/result": _FakeResponse({"ok": True}),
        }
    )
    client = RuntimeClient(_credential(tmp_path), opener=server)
    assert run_once(client) is True
    paths = [url.rsplit("/runtime", 1)[-1] for _method, url, _body in server.calls]
    assert paths.index("/commands/cmd-1/ack") < paths.index("/commands/cmd-1/result")


def test_the_loop_keeps_the_token_out_of_the_log_body(tmp_path: Path) -> None:
    """The token travels in the header, never in a request body."""
    server = _FakeServer(
        {
            "/heartbeat": _FakeResponse({"ok": True}),
            "/commands": _FakeResponse({}),
        }
    )
    client = RuntimeClient(_credential(tmp_path), opener=server)
    run_once(client)
    for _method, _url, body in server.calls:
        if body is not None:
            assert "secret-token" not in json.dumps(body)


def test_a_refused_command_is_reported_as_failed_not_raised(tmp_path: Path) -> None:
    """A well-understood negative answer must reach the server."""
    server = _FakeServer(
        {
            "/heartbeat": _FakeResponse({"ok": True}),
            "/commands": _FakeResponse(
                _command("filesystem.stat", {"path": "../escape"}),
            ),
            "/commands/cmd-1/ack": _FakeResponse({"ok": True}),
            "/commands/cmd-1/result": _FakeResponse({"ok": True}),
        }
    )
    client = RuntimeClient(_credential(tmp_path), opener=server)
    assert run_once(client) is True
    reported = [body for _m, url, body in server.calls if url.endswith("/result")]
    assert reported and reported[0]["status"] == "FAILED"
    assert "escapes" in reported[0]["error"]


def test_an_unsupported_capability_is_reported_not_executed(tmp_path: Path) -> None:
    server = _FakeServer(
        {
            "/heartbeat": _FakeResponse({"ok": True}),
            "/commands": _FakeResponse(_command("filesystem.delete", {"path": "a"})),
            "/commands/cmd-1/ack": _FakeResponse({"ok": True}),
            "/commands/cmd-1/result": _FakeResponse({"ok": True}),
        }
    )
    client = RuntimeClient(_credential(tmp_path), opener=server)
    run_once(client)
    reported = [body for _m, url, body in server.calls if url.endswith("/result")]
    assert reported and reported[0]["status"] == "FAILED"


def test_a_revoked_token_stops_the_runtime(tmp_path: Path) -> None:
    """A 401 is terminal: retrying hammers the server and keeps using a
    credential the operator already invalidated."""
    server = _FakeServer(
        {
            "/heartbeat": urllib.error.HTTPError(
                "https://homemind.example/api/homemind/runtime/heartbeat",
                401,
                "unauthorized",
                {},
                None,  # type: ignore[arg-type]
            ),
        }
    )
    client = RuntimeClient(_credential(tmp_path), opener=server)
    with pytest.raises(AuthenticationLost):
        run_once(client)


def test_an_empty_queue_is_not_an_error(tmp_path: Path) -> None:
    server = _FakeServer(
        {
            "/heartbeat": _FakeResponse({"ok": True}),
            "/commands": _FakeResponse({}),
        }
    )
    client = RuntimeClient(_credential(tmp_path), opener=server)
    assert run_once(client) is False


def test_a_network_blip_is_recoverable(tmp_path: Path) -> None:
    server = _FakeServer(
        {
            "/heartbeat": _FakeResponse({"ok": True}),
            "/commands": urllib.error.URLError("connection reset"),
        }
    )
    client = RuntimeClient(_credential(tmp_path), opener=server)
    with pytest.raises(ConnectionError):
        run_once(client)
