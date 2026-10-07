"""Long-lived runtime client for one family device.

The runtime is the piece of HomeMind that runs on a NAS or a home PC and
executes filesystem work for the household. It has no dashboard session:
it authenticates with the device token issued at pairing time.

Lifecycle, in order:

1. **pair** once with a human-typed code, store the token, then never
   pair again unless the token is revoked;
2. **heartbeat** on a timer so the dashboard can show which device is
   actually alive;
3. **poll** for one command, ACK it immediately, run it, report the
   outcome.

Two rules the loop does not bend:

* **A 401 stops everything.** The token was revoked or rotated; retrying
  would hammer the server and would also keep using a credential the
  operator has already invalidated.
* **The runtime never guesses.** If it dies mid-command, it does not
  report success on restart — the server's lease decides, and a lapsed
  lease makes the command reclaimable. That is why every write command is
  idempotent.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemind.device_runtime.commands import (
    PROTOCOL_VERSION,
    CommandContext,
    CommandRefused,
    UnsupportedCommand,
    execute,
)

logger = logging.getLogger(__name__)

#: How long to wait between polls when the queue was empty.
DEFAULT_POLL_SECONDS = 5.0
#: Heartbeat cadence; the server treats a device as offline past its own
#: timeout, so this must be comfortably shorter than that.
DEFAULT_HEARTBEAT_SECONDS = 30.0
#: Network failures are transient (a NAS reboot, a dropped Wi-Fi link);
#: only an auth failure is terminal.
MAX_CONSECUTIVE_NETWORK_ERRORS = 10


class AuthenticationLost(RuntimeError):
    """The device token was rejected. Stop; do not retry."""


@dataclass(frozen=True)
class DeviceCredential:
    """What pairing returns and what every later call needs."""

    device_id: str
    token: str
    server: str
    root: str


def credential_path(data_dir: Path) -> Path:
    """Where the token is stored.

    A separate file from any config so it can be chmod'ed on its own.
    """
    return Path(data_dir) / "device.json"


def save_credential(data_dir: Path, credential: DeviceCredential) -> None:
    """Persist the token with owner-only permissions where the platform has them."""
    target = credential_path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "device_id": credential.device_id,
                "token": credential.token,
                "server": credential.server,
                "root": credential.root,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    # Best effort: on Windows the mode is largely advisory, but setting it
    # still keeps the file out of a shared-permission default.
    try:
        os.chmod(target, 0o600)  # noqa: S103 - POSIX mode, ignored on Windows
    except OSError:
        logger.debug("could not restrict credential file permissions")


def load_credential(data_dir: Path) -> DeviceCredential | None:
    """Read a stored credential, or ``None`` when absent or corrupt."""
    target = credential_path(data_dir)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("stored device credential is unreadable; re-pair required")
        return None
    try:
        return DeviceCredential(
            device_id=str(data["device_id"]),
            token=str(data["token"]),
            server=str(data["server"]),
            root=str(data["root"]),
        )
    except KeyError:
        return None


class RuntimeClient:
    """Thin HTTP client for the runtime endpoints."""

    def __init__(
        self,
        credential: DeviceCredential,
        *,
        timeout: float = 30.0,
        opener: Any | None = None,
    ) -> None:
        self._credential = credential
        self._timeout = timeout
        # Injectable so the contract tests can drive a fake server without
        # binding a socket.
        self._opener = opener or urllib.request.urlopen

    @property
    def credential(self) -> DeviceCredential:
        """The credential in use. Exposed so the loop can build a context."""
        return self._credential

    def _url(self, path: str) -> str:
        return f"{self._credential.server.rstrip('/')}/api/homemind/runtime{path}"

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - scheme is operator-supplied https
            self._url(path),
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self._credential.token}"} if authenticated else {}),
            },
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                # Terminal by design: a revoked token must not be retried.
                raise AuthenticationLost(
                    "device token was rejected; re-pair this runtime",
                ) from exc
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"server returned {exc.code}: {body_text[:300]}") from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(f"cannot reach {self._credential.server}: {exc.reason}") from exc
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    # ---------------------------------------------------------------- calls

    def heartbeat(self, *, status: str = "ONLINE") -> dict[str, Any]:
        payload: dict[str, Any] = self._request(
            "/heartbeat",
            method="POST",
            body={"status": status, "runtime_version": f"runtime-v{PROTOCOL_VERSION}"},
        )
        return payload

    def next_command(self) -> dict[str, Any] | None:
        """Claim one command, or ``None`` when the queue is empty."""
        payload: dict[str, Any] | None = self._request("/commands")
        return payload

    def acknowledge(self, command_id: str) -> None:
        self._request(f"/commands/{command_id}/ack", method="POST", body={})

    def report(
        self,
        command_id: str,
        *,
        status: str,
        result: dict[str, Any],
        error: str | None = None,
    ) -> None:
        self._request(
            f"/commands/{command_id}/result",
            method="POST",
            body={"status": status, "result": result, "error": error},
        )


def pair(
    server: str,
    code: str,
    *,
    root: str,
    opener: Any | None = None,
    timeout: float = 30.0,
) -> DeviceCredential:
    """Exchange a one-time pairing code for a device token."""
    root_path = Path(root)
    if not root_path.is_dir():
        raise ValueError(f"root is not a directory: {root}")
    resolved_root = str(root_path.resolve())

    request = urllib.request.Request(  # noqa: S310
        f"{server.rstrip('/')}/api/homemind/runtime/pair",
        data=json.dumps({"code": code, "address": None}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"pairing failed ({exc.code}): {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(f"cannot reach {server}: {exc.reason}") from exc

    return DeviceCredential(
        device_id=str(payload["device"]["id"]),
        token=str(payload["token"]),
        server=server,
        root=resolved_root,
    )


def run_once(client: RuntimeClient, *, status: str = "ONLINE") -> bool:
    """One pass of the loop. Returns True when a command was executed.

    Split out so the contract tests can drive a single iteration with a
    scripted sequence of server responses instead of a real socket.
    """
    client.heartbeat(status=status)
    command = client.next_command()
    if not command:
        return False

    command_id = str(command.get("id") or command.get("command_id") or "")
    if not command_id:
        logger.error("server returned a command with no id; ignoring")
        return False

    # Acknowledge before doing the work: a long hash or copy must not look
    # like a lost command to the server's lease.
    client.acknowledge(command_id)

    capability = str(command.get("capability") or command.get("kind") or "")
    payload = command.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    ctx = CommandContext(root=client.credential.root, command_id=command_id)
    try:
        result = execute(ctx, capability, payload)
    except (CommandRefused, UnsupportedCommand) as exc:
        # A refusal is a well-understood negative answer: report it as a
        # failure with the reason, not as a transport error.
        client.report(command_id, status="FAILED", result={}, error=str(exc))
        return True
    except OSError as exc:
        client.report(command_id, status="FAILED", result={}, error=str(exc)[:500])
        return True

    client.report(command_id, status="SUCCEEDED", result=result)
    return True


def serve(
    client: RuntimeClient,
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    should_stop: Any | None = None,
    sleep: Any | None = None,
) -> None:
    """Run until stopped, or until the token is rejected.

    ``should_stop`` and ``sleep`` are injectable so a test can drive a
    bounded number of iterations without wall-clock time.
    """
    sleep = sleep or time.sleep
    last_heartbeat = 0.0
    network_errors = 0

    while should_stop is None or not should_stop():
        now = time.monotonic()
        if now - last_heartbeat >= heartbeat_seconds:
            client.heartbeat()
            last_heartbeat = now

        try:
            # Whether or not a command ran, the server answered: the
            # link is healthy again.
            run_once(client)
            network_errors = 0
        except AuthenticationLost:
            # Not retried and not swallowed: an operator must re-pair.
            raise
        except ConnectionError as exc:
            network_errors += 1
            if network_errors >= MAX_CONSECUTIVE_NETWORK_ERRORS:
                raise RuntimeError(
                    f"server unreachable after {network_errors} attempts",
                ) from exc
            logger.warning("runtime lost contact with the server: %s", exc)
            sleep(poll_seconds)
        else:
            sleep(poll_seconds)


__all__ = [
    "AuthenticationLost",
    "DEFAULT_HEARTBEAT_SECONDS",
    "DEFAULT_POLL_SECONDS",
    "DeviceCredential",
    "RuntimeClient",
    "credential_path",
    "load_credential",
    "pair",
    "run_once",
    "save_credential",
    "serve",
]
