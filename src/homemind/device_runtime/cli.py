"""``homemind device-runtime`` command line interface.

Two subcommands:

``pair``
    exchange a one-time code for a device token and store it;

``run``
    enter the heartbeat / poll / execute loop.

Kept separate from the main ``homemind`` CLI because the runtime is
installed on a machine that never has a HomeMind server, a dashboard, or
an Octop configuration — it only needs this module and the token.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from homemind.device_runtime import (
    AuthenticationLost,
    RuntimeClient,
    __version__,
    load_credential,
    pair,
    save_credential,
    serve,
)

DEFAULT_DATA_DIR = Path.home() / ".homemind-runtime"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="homemind-device-runtime",
        description="HomeMind device runtime for NAS / home PC file commands.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Where the device token is stored (default: %(default)s).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity (default: %(default)s).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pair_cmd = sub.add_parser("pair", help="Pair this device with a server.")
    pair_cmd.add_argument("--server", required=True, help="HomeMind server base URL.")
    pair_cmd.add_argument("--code", required=True, help="One-time pairing code.")
    pair_cmd.add_argument(
        "--root",
        required=True,
        help="Directory this device is authorised to touch. Nothing outside "
        "it can be read or written, whatever a command asks for.",
    )

    run_cmd = sub.add_parser("run", help="Run the runtime loop.")
    run_cmd.add_argument("--poll-seconds", type=float, default=5.0, help="Idle poll interval.")
    run_cmd.add_argument(
        "--heartbeat-seconds", type=float, default=30.0, help="Heartbeat interval."
    )
    run_cmd.add_argument(
        "--once",
        action="store_true",
        help="Poll once and exit; useful for cron-style deployments.",
    )
    return parser


def _cmd_pair(args: argparse.Namespace) -> int:
    credential = pair(args.server, args.code, root=args.root)
    save_credential(args.data_dir, credential)
    # The token itself is never printed: this may be a shared terminal or
    # a captured log.
    print(
        json.dumps(
            {
                "device_id": credential.device_id,
                "server": credential.server,
                "root": credential.root,
                "token_stored_at": str(args.data_dir / "device.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    credential = load_credential(args.data_dir)
    if credential is None:
        print(
            "No stored credential. Run `pair` first.",
            file=sys.stderr,
        )
        return 2
    client = RuntimeClient(credential)

    if args.once:
        from homemind.device_runtime import run_once

        try:
            worked = run_once(client)
        except AuthenticationLost as exc:
            print(str(exc), file=sys.stderr)
            return 3
        print(json.dumps({"executed": worked}, ensure_ascii=False))
        return 0

    stopping = False

    def _request_stop(_signum: int, _frame: Any) -> None:
        # Graceful: the in-flight command finishes and reports, so the
        # server never has to wait out a lease for work that is done.
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_stop)

    try:
        serve(
            client,
            poll_seconds=args.poll_seconds,
            heartbeat_seconds=args.heartbeat_seconds,
            should_stop=lambda: stopping,
        )
    except AuthenticationLost as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.command == "pair":
        return _cmd_pair(args)
    if args.command == "run":
        return _cmd_run(args)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
