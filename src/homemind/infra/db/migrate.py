"""Apply HomeMind migrations without consuming Octop schema versions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from octop.infra.db.pool import DatabasePool

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_VERSION_TABLE = "_homemind_schema_version"
_SQL_STMT_RE = re.compile(r";\s*\n")


def _discover(dialect: str) -> list[tuple[int, Path]]:
    suffix = ".pg.sql" if dialect == "postgresql" else ".sql"
    migrations: list[tuple[int, Path]] = []
    for path in sorted(_MIGRATIONS_DIR.iterdir()):
        if dialect != "postgresql" and path.name.endswith(".pg.sql"):
            continue
        match = re.match(r"^(\d{3})_.*" + re.escape(suffix) + r"$", path.name)
        if match:
            migrations.append((int(match.group(1)), path))
    return migrations


def _split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    for part in _SQL_STMT_RE.split(sql):
        lines = part.strip().splitlines()
        while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
            lines.pop(0)
        statement = "\n".join(lines).strip()
        if statement:
            statements.append(statement)
    return statements


def _ensure_version_table(db: DatabasePool) -> None:
    with db.connect() as conn:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {_VERSION_TABLE} (version INTEGER NOT NULL)")
        row = conn.execute(f"SELECT version FROM {_VERSION_TABLE}").fetchone()
        if row is None:
            conn.execute(f"INSERT INTO {_VERSION_TABLE}(version) VALUES (0)")


def _current_version(db: DatabasePool) -> int:
    with db.connect() as conn:
        row = conn.execute(f"SELECT version FROM {_VERSION_TABLE}").fetchone()
    if row is None:
        return 0
    value: Any = row["version"] if isinstance(row, Mapping) else row[0]
    return int(value)


def run_migrations(db: DatabasePool) -> None:
    """Bring the HomeMind schema up to date on an Octop database connection."""
    _ensure_version_table(db)
    current = _current_version(db)
    for version, path in _discover(db.dialect):
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        if db.dialect == "postgresql":
            with db.transaction() as conn:
                for statement in _split_sql(sql):
                    conn.execute(statement)
        else:
            with db.connect() as conn:
                conn.executescript(sql)
        current = version
    if current >= 2:
        _reapply_unreleased_v2(db)


def _reapply_unreleased_v2(db: DatabasePool) -> None:
    """Keep databases that recorded unreleased v2 equivalent to its canonical DDL."""
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1 FROM homemind_family_audit_log WHERE 1 = 0")
        return
    except Exception:
        pass
    suffix = ".pg.sql" if db.dialect == "postgresql" else ".sql"
    sql = (_MIGRATIONS_DIR / f"002_family_memory{suffix}").read_text(encoding="utf-8")
    if db.dialect == "postgresql":
        with db.transaction() as conn:
            for statement in _split_sql(sql):
                conn.execute(statement)
    else:
        with db.connect() as conn:
            conn.executescript(sql)
