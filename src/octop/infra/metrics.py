"""In-memory metrics counters."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class Metrics:
    messages_total: int = 0
    stream_errors_total: int = 0
    cron_runs_total: int = 0
    cron_errors_total: int = 0
    agent_active: int = 0
    # Bridge connections (plan phase 16).
    bridge_connections_online: int = 0
    bridge_connects_total: int = 0
    bridge_reconnects_total: int = 0
    bridge_auth_refresh_total: int = 0
    bridge_auth_refresh_failures_total: int = 0
    bridge_tunnel_requests_total: int = 0
    bridge_tunnel_errors_total: int = 0
    bridge_tunnel_timeouts_total: int = 0
    bridge_tunnel_in_flight: int = 0
    bridge_tunnel_bytes_total: int = 0
    bridge_turn_frames_total: int = 0
    bridge_turn_dropped_total: int = 0
    bridge_frames_shed_total: int = 0
    # Resumable upload sessions (plan phase 4).
    upload_sessions_active: int = 0
    upload_sessions_total: int = 0
    upload_bytes_received_total: int = 0
    upload_parts_total: int = 0
    upload_failures_total: int = 0
    upload_checksum_failures_total: int = 0
    upload_resumes_total: int = 0
    upload_cleanup_bytes_total: int = 0
    upload_cleanup_sessions_total: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + n)

    def set(self, name: str, value: int) -> None:
        with self._lock:
            setattr(self, name, value)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "messages_total": self.messages_total,
                "stream_errors_total": self.stream_errors_total,
                "cron_runs_total": self.cron_runs_total,
                "cron_errors_total": self.cron_errors_total,
                "agent_active": self.agent_active,
                "bridge_connections_online": self.bridge_connections_online,
                "bridge_connects_total": self.bridge_connects_total,
                "bridge_reconnects_total": self.bridge_reconnects_total,
                "bridge_auth_refresh_total": self.bridge_auth_refresh_total,
                "bridge_auth_refresh_failures_total": self.bridge_auth_refresh_failures_total,
                "bridge_tunnel_requests_total": self.bridge_tunnel_requests_total,
                "bridge_tunnel_errors_total": self.bridge_tunnel_errors_total,
                "bridge_tunnel_timeouts_total": self.bridge_tunnel_timeouts_total,
                "bridge_tunnel_in_flight": self.bridge_tunnel_in_flight,
                "bridge_tunnel_bytes_total": self.bridge_tunnel_bytes_total,
                "bridge_turn_frames_total": self.bridge_turn_frames_total,
                "bridge_turn_dropped_total": self.bridge_turn_dropped_total,
                "bridge_frames_shed_total": self.bridge_frames_shed_total,
                "upload_sessions_active": self.upload_sessions_active,
                "upload_sessions_total": self.upload_sessions_total,
                "upload_bytes_received_total": self.upload_bytes_received_total,
                "upload_parts_total": self.upload_parts_total,
                "upload_failures_total": self.upload_failures_total,
                "upload_checksum_failures_total": self.upload_checksum_failures_total,
                "upload_resumes_total": self.upload_resumes_total,
                "upload_cleanup_bytes_total": self.upload_cleanup_bytes_total,
                "upload_cleanup_sessions_total": self.upload_cleanup_sessions_total,
            }


METRICS = Metrics()
