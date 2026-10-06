"""Verify HomeMind SQL migrations ship in SQLite/PostgreSQL pairs and
both dialects reach the same final schema version."""

from __future__ import annotations

from pathlib import Path

from homemind.infra.db.migrate import _discover
from homemind.infra.db.migrate import _MIGRATIONS_DIR as HM_MIGRATIONS_DIR


def _homemind_migration_names() -> tuple[set[str], set[str]]:
    sqlite: set[str] = set()
    pg: set[str] = set()
    for path in sorted(HM_MIGRATIONS_DIR.iterdir()):
        if path.name == "__init__.py" or path.name.startswith("."):
            continue
        if path.name.endswith(".pg.sql"):
            pg.add(path.name[: -len(".pg.sql")])
        elif path.name.endswith(".sql"):
            sqlite.add(path.name[: -len(".sql")])
    return sqlite, pg


def test_homemind_sqlite_and_pg_migrations_are_paired() -> None:
    sqlite, pg = _homemind_migration_names()
    assert sqlite == pg, (
        f"HomeMind migration files must ship in SQLite/PostgreSQL pairs. "
        f"SQLite-only: {sorted(sqlite - pg)}. PG-only: {sorted(pg - sqlite)}."
    )


def test_homemind_migration_versions_run_in_order_on_both_dialects() -> None:
    sqlite = [v for v, _ in _discover("sqlite")]
    pg = [v for v, _ in _discover("postgresql")]
    assert sqlite, "expected at least one SQLite migration"
    assert sqlite == pg, (
        f"Sequence versions differ: SQLite={sqlite} PG={pg}. "
        "Both dialects must reach the same final schema version."
    )


def test_homemind_pg_migration_files_have_no_sqlite_only_artifacts() -> None:
    """PostgreSQL migrations must not declare SQLite-only objects
    (FTS5 virtual tables, sqlite-specific PRAGMAs, etc.) so they
    actually run on PostgreSQL."""

    forbidden_substrings = (
        "USING fts5",
        "PRAGMA ",
    )
    for path in sorted(HM_MIGRATIONS_DIR.glob("*.pg.sql")):
        text = path.read_text(encoding="utf-8").lower()
        for needle in forbidden_substrings:
            assert needle.lower() not in text, (
                f"{path.name} contains SQLite-only fragment {needle!r}"
            )


def test_homemind_migrations_directory_layout_is_stable() -> None:
    """Regression guard: keep migrations folder colocated with the
    runner so the dialect switch in migrate.py keeps working when
    new files land."""

    assert HM_MIGRATIONS_DIR.is_dir(), (
        f"expected HomeMind migrations directory at {HM_MIGRATIONS_DIR}"
    )
    assert HM_MIGRATIONS_DIR.parent.name == "db"
    assert HM_MIGRATIONS_DIR.parent.parent.name == "infra"
    # The runner resolves the directory via Path(__file__).parent; keep
    # the layout stable so the relative path doesn't drift.
    runner_path = Path(__file__).resolve()
    assert (runner_path.parents[3] / "src" / "homemind" / "infra" / "db" / "migrations").is_dir()
