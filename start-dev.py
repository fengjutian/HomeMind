#!/usr/bin/env python3
"""Start HomeMind development services on macOS, Linux, or Windows."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DASHBOARD_ROOT = REPO_ROOT / "dashboard"
DESKTOP_ROOT = REPO_ROOT / "desktop" / "src"
UV_CACHE_ROOT = REPO_ROOT / ".uv-cache"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def find_available_port(start_port: int) -> int:
    for port in range(start_port, start_port + 21):
        if not port_in_use(port):
            return port
    raise RuntimeError(
        f"No available development port found between {start_port} and {start_port + 20}."
    )


def require_command(name: str, purpose: str) -> str:
    command = shutil.which(name)
    if command is None:
        raise RuntimeError(f"{name} was not found in PATH. {purpose}")
    return command


def start_process(
    command: list[str], cwd: Path, *, env: dict[str, str] | None = None
) -> subprocess.Popen[bytes]:
    print(f"Starting in {cwd}: {' '.join(command)}", flush=True)
    return subprocess.Popen(command, cwd=cwd, env=env)


def wait_http_ready(url: str, process: subprocess.Popen[bytes], dashboard_root: Path) -> None:
    for _ in range(30):
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"Frontend exited with code {exit_code}. Run 'npm run dev' in "
                f"'{dashboard_root}' to see the error."
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if 200 <= response.status < 500:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise RuntimeError(
        f"Frontend process {process.pid} started, but {url} did not become ready within 15 seconds."
    )


def stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    running = [process for process in processes if process.poll() is None]
    for process in running:
        process.terminate()
    for process in running:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    for process in running:
        process.wait()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start HomeMind backend, frontend, and desktop development services."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="all",
        choices=("backend", "frontend", "web", "desktop", "all"),
        help="service group to start (default: all)",
    )
    return parser.parse_args()


def main() -> int:
    target = parse_args().target
    start_backend = target in {"backend", "web", "desktop", "all"}
    start_frontend = target in {"frontend", "web", "desktop", "all"}
    start_desktop = target in {"desktop", "all"}

    uv = (
        require_command("uv", "Install uv or open a terminal where uv is available.")
        if start_backend
        else None
    )
    npm = (
        require_command("npm", "Install Node.js 18+ and reopen the terminal.")
        if start_frontend
        else None
    )
    wails = (
        require_command("wails3", "Install Wails v3 and reopen the terminal.")
        if start_desktop
        else None
    )

    processes: list[subprocess.Popen[bytes]] = []
    frontend_url: str | None = None
    try:
        if start_backend:
            if port_in_use(8088):
                print("Backend is already listening on http://127.0.0.1:8088; reusing it.")
            else:
                backend_env = os.environ.copy()
                backend_env["UV_CACHE_DIR"] = str(UV_CACHE_ROOT)
                processes.append(
                    start_process(
                        [
                            uv,
                            "run",
                            "--frozen",
                            "homemind",
                            "run",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "8088",
                        ],
                        REPO_ROOT,
                        env=backend_env,
                    )
                )

        if start_frontend:
            frontend_port = find_available_port(5173)
            frontend_url = f"http://127.0.0.1:{frontend_port}"
            frontend_env = os.environ.copy()
            frontend_env["VITE_DEV_PORT"] = str(frontend_port)
            frontend_env["VITE_API_PORT"] = "8088"
            frontend_process = start_process([npm, "run", "dev"], DASHBOARD_ROOT, env=frontend_env)
            processes.append(frontend_process)
            wait_http_ready(frontend_url, frontend_process, DASHBOARD_ROOT)
            print(f"Frontend started (PID {frontend_process.pid}): {frontend_url}")

        if start_desktop:
            if port_in_use(9245):
                print(
                    "Wails dev server is already listening on 127.0.0.1:9245; "
                    "skipping duplicate desktop startup."
                )
            else:
                desktop_env = os.environ.copy()
                desktop_env["OCTOP_DESKTOP_URL"] = frontend_url
                processes.append(start_process([wails, "dev"], DESKTOP_ROOT, env=desktop_env))

        if not processes:
            return 0
        print("Development services are running. Press Ctrl+C to stop.")
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        failed = next(process for process in processes if process.poll() is not None)
        return failed.returncode or 0
    except KeyboardInterrupt:
        print("\nStopping development services...")
        return 0
    finally:
        stop_processes(processes)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
