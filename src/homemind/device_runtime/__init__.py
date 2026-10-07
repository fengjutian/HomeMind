"""HomeMind device runtime.

A small, UI-less agent that runs on a NAS or a home PC, pairs with a
HomeMind server, and executes filesystem commands on the household's
behalf. It is deliberately not a desktop product: it exists to prove that
HomeMind can act on files it does not host, inside a root the server
authorises, with every command visible to the family.
"""

from __future__ import annotations

from homemind.device_runtime.client import (
    AuthenticationLost,
    DeviceCredential,
    RuntimeClient,
    load_credential,
    pair,
    run_once,
    save_credential,
    serve,
)
from homemind.device_runtime.commands import (
    PROTOCOL_VERSION,
    SUPPORTED_CAPABILITIES,
    CommandContext,
    CommandRefused,
    UnsupportedCommand,
    execute,
)
from homemind.device_runtime.path_guard import PathEscapeError

#: Bumped with any breaking change to the command contract.
__version__ = f"1.0.{PROTOCOL_VERSION}"

__all__ = [
    "PROTOCOL_VERSION",
    "SUPPORTED_CAPABILITIES",
    "AuthenticationLost",
    "CommandContext",
    "CommandRefused",
    "DeviceCredential",
    "PathEscapeError",
    "RuntimeClient",
    "UnsupportedCommand",
    "__version__",
    "execute",
    "load_credential",
    "pair",
    "run_once",
    "save_credential",
    "serve",
]
