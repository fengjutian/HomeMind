"""Filesystem layout for ``~/.octop/``."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_SAFE_UPLOAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _safe_upload_id(upload_id: str) -> str:
    """Reject anything that is not a plain id.

    Upload staging is server-managed, so the id is the only client-influenced
    component of the path. Refusing separators, dots and absolute paths keeps a
    crafted id from escaping the staging root.
    """
    if not isinstance(upload_id, str) or not _SAFE_UPLOAD_ID.match(upload_id):
        raise ValueError("invalid upload_id")
    return upload_id


@dataclass(frozen=True)
class PathLayout:
    root: Path

    @classmethod
    def from_env(cls) -> PathLayout:
        """Resolve install root from ``OCTOP_HOME`` or ``~/.octop``."""
        raw = os.environ.get("OCTOP_HOME", "").strip()
        if raw:
            return cls(Path(raw).expanduser())
        return cls(Path.home() / ".octop")

    @property
    def db(self) -> Path:
        return self.root / "octop.db"

    @property
    def logs_dir(self) -> Path:
        """Structured runtime logs: ``~/.octop/logs/``."""
        return self.root / "logs"

    @property
    def log(self) -> Path:
        return self.logs_dir / "octop.log"

    def ensure_logs_dir(self) -> Path:
        """Create the logs directory and return it."""
        out = self.logs_dir
        out.mkdir(parents=True, exist_ok=True)
        return out

    def ensure_log(self) -> Path:
        """Create the logs directory and return ``octop.log``."""
        self.ensure_logs_dir()
        return self.log

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def users_dir(self) -> Path:
        return self.root / "users"

    def user_dir(self, username: str) -> Path:
        return self.users_dir / username

    @property
    def agents_dir(self) -> Path:
        """Global agents directory: ~/.octop/agents/"""
        return self.root / "agents"

    @property
    def expert_market_dir(self) -> Path:
        """Cached SkillHub expert templates: ``~/.octop/expert_market/``."""
        return self.root / "expert_market"

    @property
    def published_experts_dir(self) -> Path:
        """User-published expert snapshots: ``~/.octop/published_experts/``."""
        return self.root / "published_experts"

    @property
    def skill_packages_dir(self) -> Path:
        """Global skill package content: ``~/.octop/skill-packages/``."""
        return self.root / "skill-packages"

    @property
    def knowledge_dir(self) -> Path:
        """Global knowledge base files: ``~/.octop/knowledge/``."""
        return self.root / "knowledge"

    @property
    def uploads_dir(self) -> Path:
        """Resumable upload scratch space: ``~/.octop/uploads/``."""
        return self.root / "uploads"

    @property
    def uploads_staging_dir(self) -> Path:
        """Per-session part staging: ``~/.octop/uploads/staging/``."""
        return self.uploads_dir / "staging"

    def upload_staging_dir(self, upload_id: str) -> Path:
        """Staging dir for one session. Never accepts a client-supplied path."""
        return self.uploads_staging_dir / _safe_upload_id(upload_id)

    def ensure_upload_staging_dir(self, upload_id: str) -> Path:
        """``upload_staging_dir`` with mkdir -p."""
        out = self.upload_staging_dir(upload_id)
        out.mkdir(parents=True, exist_ok=True)
        return out

    @property
    def uploads_blobs_dir(self) -> Path:
        """Assembled uploads too large for the bytes-only workspace API."""
        return self.uploads_dir / "blobs"

    def upload_blob_dir(self, upload_id: str) -> Path:
        """Blob dir for one upload, guarded exactly like the staging dir."""
        return self.uploads_blobs_dir / _safe_upload_id(upload_id)

    def agent_workspace(self, agent_id: str) -> Path:
        """Global agent workspace: ~/.octop/agents/<agent_id>/"""
        return self.agents_dir / agent_id

    def ensure_agent_workspace(self, agent_id: str) -> Path:
        """Global agent workspace, mkdir -p."""
        out = self.agent_workspace(agent_id)
        out.mkdir(parents=True, exist_ok=True)
        return out

    def ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    @property
    def plugins_dir(self) -> Path:
        return self.root / "plugins"

    @property
    def tool_guard_rules_dir(self) -> Path:
        """User-editable command guard rules: ``~/.octop/security/tool_guard/``."""
        return self.root / "security" / "tool_guard"

    @property
    def tool_guard_rules_file(self) -> Path:
        return self.tool_guard_rules_dir / "dangerous_shell_commands.yaml"

    @property
    def backups_dir(self) -> Path:
        """Stored system backup archives: ``~/.octop/backups/``."""
        return self.root / "backups"

    def ensure_backups_dir(self) -> Path:
        out = self.backups_dir
        out.mkdir(parents=True, exist_ok=True)
        return out

    def backup_file(self, filename: str) -> Path:
        """Resolve a backup archive path under :attr:`backups_dir` (basename only)."""
        return self.backups_dir / Path(filename).name

    @property
    def user_avatars_dir(self) -> Path:
        """Uploaded user portraits: ``~/.octop/avatars/users/``."""
        return self.root / "avatars" / "users"

    @property
    def role_avatars_dir(self) -> Path:
        """Uploaded role portraits: ``~/.octop/avatars/roles/``."""
        return self.root / "avatars" / "roles"

    @property
    def ssl_dir(self) -> Path:
        """TLS certificates and ACME account keys: ``~/.octop/ssl/``."""
        return self.root / "ssl"

    def ensure_ssl_dir(self) -> Path:
        out = self.ssl_dir
        out.mkdir(parents=True, exist_ok=True)
        return out

    @property
    def connector_cli_dir(self) -> Path:
        """Per-instance CLI config roots: ``~/.octop/connector-cli/``."""
        return self.root / "connector-cli"

    def connector_cli_instance_dir(self, kind: str, instance_key: str) -> Path:
        """Isolated config dir for one connector CLI instance."""
        safe_kind = Path(kind).name
        safe_key = (
            "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in instance_key)[:80]
            or "default"
        )
        return self.connector_cli_dir / safe_kind / safe_key

    def ensure_connector_cli_instance_dir(self, kind: str, instance_key: str) -> Path:
        out = self.connector_cli_instance_dir(kind, instance_key)
        out.mkdir(parents=True, exist_ok=True)
        return out
