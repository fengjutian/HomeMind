"""BridgeManager — connection lifecycle, live sessions, HTTP tunnel, turn relay."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
import websockets

from octop.infra.bridge.audit import audit_from_frame, new_audit
from octop.infra.bridge.crypto import decrypt_payload, encrypt_payload
from octop.infra.bridge.http_tunnel import decode_body_b64, execute_local_http
from octop.infra.bridge.icons import rewrite_remote_icon_url
from octop.infra.bridge.ids import (
    format_bridge_agent_id,
    rewrite_peer_agent_ids,
    rewrite_peer_stream_frame,
)
from octop.infra.bridge.peer_auth import login_peer, normalize_peer_base_url, peer_ws_url
from octop.infra.bridge.peer_turn import (
    PeerBrowserRunner,
    PeerTurnRejected,
    PeerTurnRunner,
    bridge_turn_ws_payload,
)
from octop.infra.bridge.protocol import (
    LOCAL_CAPABILITIES,
    LOCAL_VERSION_STRING,
    MAX_FRAME_BYTES,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    Hello,
    ProtocolIncompatible,
    arbitrate_dial,
    build_hello,
    negotiate,
)
from octop.infra.bridge.states import BridgeState, coerce_state, persist_state
from octop.infra.bridge.token_refresh import (
    RefreshFailure,
    TokenRefresher,
    classify_login_failure,
    should_refresh,
)
from octop.infra.bridge.transport import BridgeSession
from octop.infra.db.repos.bridge_connections import BridgeConnectionRepo, BridgeConnectionRow
from octop.infra.db.repos.secrets import SecretRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_short_id, new_ulid

logger = logging.getLogger(__name__)


def _local_version() -> str:
    """This build's version, advertised in hello for diagnostics."""
    from octop import __version__

    return __version__


# Auto-reconnect: backoff after unexpected disconnect; disable after this many failures.
_AUTO_RECONNECT_MAX_FAILURES = 5
_AUTO_RECONNECT_BACKOFF_SEC = (2.0, 4.0, 8.0, 16.0, 30.0)


def _is_inbound(row: BridgeConnectionRow) -> bool:
    """Peer-dialed reverse rows store no password and cannot redial."""
    return not bool(row.credential_blob)


# Re-export for existing unit tests.
_bridge_turn_ws_payload = bridge_turn_ws_payload


def _looks_like_endpoint_label(name: str) -> bool:
    """True when a display name is an auto-filled URL / IPv4, not a nickname."""
    n = (name or "").strip()
    if not n:
        return True
    low = n.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return True
    return n.count(".") >= 3 and any(ch.isdigit() for ch in n)


def inbound_default_display_name(
    *,
    connection_id: str,
    preferred_name: str = "",
    peer_username: str = "",
    existing_name: str | None = None,
) -> str:
    """Human label for a reverse (peer-dialed) row — never the advertised URL."""
    existing = (existing_name or "").strip()
    if existing and existing != connection_id and not _looks_like_endpoint_label(existing):
        return existing
    name = (preferred_name or "").strip() or (peer_username or "").strip() or connection_id
    if _looks_like_endpoint_label(name):
        name = (peer_username or "").strip() or connection_id
    return name


def _absolute_peer_url(base_url: str, maybe_path: str | None) -> str | None:
    raw = (maybe_path or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    root = base_url.rstrip("/") + "/"
    return urljoin(root, raw.lstrip("/"))


def _probe_agent_summary(
    item: dict[str, Any],
    *,
    peer_base_url: str,
    connection_id: str | None = None,
) -> dict[str, Any]:
    remote_id = str(item.get("agent_id") or item.get("id") or "").strip()
    raw_icon = str(item.get("icon_url") or "").strip()
    if not raw_icon:
        legacy = str(item.get("icon") or "").strip()
        if legacy.startswith("/") or legacy.startswith("http://") or legacy.startswith("https://"):
            raw_icon = legacy
    bridge_id = (
        format_bridge_agent_id(connection_id, remote_id) if connection_id and remote_id else None
    )
    _ = peer_base_url  # probe never returns peer-absolute URLs (browser cannot auth to them)
    icon_name = item.get("icon_name")
    color = item.get("color")
    return {
        "agent_id": remote_id,
        "name": str(item.get("name") or remote_id or "").strip() or remote_id,
        "description": str(item.get("description") or "").strip() or None,
        "icon_url": rewrite_remote_icon_url(
            raw_icon or None,
            remote_agent_id=remote_id,
            bridge_agent_id=bridge_id,
        ),
        "icon_name": str(icon_name).strip() or None if isinstance(icon_name, str) else None,
        "color": str(color).strip() or None if isinstance(color, str) else None,
        "state": item.get("state"),
        "kind": item.get("kind") or "expert",
    }


class DialYielded(Exception):
    """This instance lost a simultaneous dial and stepped aside.

    Not an error: the peer keeps the connection by the agreed instance-id
    rule, and the next auto-reconnect attempt is a no-op because the session
    is already gone. It is raised rather than swallowed so the caller stops
    treating this socket as live.
    """

    def __init__(self, connection_id: str, winner: str) -> None:
        super().__init__(f"dial for {connection_id} yielded to instance {winner}")
        self.connection_id = connection_id
        self.winner = winner


class RefreshRefused(Exception):
    """A token login failed for a *classified* reason.

    The kind matters more than the message: a revoked credential and a peer
    that is merely offline need completely different operator responses.
    """

    def __init__(self, failure: RefreshFailure, detail: str = "") -> None:
        super().__init__(f"{failure}: {detail}" if detail else str(failure))
        self.failure = failure
        self.detail = detail


def _status_of(exc: BaseException) -> int | None:
    """Recover an HTTP status from a peer-auth error, if it carries one.

    ``login_peer`` maps every failure to a bridge error code, so the status is
    recovered from the message; ``None`` means "never reached the peer".
    """
    import re

    match = re.search(r"\b([45]\d{2})\b", str(exc))
    return int(match.group(1)) if match else None


#: Close code the peer uses when it refuses our token.
_WS_CLOSE_UNAUTHENTICATED = 4001

#: Pause before re-authenticating after the peer rejected our token. Short:
#: this path is expected to recover on the next login, not to back off hard.
_AUTH_RETRY_DELAY_SEC = 1.0


def _close_code_of(exc: BaseException) -> int | None:
    """The peer's WebSocket close code, when the exception carries one.

    ``websockets`` attaches the received close frame as ``rcvd``; older shapes
    put the code on the exception itself.
    """
    for source in (getattr(exc, "rcvd", None), exc):
        code = getattr(source, "code", None)
        if isinstance(code, int):
            return code
    return None


def is_auth_rejection(exc: BaseException) -> bool:
    """Whether the peer refused our token on the WebSocket.

    This is a *different* failure from a rejected login: the token looked fine
    by expiry, so the login path is never taken and the bad token would be
    re-presented on every reconnect. Recognising the close code is what lets
    the link recover on its own.
    """
    if _close_code_of(exc) == _WS_CLOSE_UNAUTHENTICATED:
        return True
    low = str(exc).lower()
    return f"{_WS_CLOSE_UNAUTHENTICATED}" in low or "auth:" in low or "missing token" in low


def _peer_fields(hello: Hello | None) -> dict[str, str | None]:
    """Keyword arguments for :meth:`BridgeManager._set_state` from a handshake.

    ``None`` fields mean "keep whatever is stored", so an un-negotiated peer
    does not blank a previously recorded version.
    """
    if hello is None or hello.peer_version is None:
        return {"peer_protocol": None, "peer_instance_id": None}
    return {
        "peer_protocol": str(hello.peer_version),
        "peer_instance_id": hello.peer_instance_id or None,
    }


class BridgeManager:
    def __init__(
        self,
        *,
        bridge_repo: BridgeConnectionRepo,
        secret_repo: SecretRepo,
        user_repo: UserRepo,
        advertise_base_url: str,
        token_signer: Any | None = None,
    ) -> None:
        self._repo = bridge_repo
        self._secrets = secret_repo
        self._users = user_repo
        self._advertise_base_url = advertise_base_url.rstrip("/")
        self._token_signer = token_signer
        self._sessions: dict[str, BridgeSession] = {}
        #: Per-connection handshake outcome, for diagnostics and capability
        #: gating. Absent for a peer that predates negotiation.
        self._negotiated: dict[str, Hello] = {}
        #: Stable id for *this* instance. Two instances dialing the same
        #: connection resolve the conflict by comparing these, never by
        #: arrival order.
        self._instance_id = new_ulid()
        #: Per-connection single-flight token acquisition (plan phase 14).
        self._refreshers: dict[str, TokenRefresher] = {}
        self._client_tasks: dict[str, asyncio.Task[None]] = {}
        self._turn_waiters: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._browser_waiters: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._browser_peer_clients: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}
        self._asgi_app: Any | None = None
        self._peer_turn_runner: PeerTurnRunner | None = None
        self._peer_browser_runner: PeerBrowserRunner | None = None
        self._lock = asyncio.Lock()
        # Manual disconnect / intentional stop — do not auto-reconnect until connect().
        self._user_stopped: set[str] = set()
        self._reconnect_failures: dict[str, int] = {}

    def bind_asgi_app(
        self,
        app: Any,
        *,
        peer_turn_runner: PeerTurnRunner | None = None,
        peer_browser_runner: PeerBrowserRunner | None = None,
    ) -> None:
        """Attach the local ASGI app and optional HTTP-layer peer runners.

        ``peer_turn_runner`` / ``peer_browser_runner`` are provided by
        ``api.app`` so this module never imports ``octop.api``.
        """
        self._asgi_app = app
        self._peer_turn_runner = peer_turn_runner
        self._peer_browser_runner = peer_browser_runner

    def set_token_signer(self, signer: Any) -> None:
        self._token_signer = signer

    def set_advertise_base_url(self, url: str) -> None:
        self._advertise_base_url = url.rstrip("/")

    # -- persistence helpers -------------------------------------------------

    def _set_state(
        self,
        connection_id: str,
        state: BridgeState,
        *,
        detail: str | None = None,
        clear_detail: bool = False,
        peer_protocol: str | None = None,
        peer_instance_id: str | None = None,
    ) -> None:
        """Record a connection state, keeping the legacy column in step.

        Every state write goes through here so ``state`` and ``status`` can
        never drift apart.
        """
        persist_state(
            self._repo,
            connection_id,
            state,
            detail=detail,
            clear_detail=clear_detail,
            peer_protocol=peer_protocol,
            peer_instance_id=peer_instance_id,
        )

    def _touch_seen(self, connection_id: str) -> None:
        """Bump ``last_seen_at`` without disturbing the state machine."""
        row = self._repo.get(connection_id)
        if row is None:
            return
        self._repo.update_status(connection_id, status=row.status, touch_seen=True)

    def list_connections(self, owner_user_id: int) -> list[BridgeConnectionRow]:
        return self._repo.list_for_owner(owner_user_id)

    def get_owned(self, connection_id: str, owner_user_id: int) -> BridgeConnectionRow:
        row = self._repo.get_for_owner(connection_id, owner_user_id)
        if row is None:
            raise OctopError(ErrorCode.BRIDGE_NOT_FOUND, "bridge connection not found")
        return row

    def _allocate_display_name(
        self,
        *,
        owner_user_id: int,
        preferred: str,
        exclude_connection_id: str | None = None,
    ) -> str:
        base = preferred.strip() or "Remote"
        candidate = base
        n = 2
        while True:
            other = self._repo.find_by_display_name(owner_user_id, candidate)
            if other is None or other.connection_id == exclude_connection_id:
                return candidate
            candidate = f"{base} ({n})"
            n += 1

    def connection_public(self, row: BridgeConnectionRow) -> dict[str, Any]:
        live = self._sessions.get(row.connection_id)
        status = row.status
        state = coerce_state(row.state)
        if live is not None and not live.closed:
            status = "connected"
            state = BridgeState.ONLINE
        elif status == "connected":
            status = "disconnected"
            # A row that claims ONLINE while no socket exists is stale; report
            # it as such instead of pretending the link is up.
            if state is BridgeState.ONLINE:
                state = BridgeState.DISCONNECTED
        return {
            "connection_id": row.connection_id,
            "peer_base_url": row.peer_base_url,
            "peer_username": row.peer_username,
            "display_name": row.display_name,
            "notes": row.notes,
            "icon_name": row.icon_name,
            # ``status`` is the legacy two-value field and is unchanged.
            "status": status,
            "state": state.value,
            "state_detail": row.state_detail,
            "retry_may_help": state.retry_may_help,
            "peer_protocol": row.peer_protocol,
            "peer_instance_id": row.peer_instance_id,
            "connected_since": row.connected_since,
            "reconnect_attempts": row.reconnect_attempts,
            "last_error": row.last_error,
            "last_seen_at": row.last_seen_at,
            "auto_reconnect": bool(row.auto_reconnect),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "has_password": bool(row.credential_blob),
            "inbound": _is_inbound(row),
        }

    async def probe_peer(
        self,
        *,
        peer_base_url: str,
        peer_username: str,
        password: str,
        connection_id: str | None = None,
    ) -> dict[str, Any]:
        """Login to peer over HTTP and list experts (no connection row / WS)."""
        base = normalize_peer_base_url(peer_base_url)
        username = peer_username.strip()
        if not username or not password:
            raise OctopError(ErrorCode.BRIDGE_AUTH_FAILED, "username and password required")
        login = await login_peer(base_url=base, username=username, password=password)
        token = str(login["access_token"])
        raw_user = login.get("user")
        user_info: dict[str, Any] = raw_user if isinstance(raw_user, dict) else {}
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(
                    urljoin(base + "/", "api/agents"),
                    params={"scope": "mine"},
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            raise OctopError(
                ErrorCode.BRIDGE_PEER_UNREACHABLE,
                f"peer agents probe failed: {exc}",
            ) from exc
        if resp.status_code >= 400:
            raise OctopError(
                ErrorCode.BRIDGE_AUTH_FAILED,
                f"peer agents HTTP {resp.status_code}: {resp.text[:200]}",
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                "peer agents returned non-JSON",
            ) from exc
        if not isinstance(data, list):
            raise OctopError(ErrorCode.BRIDGE_TUNNEL_FAILED, "peer agents payload invalid")
        agents = [
            _probe_agent_summary(item, peer_base_url=base, connection_id=connection_id)
            for item in data
            if isinstance(item, dict) and str(item.get("agent_id") or item.get("id") or "").strip()
        ]
        return {
            "peer_base_url": base,
            "peer_username": username,
            "peer_display_name": (
                str(user_info.get("display_name") or "").strip()
                or str(user_info.get("username") or username).strip()
            ),
            "agent_count": len(agents),
            "agents": agents,
        }

    def _stored_peer_password(self, row: BridgeConnectionRow) -> str:
        if not row.credential_blob:
            raise OctopError(ErrorCode.BRIDGE_AUTH_FAILED, "password required")
        secret = str(decrypt_payload(self._secrets, row.credential_blob).get("password") or "")
        if not secret:
            raise OctopError(ErrorCode.BRIDGE_AUTH_FAILED, "password required")
        return secret

    async def probe_owned_connection(
        self,
        connection_id: str,
        *,
        owner_user_id: int,
        peer_base_url: str,
        peer_username: str,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Probe using the form endpoint; empty password reuses the stored secret."""
        row = self.get_owned(connection_id, owner_user_id)
        if _is_inbound(row):
            raise OctopError(
                ErrorCode.BRIDGE_INBOUND_PASSIVE,
                "inbound bridge cannot probe peer",
            )
        secret = (password or "").strip() or self._stored_peer_password(row)
        return await self.probe_peer(
            peer_base_url=peer_base_url,
            peer_username=peer_username,
            password=secret,
            connection_id=connection_id,
        )

    # -- create / delete / connect -------------------------------------------

    async def create_connection(
        self,
        *,
        owner_user_id: int,
        peer_base_url: str,
        peer_username: str,
        password: str,
        display_name: str,
        notes: str | None = None,
        icon_name: str | None = None,
        connect: bool = True,
    ) -> BridgeConnectionRow:
        """Create a bridge link.

        With ``connect=True`` (Dashboard default), the row is only kept when the
        peer login **and** Bridge WS handshake both succeed. A failed dial rolls
        back the insert so a retry can reuse the same display name.
        """
        base = normalize_peer_base_url(peer_base_url)
        username = peer_username.strip()
        name = display_name.strip()
        note = (notes or "").strip() or None
        icon = (icon_name or "").strip() or None
        if not username or not password:
            raise OctopError(ErrorCode.BRIDGE_AUTH_FAILED, "username and password required")
        if not name:
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "display_name required",
                status=400,
                details={"field": "display_name"},
            )
        if self._repo.find_by_display_name(owner_user_id, name) is not None:
            raise OctopError(
                ErrorCode.BRIDGE_DISPLAY_NAME_TAKEN,
                f"display name {name!r} already in use",
                details={"name": name},
            )

        # Login first — no DB row yet on auth / unreachable peer.
        login = await login_peer(base_url=base, username=username, password=password)
        token = str(login["access_token"])
        expires_in = int(login.get("expires_in") or 0)
        from octop.infra.db.repos._base import now_ts

        expires_at = now_ts() + expires_in if expires_in > 0 else None
        connection_id = self._allocate_connection_id()
        cred_blob = encrypt_payload(self._secrets, {"password": password})
        token_blob = encrypt_payload(self._secrets, {"access_token": token})
        row = self._repo.create(
            connection_id=connection_id,
            owner_user_id=owner_user_id,
            peer_base_url=base,
            peer_username=username,
            display_name=name,
            notes=note,
            icon_name=icon,
            credential_blob=cred_blob,
            access_token_blob=token_blob,
            token_expires_at=expires_at,
            status="connecting" if connect else "disconnected",
        )
        if not connect:
            return self._repo.get(connection_id) or row
        try:
            return await self.connect(connection_id, owner_user_id=owner_user_id)
        except Exception:
            # Roll back so a failed "Save & connect" does not occupy display_name
            # or leave a dead card in the list.
            with suppress(Exception):
                await self.disconnect(connection_id)
            self._repo.delete(connection_id)
            raise

    def _allocate_connection_id(self) -> str:
        for _ in range(16):
            cid = new_short_id()
            if self._repo.get(cid) is None:
                return cid
        raise RuntimeError("failed to allocate unique bridge connection_id")

    async def delete_connection(self, connection_id: str, *, owner_user_id: int) -> None:
        self.get_owned(connection_id, owner_user_id)
        sess = self._sessions.get(connection_id)
        if sess is not None and not sess.closed:
            with suppress(Exception):
                await sess.send_json({"type": "close", "reason": "deleted"})
                await asyncio.sleep(0.1)
        await self.disconnect(connection_id)
        self._repo.delete(connection_id)

    async def connect(self, connection_id: str, *, owner_user_id: int) -> BridgeConnectionRow:
        row = self.get_owned(connection_id, owner_user_id)
        if _is_inbound(row):
            raise OctopError(
                ErrorCode.BRIDGE_INBOUND_PASSIVE,
                "inbound bridge cannot dial out",
            )
        self._user_stopped.discard(connection_id)
        async with self._lock:
            existing = self._sessions.get(connection_id)
            if existing is not None and not existing.closed:
                return row
            task = self._client_tasks.get(connection_id)
            if task is not None and not task.done():
                # Supervisor already running (reconnect wait) — wait for live session.
                pass
            else:
                self._set_state(connection_id, BridgeState.CONNECTING, clear_detail=True)
                self._reconnect_failures[connection_id] = 0
                task = asyncio.create_task(
                    self._outbound_supervisor(connection_id),
                    name=f"bridge-out-{connection_id}",
                )
                self._client_tasks[connection_id] = task
        assert task is not None
        # Wait briefly for hello_ack / live session
        for _ in range(50):
            sess = self._sessions.get(connection_id)
            if sess is not None and not sess.closed:
                self._set_state(
                    connection_id,
                    BridgeState.ONLINE,
                    clear_detail=True,
                    **_peer_fields(self._negotiated.get(connection_id)),
                )
                self._touch_seen(connection_id)
                self._reconnect_failures[connection_id] = 0
                return self._repo.get(connection_id) or row
            if task.done():
                exc = task.exception() if not task.cancelled() else None
                msg = str(exc) if exc else "bridge connect failed"
                latest = self._repo.get(connection_id)
                if latest is not None and latest.status != "error":
                    self._set_state(connection_id, BridgeState.DEGRADED, detail=msg[:500])
                raise OctopError(ErrorCode.BRIDGE_PEER_UNREACHABLE, msg)
            await asyncio.sleep(0.1)
        self._set_state(connection_id, BridgeState.DEGRADED, detail="bridge connect timeout")
        raise OctopError(ErrorCode.BRIDGE_PEER_UNREACHABLE, "bridge connect timeout")

    async def disconnect(self, connection_id: str) -> None:
        self._user_stopped.add(connection_id)
        task = self._client_tasks.pop(connection_id, None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        sess = self._sessions.pop(connection_id, None)
        if sess is not None:
            await sess.close()
        # Forget any cached peer token: a disconnect is a chance for the
        # credential to have been revoked on the other side, and a token that
        # survives here is one reconnect will happily reuse.
        self._forget_token(connection_id)
        if self._repo.get(connection_id) is not None:
            self._set_state(connection_id, BridgeState.DISCONNECTED, clear_detail=True)

    def _forget_token(self, connection_id: str) -> None:
        """Drop the in-memory token and any in-flight login for a connection."""
        refresher = self._refreshers.pop(connection_id, None)
        if refresher is not None:
            refresher.drop(connection_id)

    def diagnostics_summary(self, owner_user_id: int) -> dict[str, Any]:
        """Everything the "copy diagnostics" button needs, and nothing more.

        Counters are read from ``METRICS`` (this process) rather than re-derived
        from the database, so the summary describes the instance actually
        serving requests.
        """
        from octop.infra.bridge.diagnostics import (
            connection_diagnostic,
            summarize,
        )
        from octop.infra.bridge.protocol import LOCAL_CAPABILITIES
        from octop.infra.db.repos._base import now_ts

        rows = self._repo.list_for_owner(owner_user_id)
        diags = [
            connection_diagnostic(
                row, negotiated=self._negotiated.get(row.connection_id), now=now_ts()
            )
            for row in rows
        ]
        return summarize(
            diags,
            local_instance_id=self._instance_id,
            local_capabilities=LOCAL_CAPABILITIES,
        )

    async def revoke_credentials(self, connection_id: str, *, owner_user_id: int) -> None:
        """Invalidate stored credentials and tear the link down immediately.

        Called when the owner explicitly signs out of a peer. A password change
        that leaves the old token cached would keep a socket alive with a
        credential the user believes they revoked, and a revoked credential left
        in the table is a credential left on disk.

        Revocation clears the stored blobs as well as in-memory state — the row
        survives so the connection can be re-pointed at a new peer.
        """
        self.get_owned(connection_id, owner_user_id)
        await self.disconnect(connection_id)
        self._user_stopped.discard(connection_id)
        self._forget_token(connection_id)
        self._repo.clear_credentials(connection_id)
        self._set_state(
            connection_id,
            BridgeState.REAUTH_REQUIRED,
            detail="credentials revoked by owner",
            clear_detail=True,
        )

    async def set_auto_reconnect(
        self, connection_id: str, *, owner_user_id: int, enabled: bool
    ) -> BridgeConnectionRow:
        row = self.get_owned(connection_id, owner_user_id)
        if enabled and _is_inbound(row):
            raise OctopError(
                ErrorCode.BRIDGE_INBOUND_PASSIVE,
                "inbound bridge cannot auto-reconnect",
            )
        self._repo.set_auto_reconnect(connection_id, enabled)
        if enabled:
            self._user_stopped.discard(connection_id)
            self._reconnect_failures[connection_id] = 0
            live = self._sessions.get(connection_id)
            if live is None or live.closed:
                return await self.connect(connection_id, owner_user_id=owner_user_id)
        return self._repo.get(connection_id) or row

    async def update_connection_meta(
        self,
        connection_id: str,
        *,
        owner_user_id: int,
        display_name: str | None = None,
        notes: str | None = None,
        update_notes: bool = False,
        icon_name: str | None = None,
        update_icon: bool = False,
        peer_base_url: str | None = None,
        peer_username: str | None = None,
        password: str | None = None,
    ) -> BridgeConnectionRow:
        """Update display / notes / icon and optionally remote endpoint credentials.

        When URL, username, or password changes, re-login against the peer and
        bounce the live Bridge session if it was connected.
        """
        row = self.get_owned(connection_id, owner_user_id)
        if _is_inbound(row):
            url_touch = peer_base_url is not None and peer_base_url.strip().rstrip(
                "/"
            ) != row.peer_base_url.rstrip("/")
            user_touch = peer_username is not None and peer_username.strip() != row.peer_username
            pwd_touch = bool((password or "").strip())
            if url_touch or user_touch or pwd_touch:
                raise OctopError(
                    ErrorCode.BRIDGE_INBOUND_PASSIVE,
                    "inbound bridge cannot change peer credentials",
                )
            peer_base_url = None
            peer_username = None
            password = None
        name = row.display_name
        if display_name is not None:
            name = display_name.strip()
            if not name:
                raise OctopError(
                    ErrorCode.FORBIDDEN,
                    "display_name required",
                    status=400,
                    details={"field": "display_name"},
                )
            other = self._repo.find_by_display_name(owner_user_id, name)
            if other is not None and other.connection_id != connection_id:
                raise OctopError(
                    ErrorCode.BRIDGE_DISPLAY_NAME_TAKEN,
                    f"display name {name!r} already in use",
                    details={"name": name},
                )
        note = row.notes
        if update_notes:
            note = (notes or "").strip() or None
        icon = row.icon_name
        if update_icon:
            icon = (icon_name or "").strip() or None

        next_user = peer_username.strip() if peer_username is not None else row.peer_username
        if not next_user:
            raise OctopError(ErrorCode.BRIDGE_AUTH_FAILED, "username required")

        pwd_raw = (password or "").strip()
        url_changed = False
        next_base = row.peer_base_url
        if peer_base_url is not None:
            stripped = peer_base_url.strip().rstrip("/")
            stored = row.peer_base_url.rstrip("/")
            if stripped != stored:
                url_changed = True

        user_changed = next_user != row.peer_username
        password_changed = bool(pwd_raw)
        need_reauth = url_changed or user_changed or password_changed

        if not need_reauth:
            updated = self._repo.update_settings(
                connection_id,
                display_name=name,
                notes=note,
                icon_name=icon,
            )
            if updated is None:
                raise OctopError(ErrorCode.BRIDGE_NOT_FOUND, "bridge connection not found")
            return updated

        if password_changed:
            secret = pwd_raw
        else:
            if not row.credential_blob:
                raise OctopError(ErrorCode.BRIDGE_AUTH_FAILED, "password required")
            secret = str(decrypt_payload(self._secrets, row.credential_blob).get("password") or "")
            if not secret:
                raise OctopError(ErrorCode.BRIDGE_AUTH_FAILED, "password required")

        if url_changed:
            assert peer_base_url is not None
            next_base = normalize_peer_base_url(peer_base_url)

        login = await login_peer(base_url=next_base, username=next_user, password=secret)
        token = str(login["access_token"])
        expires_in = int(login.get("expires_in") or 0)
        from octop.infra.db.repos._base import now_ts

        expires_at = now_ts() + expires_in if expires_in > 0 else None
        live = self._sessions.get(connection_id)
        was_live = live is not None and not live.closed

        updated = self._repo.update_settings(
            connection_id,
            display_name=name,
            notes=note,
            icon_name=icon,
            peer_base_url=next_base,
            peer_username=next_user,
            credential_blob=encrypt_payload(self._secrets, {"password": secret}),
            access_token_blob=encrypt_payload(self._secrets, {"access_token": token}),
            token_expires_at=expires_at,
            update_credentials=True,
        )
        if updated is None:
            raise OctopError(ErrorCode.BRIDGE_NOT_FOUND, "bridge connection not found")

        await self.disconnect(connection_id)
        self._user_stopped.discard(connection_id)
        if was_live or updated.auto_reconnect:
            try:
                return await self.connect(connection_id, owner_user_id=owner_user_id)
            except OctopError:
                return self._repo.get(connection_id) or updated
        return self._repo.get(connection_id) or updated

    async def resume_auto_connections(self) -> None:
        """Boot-time: dial every connection with auto_reconnect enabled."""
        for row in self._repo.list_auto_reconnect():
            if _is_inbound(row):
                continue
            if row.connection_id in self._user_stopped:
                continue
            live = self._sessions.get(row.connection_id)
            if live is not None and not live.closed:
                continue
            try:
                await self.connect(row.connection_id, owner_user_id=row.owner_user_id)
            except Exception as exc:
                logger.warning(
                    "bridge auto-resume failed connection=%s: %s",
                    row.connection_id,
                    exc,
                )

    async def _outbound_supervisor(self, connection_id: str) -> None:
        """Keep an outbound Bridge WS alive while auto_reconnect is on."""
        while True:
            if connection_id in self._user_stopped:
                return
            row = self._repo.get(connection_id)
            if row is None:
                return
            try:
                token = await self._ensure_peer_token(row)
                self._set_state(connection_id, BridgeState.CONNECTING, clear_detail=True)
                await self._run_outbound_client(connection_id, row.peer_base_url, token)
                # Clean close (peer hung up) — reset failure streak.
                self._reconnect_failures[connection_id] = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if is_auth_rejection(exc):
                    # The peer refused the token we already had. Drop it so the
                    # next iteration logs in again; keep the stored password,
                    # which may still be perfectly good.
                    self._forget_token(connection_id)
                    self._repo.clear_credentials(connection_id, keep_password=True)
                    detail = f"peer rejected the stored token: {str(exc)[:200]}"
                    self._set_state(
                        connection_id, BridgeState.DEGRADED, detail=detail, clear_detail=True
                    )
                    self._reconnect_failures[connection_id] = 0
                    await asyncio.sleep(_AUTH_RETRY_DELAY_SEC)
                    continue
                raw_detail = str(exc)
                raw_low = raw_detail.lower()
                if "incompatible" in raw_low or "matching major version" in raw_low:
                    # Major-version mismatch: only an upgrade on one side
                    # clears it. Persist the steady INCOMPATIBLE state and
                    # stop the auto-reconnect cycle so the operator sees a
                    # single, clear reason instead of a flood of identical
                    # failures. ``_run_outbound_client`` already wrote the
                    # state, but doing it here too makes the rule hold even
                    # if the failure reaches the supervisor through some
                    # other path in the future. The "matching major version"
                    # arm catches the wire-format close(4002) reason the
                    # peer sends after ``ProtocolIncompatible`` is raised
                    # on its side, where the substring only carries the
                    # second half of our message.
                    if self._repo.get(connection_id) is not None:
                        self._set_state(
                            connection_id,
                            BridgeState.INCOMPATIBLE,
                            detail=raw_detail[:500],
                            clear_detail=False,
                        )
                    self._user_stopped.add(connection_id)
                    return
                detail = raw_detail
                low = detail.lower()
                if "403" in detail or "404" in detail or "rejected websocket" in low:
                    detail = (
                        f"{detail}; peer may not support Bridge yet "
                        "(needs Octop with /api/bridge/ws)"
                    )
                failures = self._reconnect_failures.get(connection_id, 0) + 1
                self._reconnect_failures[connection_id] = failures
                if self._repo.get(connection_id) is not None:
                    self._set_state(connection_id, BridgeState.DISCONNECTED, detail=detail[:500])
                row = self._repo.get(connection_id)
                if row is None or connection_id in self._user_stopped:
                    return
                if not row.auto_reconnect:
                    return
                if failures >= _AUTO_RECONNECT_MAX_FAILURES:
                    msg = (
                        f"Auto-reconnect disabled after {failures} failed attempts. "
                        f"Last error: {detail[:300]}"
                    )
                    self._repo.set_auto_reconnect(connection_id, False)
                    self._set_state(connection_id, BridgeState.DEGRADED, detail=msg[:500])
                    self._user_stopped.add(connection_id)
                    logger.warning(
                        "bridge auto-reconnect disabled connection=%s after %s failures",
                        connection_id,
                        failures,
                    )
                    return
                delay = _AUTO_RECONNECT_BACKOFF_SEC[
                    min(failures - 1, len(_AUTO_RECONNECT_BACKOFF_SEC) - 1)
                ]
                logger.info(
                    "bridge reconnecting connection=%s in %.0fs (attempt %s)",
                    connection_id,
                    delay,
                    failures,
                )
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise
                continue

            # Session ended without exception — reconnect if still enabled.
            if connection_id in self._user_stopped:
                return
            row = self._repo.get(connection_id)
            if row is None or not row.auto_reconnect:
                if self._repo.get(connection_id) is not None:
                    self._set_state(connection_id, BridgeState.DISCONNECTED, clear_detail=True)
                return
            delay = _AUTO_RECONNECT_BACKOFF_SEC[0]
            logger.info(
                "bridge session ended; reconnecting connection=%s in %.0fs",
                connection_id,
                delay,
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    def _refresher_for(self, row: BridgeConnectionRow) -> TokenRefresher:
        """One refresher per connection, created on first use.

        Keyed by connection rather than shared globally: two healthy links to
        the same peer must not serialise behind a single login.
        """
        existing = self._refreshers.get(row.connection_id)
        if existing is not None:
            return existing
        created = TokenRefresher(lambda: self._login_peer(row))
        self._refreshers[row.connection_id] = created
        return created

    async def _login_peer(self, row: BridgeConnectionRow) -> str:
        """POST /api/auth/login on the peer and commit the token via CAS.

        The login round trip is slow and happens *outside* any lock, so the
        version read on ``row`` may be stale by the time we write. Committing
        only if it is unchanged is what stops a slow writer from clobbering a
        token a faster writer already installed.
        """
        from octop.infra.db.repos._base import now_ts

        if not row.credential_blob:
            raise RefreshRefused(RefreshFailure.NO_CREDENTIAL, "no peer credentials stored")
        creds = decrypt_payload(self._secrets, row.credential_blob)
        password = str(creds.get("password") or "")
        try:
            login = await login_peer(
                base_url=row.peer_base_url,
                username=row.peer_username,
                password=password,
            )
        except OctopError as exc:
            raise RefreshRefused(
                classify_login_failure(_status_of(exc), detail=str(exc)),
                "peer login failed",
            ) from exc
        token = str(login["access_token"])
        expires_in = int(login.get("expires_in") or 0)
        expires_at = now_ts() + expires_in if expires_in > 0 else None
        committed = self._repo.update_credentials_if_current(
            row.connection_id,
            expected_token_version=row.token_version,
            access_token_blob=encrypt_payload(self._secrets, {"access_token": token}),
            token_expires_at=expires_at,
        )
        if not committed:
            # Someone else wrote first. Their value is the newer one, so keep
            # it rather than overwriting a token that may be fresher than ours.
            logger.info(
                "bridge %s: skipped a stale token write (version moved during login)",
                row.connection_id,
            )
        return token

    async def _ensure_peer_token(self, row: BridgeConnectionRow) -> str:
        """Return a usable peer token, refreshing only when it is close to expiry.

        Concurrent callers for one connection share a single login
        (``TokenRefresher``), so a peer restart cannot turn into a login storm.
        """
        from octop.infra.db.repos._base import now_ts

        refresher = self._refresher_for(row)
        if row.access_token_blob:
            data = decrypt_payload(self._secrets, row.access_token_blob)
            token = str(data.get("access_token") or "").strip()
            if token and not should_refresh(expires_at=row.token_expires_at, now=now_ts()):
                refresher.seed(row.connection_id, token)
                return token
        try:
            return await refresher.acquire(row.connection_id)
        except RefreshRefused as exc:
            revoked = exc.failure in (
                RefreshFailure.CREDENTIAL_REVOKED,
                RefreshFailure.NO_CREDENTIAL,
            )
            if revoked:
                # The cached token is what the peer just rejected. Keeping it
                # would re-present the same bad token on every reconnect, so
                # the link could never recover on its own.
                refresher.drop(row.connection_id)
                self._repo.clear_credentials(row.connection_id)
            # Persist the kind so the UI can say "sign in again" instead of
            # "check the logs" for a credential that will never work.
            self._set_state(
                row.connection_id,
                BridgeState.REAUTH_REQUIRED if revoked else BridgeState.DEGRADED,
                detail=exc.detail[:500],
            )
            raise OctopError(
                ErrorCode.BRIDGE_AUTH_FAILED,
                f"peer authentication failed ({exc.failure})",
            ) from exc

    async def _run_outbound_client(
        self, connection_id: str, peer_base_url: str, token: str
    ) -> None:
        url = peer_ws_url(peer_base_url, token=token)
        session: BridgeSession | None = None
        try:
            async with websockets.connect(url, open_timeout=20, max_size=32 * 1024 * 1024) as ws:
                owner = self._repo.get(connection_id)
                local_user = self._users.get(owner.owner_user_id) if owner is not None else None
                hello = build_hello(
                    connection_id=connection_id,
                    role="initiator",
                    base_url=self._advertise_base_url or "http://127.0.0.1",
                    username=(local_user.username if local_user is not None else ""),
                    display_name=owner.display_name if owner else "",
                    instance_id=self._instance_id,
                    octop_version=_local_version(),
                )
                await ws.send(json.dumps(hello, ensure_ascii=False))

                # Wait for hello_ack before advertising the session as connected.
                while True:
                    raw_ack = await asyncio.wait_for(ws.recv(), timeout=20.0)
                    text_ack = (
                        raw_ack
                        if isinstance(raw_ack, str)
                        else raw_ack.decode("utf-8", errors="replace")
                    )
                    try:
                        ack = json.loads(text_ack)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(ack, dict):
                        continue
                    if str(ack.get("type") or "") != "hello_ack":
                        continue
                    try:
                        negotiated = negotiate(ack)
                    except ProtocolIncompatible as exc:
                        # Major version mismatch. Persist INCOMPATIBLE *before*
                        # the supervisor catches and retries: the supervisor
                        # otherwise lumps every failure into a transient
                        # DISCONNECTED, and a permanent incompatibility would
                        # then look identical to a flaky network.
                        msg = f"bridge protocol incompatible: {exc}"
                        if self._repo.get(connection_id) is not None:
                            self._set_state(
                                connection_id,
                                BridgeState.INCOMPATIBLE,
                                detail=msg[:500],
                                clear_detail=False,
                            )
                        raise OctopError(
                            ErrorCode.BRIDGE_PEER_UNREACHABLE,
                            msg,
                        ) from exc
                    self._negotiated[connection_id] = negotiated
                    break

                session = BridgeSession(
                    connection_id=connection_id,
                    send_text=ws.send,
                    on_tunnel_request=lambda p: self._handle_inbound_tunnel(connection_id, p),
                    on_turn_frame=lambda p: self._handle_inbound_turn(connection_id, p),
                    on_browser_frame=lambda p: self._handle_inbound_browser(connection_id, p),
                )
                await self._register_session(
                    connection_id,
                    session,
                    peer_instance_id=negotiated.peer_instance_id if negotiated else "",
                )
                if self._repo.get(connection_id) is not None:
                    self._set_state(
                        connection_id,
                        BridgeState.ONLINE,
                        clear_detail=True,
                        **_peer_fields(self._negotiated.get(connection_id)),
                    )
                    self._touch_seen(connection_id)
                    self._reconnect_failures[connection_id] = 0
                async for raw in ws:
                    text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
                    await session.handle_message(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bridge outbound closed connection=%s: %s", connection_id, exc)
            raise
        finally:
            await self._unregister_session(connection_id)

    # -- inbound WS (peer dials us) ------------------------------------------

    async def accept_inbound(
        self,
        *,
        websocket: Any,
        user: Any,
    ) -> None:
        """Handle an inbound ``/api/bridge/ws`` after JWT auth + accept()."""
        connection_id: str | None = None
        session: BridgeSession | None = None

        async def send_text(text: str) -> None:
            await websocket.send_text(text)

        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            payload = json.loads(raw)
        except Exception:
            await websocket.close(code=4000, reason="hello required")
            return
        if not isinstance(payload, dict) or str(payload.get("type") or "") != "hello":
            await websocket.close(code=4000, reason="hello required")
            return
        connection_id = str(payload.get("connection_id") or "").strip()
        if not connection_id:
            await websocket.close(code=4000, reason="connection_id required")
            return
        try:
            negotiated = negotiate(payload)
        except ProtocolIncompatible as exc:
            # A major mismatch is a real incompatibility, not a transient
            # failure — say so instead of a bare "protocol mismatch".
            await websocket.close(code=4002, reason=str(exc)[:120])
            return
        self._negotiated[connection_id] = negotiated
        advertise_base = str(payload.get("advertise_base_url") or "").strip() or "http://127.0.0.1"
        advertise_user = str(payload.get("advertise_username") or "").strip() or "peer"
        preferred_name = str(payload.get("display_name") or "").strip()
        try:
            advertise_base = normalize_peer_base_url(advertise_base)
        except OctopError:
            advertise_base = "http://127.0.0.1"

        existing = self._repo.get(connection_id)
        if existing is not None and int(existing.owner_user_id) != int(user.id):
            await websocket.close(code=4003, reason="connection owned by another user")
            return
        preferred = inbound_default_display_name(
            connection_id=connection_id,
            preferred_name=preferred_name,
            peer_username=advertise_user,
            existing_name=existing.display_name if existing is not None else None,
        )
        display_name = self._allocate_display_name(
            owner_user_id=int(user.id),
            preferred=preferred,
            exclude_connection_id=connection_id if existing is not None else None,
        )

        self._repo.upsert_reverse(
            connection_id=connection_id,
            owner_user_id=int(user.id),
            peer_base_url=advertise_base,
            peer_username=advertise_user,
            display_name=display_name,
            status="connected",
        )

        session = BridgeSession(
            connection_id=connection_id,
            send_text=send_text,
            on_tunnel_request=lambda p: self._handle_inbound_tunnel(connection_id, p),
            on_turn_frame=lambda p: self._handle_inbound_turn(connection_id, p),
            on_browser_frame=lambda p: self._handle_inbound_browser(connection_id, p),
        )
        await self._register_session(
            connection_id,
            session,
            peer_instance_id=negotiated.peer_instance_id if negotiated else "",
        )
        await session.send_json(
            {
                "type": "hello_ack",
                # Reply with the same negotiation fields the peer sent us, so
                # both ends learn the other's capabilities in one round trip.
                "protocol_version": LOCAL_VERSION_STRING,
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": PROTOCOL_MINOR,
                "capabilities": sorted(c.value for c in LOCAL_CAPABILITIES),
                "max_frame_bytes": MAX_FRAME_BYTES,
                "instance_id": self._instance_id,
                "octop_version": _local_version(),
                "connection_id": connection_id,
                "peer_username": user.username,
            }
        )
        try:
            while True:
                message = await websocket.receive_text()
                await session.handle_message(message)
                if session.closed:
                    break
        except Exception:
            logger.info("bridge inbound closed connection=%s", connection_id)
        finally:
            if connection_id:
                await self._finalize_inbound(connection_id, session)

    async def _register_session(
        self,
        connection_id: str,
        session: BridgeSession,
        *,
        peer_instance_id: str = "",
    ) -> None:
        """Register a session, resolving a simultaneous dial deterministically.

        When both instances dial each other at once, two sockets exist for one
        connection. The previous rule was last-writer-wins, so whichever side's
        packet landed last kept the link — meaning the surviving connection
        depended on timing, and a single crossing packet could flip it.

        Now the winner is decided by instance id, which both sides can compute
        independently and identically. The loser closes its own socket.
        """
        old = self._sessions.get(connection_id)
        if old is not None and old is not session:
            verdict = arbitrate_dial(
                incumbent=self._instance_id,
                challenger=peer_instance_id or "",
            )
            if verdict.loser == self._instance_id:
                # We lost: drop the newcomer and close ours too.
                self._sessions.pop(connection_id, None)
                await old.close()
                await session.close()
                logger.info(
                    "bridge %s: yielding to peer instance %s (simultaneous dial)",
                    connection_id,
                    peer_instance_id,
                )
                raise DialYielded(connection_id, verdict.winner)
            await old.close()
        self._sessions[connection_id] = session

    async def _unregister_session(self, connection_id: str) -> None:
        sess = self._sessions.pop(connection_id, None)
        if sess is not None:
            await sess.close()

    async def _finalize_inbound(self, connection_id: str, session: BridgeSession | None) -> None:
        await self._unregister_session(connection_id)
        if session is not None and session.close_reason == "deleted":
            self._repo.delete(connection_id)
            return
        if self._repo.get(connection_id) is not None:
            self._set_state(connection_id, BridgeState.DISCONNECTED, clear_detail=True)

    # -- tunnel --------------------------------------------------------------

    def require_session(self, connection_id: str) -> BridgeSession:
        sess = self._sessions.get(connection_id)
        if sess is None or sess.closed:
            raise OctopError(ErrorCode.BRIDGE_NOT_CONNECTED, "bridge not connected")
        return sess

    async def tunnel_http(
        self,
        *,
        connection_id: str,
        owner_user_id: int,
        method: str,
        path: str,
        query: str = "",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> httpx.Response:
        from octop.infra.bridge.tunnel_policy import is_tunnel_path_allowed

        self.get_owned(connection_id, owner_user_id)
        # Fail fast with a user-facing "unsupported on remote" code before the hop.
        if not is_tunnel_path_allowed(method, path):
            raise OctopError(
                ErrorCode.BRIDGE_REMOTE_UNSUPPORTED,
                "This action is not available through the remote bridge. Manage it on the peer Octop.",
            )
        sess = self.require_session(connection_id)
        from octop.infra.db.repos._base import now_ts

        audit = new_audit(
            owner_user_id=owner_user_id,
            now=now_ts(),
            hub_instance_id=self._instance_id,
        )
        from octop.infra.metrics import METRICS

        METRICS.inc("bridge_tunnel_requests_total")
        METRICS.inc("bridge_tunnel_in_flight")
        sent_at = now_ts()
        # Direction and correlation ids go in the log line, never the query or
        # the body: a bridge log is something an operator will paste elsewhere.
        logger.info(
            "bridge tunnel -> %s %s %s",
            audit.request_id,
            method.upper(),
            path,
        )
        try:
            result = await sess.tunnel_request(
                method=method,
                path=path,
                query=query,
                headers=headers,
                body=body,
                timeout=float(audit.deadline_at - now_ts()),
                audit_fields={"audit": audit},
            )
        except TimeoutError:
            METRICS.inc("bridge_tunnel_timeouts_total")
            raise
        except Exception:
            METRICS.inc("bridge_tunnel_errors_total")
            raise
        finally:
            METRICS.inc("bridge_tunnel_in_flight", -1)
        METRICS.inc("bridge_tunnel_bytes_total", len(str(result.get("body_b64") or "")) * 3 // 4)
        logger.info(
            "bridge tunnel <- %s status=%s in %sms",
            audit.request_id,
            result.get("status"),
            int((now_ts() - sent_at) * 1000),
        )
        if str(result.get("type") or "") == "tunnel.error":
            code_raw = str(result.get("code") or "").strip()
            if code_raw == ErrorCode.BRIDGE_REMOTE_UNSUPPORTED.value:
                raise OctopError(
                    ErrorCode.BRIDGE_REMOTE_UNSUPPORTED,
                    "This action is not available through the remote bridge. Manage it on the peer Octop.",
                )
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                str(result.get("message") or "tunnel error"),
            )
        status = int(result.get("status") or 502)
        hdrs = result.get("headers") if isinstance(result.get("headers"), dict) else {}
        content = decode_body_b64(result)
        return httpx.Response(status, headers=hdrs, content=content)

    async def _owner_access_token(self, connection_id: str) -> str:
        row = self._repo.get(connection_id)
        if row is None:
            raise OctopError(ErrorCode.BRIDGE_NOT_FOUND, "bridge connection not found")
        if self._token_signer is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "bridge token signer not configured")
        user = self._users.get(row.owner_user_id)
        if user is None:
            raise OctopError(ErrorCode.BRIDGE_NOT_FOUND, "bridge owner missing")
        return str(self._token_signer(user))

    async def _handle_inbound_tunnel(
        self, connection_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if self._asgi_app is None:
            raise RuntimeError("ASGI app not bound")
        token = await self._owner_access_token(connection_id)
        method = str(payload.get("method") or "GET")
        path = str(payload.get("path") or "/")
        query = str(payload.get("query") or "")
        headers_raw = payload.get("headers")
        headers: dict[str, Any] = headers_raw if isinstance(headers_raw, dict) else {}
        body = decode_body_b64(payload)
        # Refuse a request that arrives without an accountable actor: an
        # unattributed write on this instance would be unauditable.
        audit = audit_from_frame(payload)
        logger.info("bridge tunnel %s", audit.describe())
        return await execute_local_http(
            app=self._asgi_app,
            method=method,
            path=path,
            query=query,
            headers={str(k): str(v) for k, v in headers.items()},
            body=body,
            access_token=token,
            audit=audit,
        )

    # -- agents via tunnel ---------------------------------------------------

    async def list_remote_agents(
        self, connection_id: str, *, owner_user_id: int
    ) -> list[dict[str, Any]]:
        row = self.get_owned(connection_id, owner_user_id)
        resp = await self.tunnel_http(
            connection_id=connection_id,
            owner_user_id=owner_user_id,
            method="GET",
            path="/api/agents",
            query="scope=mine",
        )
        if resp.status_code >= 400:
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                f"peer agents HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        if not isinstance(data, list):
            raise OctopError(ErrorCode.BRIDGE_TUNNEL_FAILED, "peer agents payload invalid")
        out: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            # Prefer public agent_id; integer ``id`` is the DB surrogate PK.
            remote_id = str(item.get("agent_id") or item.get("id") or "").strip()
            if not remote_id:
                continue
            mapped = dict(item)
            mapped["id"] = format_bridge_agent_id(connection_id, remote_id)
            mapped["agent_id"] = mapped["id"]
            mapped["bridge_connection_id"] = connection_id
            mapped["bridge_connection_name"] = row.display_name
            mapped["remote_agent_id"] = remote_id
            mapped["bridge"] = True
            mapped["bridge_inbound"] = _is_inbound(row)
            mapped["is_owner"] = True
            # Shadow experts are chat-ready while the bridge link is live.
            mapped["state"] = "running"
            # Keep bundled /experts/avatars and CDN URLs; proxy uploaded avatars.
            mapped["icon_url"] = rewrite_remote_icon_url(
                str(mapped.get("icon_url") or mapped.get("icon") or "").strip() or None,
                remote_agent_id=remote_id,
                bridge_agent_id=str(mapped["id"]),
            )
            members = mapped.get("member_ids")
            if isinstance(members, list):
                mapped["member_ids"] = rewrite_peer_agent_ids(connection_id, members)
            out.append(mapped)
        return out

    async def tunnel_json_get(
        self,
        connection_id: str,
        *,
        owner_user_id: int,
        path: str,
        query: str = "",
    ) -> Any:
        """GET a JSON body from the peer via the HTTP tunnel."""
        resp = await self.tunnel_http(
            connection_id=connection_id,
            owner_user_id=owner_user_id,
            method="GET",
            path=path,
            query=query,
        )
        if resp.status_code >= 400:
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                f"peer {path} HTTP {resp.status_code}: {resp.text[:200]}",
            )
        try:
            return resp.json()
        except Exception as exc:
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                f"peer {path} returned invalid JSON",
            ) from exc

    async def list_remote_resolved_models(
        self, connection_id: str, *, owner_user_id: int
    ) -> list[dict[str, Any]]:
        data = await self.tunnel_json_get(
            connection_id,
            owner_user_id=owner_user_id,
            path="/api/providers/resolved",
        )
        if not isinstance(data, list):
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                "peer providers/resolved payload invalid",
            )
        return [item for item in data if isinstance(item, dict)]

    async def get_remote_active_model(
        self, connection_id: str, *, owner_user_id: int
    ) -> dict[str, Any]:
        data = await self.tunnel_json_get(
            connection_id,
            owner_user_id=owner_user_id,
            path="/api/providers/active-model",
        )
        if not isinstance(data, dict):
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                "peer providers/active-model payload invalid",
            )
        return data

    async def list_remote_knowledge_bases(
        self, connection_id: str, *, owner_user_id: int
    ) -> list[dict[str, Any]]:
        data = await self.tunnel_json_get(
            connection_id,
            owner_user_id=owner_user_id,
            path="/api/knowledge-bases",
        )
        if not isinstance(data, list):
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                "peer knowledge-bases payload invalid",
            )
        return [item for item in data if isinstance(item, dict)]

    async def get_remote_knowledge_capability(
        self, connection_id: str, *, owner_user_id: int
    ) -> dict[str, Any]:
        data = await self.tunnel_json_get(
            connection_id,
            owner_user_id=owner_user_id,
            path="/api/knowledge-bases/capability",
        )
        if not isinstance(data, dict):
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                "peer knowledge-bases/capability payload invalid",
            )
        return data

    async def get_remote_browser_env_status(
        self, connection_id: str, *, owner_user_id: int
    ) -> dict[str, Any]:
        data = await self.tunnel_json_get(
            connection_id,
            owner_user_id=owner_user_id,
            path="/api/browser/env-status",
        )
        if not isinstance(data, dict):
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                "peer browser/env-status payload invalid",
            )
        return data

    async def get_remote_browser_sessions(
        self, connection_id: str, *, owner_user_id: int
    ) -> dict[str, Any]:
        data = await self.tunnel_json_get(
            connection_id,
            owner_user_id=owner_user_id,
            path="/api/browser/harness-sessions",
        )
        if not isinstance(data, dict):
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                "peer browser/harness-sessions payload invalid",
            )
        return data

    async def post_remote_browser_handoff(
        self,
        connection_id: str,
        *,
        owner_user_id: int,
        session_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        sid = (session_id or "").strip()
        if not sid or "/" in sid or ".." in sid:
            raise OctopError(ErrorCode.BRIDGE_TUNNEL_FAILED, "invalid browser session_id")
        raw = json.dumps(body).encode("utf-8")
        resp = await self.tunnel_http(
            connection_id=connection_id,
            owner_user_id=owner_user_id,
            method="POST",
            path=f"/api/browser/sessions/{sid}/handoff",
            headers={"content-type": "application/json"},
            body=raw,
        )
        if resp.status_code >= 400:
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                f"peer browser handoff HTTP {resp.status_code}: {resp.text[:200]}",
            )
        try:
            data = resp.json()
        except Exception as exc:
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                "peer browser handoff returned invalid JSON",
            ) from exc
        if not isinstance(data, dict):
            raise OctopError(
                ErrorCode.BRIDGE_TUNNEL_FAILED,
                "peer browser handoff payload invalid",
            )
        return data

    # -- chat turn relay -----------------------------------------------------

    async def open_turn_waiter(self, request_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._turn_waiters[request_id] = q
        return q

    def close_turn_waiter(self, request_id: str) -> None:
        self._turn_waiters.pop(request_id, None)

    async def _handle_inbound_turn(self, connection_id: str, payload: dict[str, Any]) -> None:
        msg_type = str(payload.get("type") or "")
        request_id = str(payload.get("request_id") or "").strip()
        if msg_type in {"turn.chunk", "turn.end", "turn.error"} and request_id:
            q = self._turn_waiters.get(request_id)
            if q is not None:
                await q.put(payload)
            return
        if msg_type == "turn.start":
            await self._execute_peer_turn(connection_id, payload)

    async def _execute_peer_turn(self, connection_id: str, payload: dict[str, Any]) -> None:
        """Peer asked us to run a turn on a local agent; stream chunks back."""
        from octop.infra.gateway.ws import WS_CHANNEL_ID
        from octop.infra.gateway.ws.ws_hub import stamp_thread_id

        request_id = str(payload.get("request_id") or "").strip()
        agent_id = str(payload.get("agent_id") or "").strip()
        sess = self._sessions.get(connection_id)
        if sess is None or not request_id or not agent_id:
            return
        row = self._repo.get(connection_id)
        if row is None:
            return
        if self._asgi_app is None:
            await sess.send_json(
                {
                    "type": "turn.error",
                    "request_id": request_id,
                    "message": "bridge owner unavailable",
                }
            )
            return
        server = self._asgi_app.state.octop_server
        user = (
            server.app_runtime.user_manager.get_by_id(row.owner_user_id)
            if server.app_runtime is not None
            else None
        )
        if user is None:
            await sess.send_json(
                {
                    "type": "turn.error",
                    "request_id": request_id,
                    "message": "bridge owner unavailable",
                }
            )
            return
        assert server.app_runtime is not None
        gateway = server.app_runtime.gateway
        hub = gateway.ws_hub
        channel_manager = gateway.channel_manager
        if channel_manager is None:
            await sess.send_json(
                {
                    "type": "turn.error",
                    "request_id": request_id,
                    "message": "gateway not ready",
                }
            )
            return

        bridge_conn_id = f"bridge-turn-{request_id}"
        chunk_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def send_frame(frame: dict[str, Any]) -> None:
            await chunk_queue.put(frame)

        runner = self._peer_turn_runner
        if runner is None:
            await sess.send_json(
                {
                    "type": "turn.error",
                    "request_id": request_id,
                    "message": "peer turn runner not wired",
                }
            )
            return

        hub.register(bridge_conn_id, send_frame, user_id=user.id)
        try:
            prepared = await runner(
                server=server,
                user=user,
                agent_id=agent_id,
                payload=payload,
                ws_connection_id=bridge_conn_id,
            )
            hub.subscribe(prepared.thread_id, bridge_conn_id)
            await sess.send_json(
                {
                    "type": "turn.chunk",
                    "request_id": request_id,
                    "frame": {
                        "type": "thread",
                        "thread_id": prepared.thread_id,
                    },
                }
            )
            channel_manager.enqueue(WS_CHANNEL_ID, prepared.inbound)
            while True:
                item = await chunk_queue.get()
                if item is None:
                    break
                stamped = stamp_thread_id(item, prepared.thread_id)
                await sess.send_json(
                    {
                        "type": "turn.chunk",
                        "request_id": request_id,
                        "frame": stamped,
                    }
                )
                if str(stamped.get("type") or "") in {"done", "error"}:
                    break
            await sess.send_json({"type": "turn.end", "request_id": request_id})
        except PeerTurnRejected as exc:
            await sess.send_json(
                {
                    "type": "turn.error",
                    "request_id": request_id,
                    "message": str(exc)[:500],
                }
            )
        except Exception as exc:
            logger.exception("bridge peer turn failed")
            await sess.send_json(
                {
                    "type": "turn.error",
                    "request_id": request_id,
                    "message": str(exc)[:500],
                }
            )
        finally:
            hub.unregister(bridge_conn_id)

    async def relay_user_turn(
        self,
        *,
        connection_id: str,
        owner_user_id: int,
        remote_agent_id: str,
        turn_payload: dict[str, Any],
        on_frame: Any,
    ) -> None:
        """Send turn.start to peer and forward turn.chunk frames to ``on_frame``."""
        import uuid

        self.get_owned(connection_id, owner_user_id)
        sess = self.require_session(connection_id)
        request_id = uuid.uuid4().hex
        queue = await self.open_turn_waiter(request_id)
        try:
            await sess.send_json(
                {
                    "type": "turn.start",
                    "request_id": request_id,
                    "agent_id": remote_agent_id,
                    **turn_payload,
                }
            )
            while True:
                msg = await asyncio.wait_for(queue.get(), timeout=600.0)
                msg_type = str(msg.get("type") or "")
                if msg_type == "turn.chunk":
                    frame = msg.get("frame")
                    if isinstance(frame, dict):
                        await on_frame(
                            rewrite_peer_stream_frame(
                                connection_id,
                                frame,
                                remote_agent_id=remote_agent_id,
                            )
                        )
                elif msg_type == "turn.error":
                    await on_frame(
                        {
                            "type": "error",
                            "message": str(msg.get("message") or "bridge turn error"),
                        }
                    )
                    await on_frame({"type": "done"})
                    break
                elif msg_type == "turn.end":
                    break
        finally:
            self.close_turn_waiter(request_id)

    # -- browser stream relay ------------------------------------------------

    async def open_browser_waiter(self, request_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._browser_waiters[request_id] = q
        return q

    def close_browser_waiter(self, request_id: str) -> None:
        self._browser_waiters.pop(request_id, None)

    async def _handle_inbound_browser(self, connection_id: str, payload: dict[str, Any]) -> None:
        msg_type = str(payload.get("type") or "")
        request_id = str(payload.get("request_id") or "").strip()
        if msg_type in {"browser.server", "browser.error"} and request_id:
            q = self._browser_waiters.get(request_id)
            if q is not None:
                await q.put(payload)
            return
        if msg_type == "browser.end" and request_id:
            q = self._browser_waiters.get(request_id)
            if q is not None:
                await q.put(payload)
            client_q = self._browser_peer_clients.get(request_id)
            if client_q is not None:
                await client_q.put({"type": "stop"})
            return
        if msg_type == "browser.client" and request_id:
            client_q = self._browser_peer_clients.get(request_id)
            if client_q is not None:
                message = payload.get("message")
                if isinstance(message, dict):
                    await client_q.put(message)
            return
        if msg_type == "browser.start":
            asyncio.create_task(
                self._execute_peer_browser(connection_id, payload),
                name=f"bridge-browser-{request_id or 'unknown'}",
            )

    async def _execute_peer_browser(self, connection_id: str, payload: dict[str, Any]) -> None:
        """Peer asked us to attach to the local browser harness and stream frames."""
        request_id = str(payload.get("request_id") or "").strip()
        sess = self._sessions.get(connection_id)
        if sess is None or not request_id:
            return
        row = self._repo.get(connection_id)
        if row is None:
            return
        user = self._users.get(row.owner_user_id)
        if user is None:
            await sess.send_json(
                {
                    "type": "browser.error",
                    "request_id": request_id,
                    "message": "bridge owner unavailable",
                }
            )
            await sess.send_json({"type": "browser.end", "request_id": request_id})
            return

        client_q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._browser_peer_clients[request_id] = client_q
        closed = False

        async def send_json(frame: dict[str, Any]) -> None:
            if closed or sess.closed:
                return
            await sess.send_json(
                {
                    "type": "browser.server",
                    "request_id": request_id,
                    "frame": frame,
                }
            )

        def is_connected() -> bool:
            return not closed and not sess.closed

        async def receive_text() -> Any:
            item = await client_q.get()
            if item is None:
                return {"type": "stop"}
            return item

        start_msg = payload.get("start")
        if not isinstance(start_msg, dict):
            start_msg = {"type": "start", "url": "", "reuse_session": True}
        else:
            start_msg = {**start_msg, "type": "start"}

        runner = self._peer_browser_runner
        if runner is None:
            await sess.send_json(
                {
                    "type": "browser.error",
                    "request_id": request_id,
                    "message": "peer browser runner not wired",
                }
            )
            await sess.send_json({"type": "browser.end", "request_id": request_id})
            return

        try:
            await runner(
                send_json=send_json,
                is_connected=is_connected,
                receive_text=receive_text,
                user_id=int(user.id),
                listen_only=bool(payload.get("listen_only")),
                default_width=int(payload.get("width") or 1280),
                default_height=int(payload.get("height") or 800),
                start_msg=start_msg,
            )
        except Exception as exc:
            logger.exception("bridge peer browser stream failed")
            with suppress(Exception):
                await sess.send_json(
                    {
                        "type": "browser.error",
                        "request_id": request_id,
                        "message": str(exc)[:500],
                    }
                )
        finally:
            closed = True
            self._browser_peer_clients.pop(request_id, None)
            with suppress(Exception):
                await sess.send_json({"type": "browser.end", "request_id": request_id})

    async def relay_browser_stream(
        self,
        *,
        connection_id: str,
        owner_user_id: int,
        listen_only: bool,
        width: int,
        height: int,
        on_frame: Any,
        client_messages: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        """Relay dashboard browser WS traffic to the peer harness."""
        import uuid

        self.get_owned(connection_id, owner_user_id)
        sess = self.require_session(connection_id)
        request_id = uuid.uuid4().hex
        queue = await self.open_browser_waiter(request_id)

        forward_task: asyncio.Task[None] | None = None

        async def forward_client() -> None:
            while True:
                msg = await client_messages.get()
                if msg is None:
                    await sess.send_json({"type": "browser.end", "request_id": request_id})
                    return
                if msg.get("type") == "start":
                    # Already sent in browser.start; ignore duplicate.
                    continue
                await sess.send_json(
                    {
                        "type": "browser.client",
                        "request_id": request_id,
                        "message": msg,
                    }
                )

        try:
            # Wait for the client's start message before opening the peer stream.
            first = await asyncio.wait_for(client_messages.get(), timeout=15.0)
            if first is None:
                return
            if not isinstance(first, dict) or first.get("type") != "start":
                await on_frame({"type": "error", "message": "expected start message"})
                return

            await sess.send_json(
                {
                    "type": "browser.start",
                    "request_id": request_id,
                    "listen_only": listen_only,
                    "width": width,
                    "height": height,
                    "start": first,
                }
            )
            forward_task = asyncio.create_task(forward_client())

            while True:
                msg = await asyncio.wait_for(queue.get(), timeout=600.0)
                msg_type = str(msg.get("type") or "")
                if msg_type == "browser.server":
                    frame = msg.get("frame")
                    if isinstance(frame, dict):
                        await on_frame(frame)
                elif msg_type == "browser.error":
                    await on_frame(
                        {
                            "type": "error",
                            "message": str(msg.get("message") or "bridge browser error"),
                        }
                    )
                    await on_frame({"type": "status", "status": "error"})
                    break
                elif msg_type == "browser.end":
                    break
        except TimeoutError:
            await on_frame({"type": "error", "message": "bridge browser timed out"})
            await on_frame({"type": "status", "status": "error"})
        finally:
            self.close_browser_waiter(request_id)
            if forward_task is not None:
                forward_task.cancel()
                with suppress(asyncio.CancelledError):
                    await forward_task
            with suppress(Exception):
                await sess.send_json({"type": "browser.end", "request_id": request_id})


def public_base_url_from_config(bind_host: str, port: int) -> str:
    host = bind_host if bind_host not in {"0.0.0.0", "::"} else "127.0.0.1"
    return f"http://{host}:{port}"


def split_request_path(path: str) -> tuple[str, str]:
    """Return (path_without_query, query)."""
    parts = urlsplit(path)
    return parts.path, parts.query
