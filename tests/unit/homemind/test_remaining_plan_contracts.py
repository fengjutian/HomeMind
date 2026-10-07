"""Stage 0 contract guards for the remaining-features plan.

The calendar / notification / task-scheduler work that follows adds
tables and routes. These tests pin the surface that already shipped so a
later migration or router cannot quietly break an existing client:

* the HomeMind HTTP surface stays reachable (paths are a subset check --
  new routes may be added, old ones may not disappear);
* an old Family Task client that only knows ``title`` / ``description``
  / ``due_at`` still works, because Stage 4 extends that model;
* the Transaction action names registered today stay registered, because
  Stage 3 only *adds* handlers;
* text PDF / DOCX parsing keeps working, because Stage 5's OCR work
  changes that path;
* Device Runtime file capabilities keep their public methods, because
  Stage 6's smart-home adapter work must not fork them.
"""

from __future__ import annotations

from pathlib import Path

from homemind.api.app import build_app
from homemind.api.routers.tasks import (
    TaskCreateBody,
    TaskResponse,
    TaskUpdateBody,
)
from homemind.infra.family.knowledge_parsers import (
    parse_docx,
    parse_pdf,
    parser_for,
)
from homemind.infra.family.transaction_actions.registry import (
    build_default_action_registry,
)
from homemind.infra.server import HomeMindServer
from octop.infra.db.migrate import run_migrations as run_octop_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _homemind_surface() -> set[tuple[str, str]]:
    schema = build_app(HomeMindServer()).openapi()
    surface: set[tuple[str, str]] = set()
    for path, methods in schema["paths"].items():
        if not path.startswith("/api/homemind"):
            continue
        for method in methods:
            if method.upper() in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
                surface.add((method.upper(), path))
    return surface


# Every (method, path) that existed before the calendar work landed. The
# assertion is a subset check on purpose: adding endpoints is the point
# of the following stages, removing one is a breaking change.
_FROZEN_HOMEMIND_SURFACE: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/homemind/families"),
        ("POST", "/api/homemind/families"),
        ("GET", "/api/homemind/families/{family_id}"),
        ("PATCH", "/api/homemind/families/{family_id}"),
        ("DELETE", "/api/homemind/families/{family_id}"),
        ("GET", "/api/homemind/families/{family_id}/members"),
        ("POST", "/api/homemind/families/{family_id}/members"),
        ("GET", "/api/homemind/families/{family_id}/relationships"),
        ("POST", "/api/homemind/families/{family_id}/relationships"),
        ("GET", "/api/homemind/families/{family_id}/spaces"),
        ("POST", "/api/homemind/families/{family_id}/spaces"),
        ("GET", "/api/homemind/families/{family_id}/permissions"),
        ("GET", "/api/homemind/families/{family_id}/tasks"),
        ("POST", "/api/homemind/families/{family_id}/tasks"),
        ("PATCH", "/api/homemind/families/{family_id}/tasks/{task_id}"),
        ("DELETE", "/api/homemind/families/{family_id}/tasks/{task_id}"),
        ("GET", "/api/homemind/families/{family_id}/albums"),
        ("POST", "/api/homemind/families/{family_id}/albums"),
        ("GET", "/api/homemind/families/{family_id}/memories"),
        ("POST", "/api/homemind/families/{family_id}/memories"),
        ("POST", "/api/homemind/families/{family_id}/context/resolve"),
        ("GET", "/api/homemind/families/{family_id}/transactions"),
        ("GET", "/api/homemind/families/{family_id}/approvals"),
        ("GET", "/api/homemind/families/{family_id}/filesystem"),
        ("GET", "/api/homemind/families/{family_id}/filesystem/search"),
        ("GET", "/api/homemind/families/{family_id}/filesystem/read"),
        ("POST", "/api/homemind/families/{family_id}/filesystem/actions"),
        ("GET", "/api/homemind/families/{family_id}/transactions"),
        ("GET", "/api/homemind/families/{family_id}/transactions/{transaction_id}"),
        ("POST", "/api/homemind/families/{family_id}/transactions/{transaction_id}/cancel"),
        ("GET", "/api/homemind/families/{family_id}/devices"),
        ("POST", "/api/homemind/families/{family_id}/devices"),
        ("GET", "/api/homemind/families/{family_id}/devices/{device_id}/commands"),
        ("POST", "/api/homemind/runtime/pair"),
        ("POST", "/api/homemind/runtime/heartbeat"),
        ("GET", "/api/homemind/runtime/commands"),
        ("POST", "/api/homemind/runtime/commands/{command_id}/result"),
        ("GET", "/api/homemind/families/{family_id}/invites"),
        ("POST", "/api/homemind/families/{family_id}/invites"),
        ("POST", "/api/homemind/families/invites/redeem"),
        ("GET", "/api/homemind/families/{family_id}/knowledge/documents"),
        ("GET", "/api/homemind/families/{family_id}/knowledge/search"),
    }
)


def test_existing_homemind_routes_are_never_removed() -> None:
    """A later stage may add routes; it may not drop an existing one."""

    surface = _homemind_surface()
    missing = sorted(_FROZEN_HOMEMIND_SURFACE - surface)
    assert missing == [], (
        f"HomeMind routes disappeared during the remaining-features work: {missing}"
    )


def test_homemind_task_response_keeps_its_field_set() -> None:
    """Stage 4 extends the task model; the legacy response shape stays."""

    fields = set(TaskResponse.model_fields)
    assert fields == {
        "id",
        "family_id",
        "title",
        "description",
        "status",
        "assigned_member_id",
        "due_at",
        "created_by",
        "created_at",
        "updated_at",
    }


def test_homemind_task_accepts_legacy_client_payloads() -> None:
    """An old dashboard posts only title/description/due_at."""

    created = TaskCreateBody(title="Buy milk", description="2%", due_at=1893456000)
    assert created.model_dump() == {
        "title": "Buy milk",
        "description": "2%",
        "assigned_member_id": None,
        "due_at": 1893456000,
    }

    patched = TaskUpdateBody(status="DONE")
    assert patched.model_dump(exclude_unset=True) == {"status": "DONE"}

    # ``title`` alone is the minimum a legacy client may send.
    assert TaskCreateBody(title="x").title == "x"


def test_registered_transaction_action_names_stay_available(tmp_path: Path) -> None:
    """Stage 3 adds handlers; the ones shipping today must not disappear."""

    from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
    from homemind.infra.db.services import HomeMindServices
    from homemind.infra.family.context import FamilyContextManager
    from homemind.infra.family.filesystem import FamilyFilesystemManager
    from homemind.infra.family.manager import FamilyManager
    from homemind.infra.family.tasks import FamilyTaskManager

    pool = SqlitePool(tmp_path / "octop.db")
    run_octop_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)

    registry = build_default_action_registry(
        family=families,
        context=FamilyContextManager(families, services.family_context_repo),
        tasks=FamilyTaskManager(families, services.family_task_repo),
        filesystem=FamilyFilesystemManager(
            families,
            services.family_context_repo,
            audit=services.family_transaction_repo,
        ),
    )
    assert registry.actions() == [
        "event.create",
        "filesystem.copy",
        "filesystem.delete",
        "filesystem.move",
        "filesystem.rename",
        "memory.create",
        "task.create",
    ]
    assert registry.get("task.create") is not None
    assert registry.get("filesystem.delete") is not None


def test_empty_action_registry_registers_nothing() -> None:
    """A registry built without managers stays empty instead of guessing."""

    assert build_default_action_registry().actions() == []


def test_text_document_parsers_still_cover_pdf_and_docx(tmp_path: Path) -> None:
    """Stage 5 replaces the parse path with OCR-aware jobs; text first."""

    pdf = tmp_path / "contract.pdf"
    docx = tmp_path / "notes.docx"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    docx.write_bytes(b"PK\x03\x04")

    assert parser_for(pdf) is not None
    assert parser_for(docx) is not None
    assert callable(parse_pdf)
    assert callable(parse_docx)


def test_device_runtime_keeps_its_file_and_command_surface() -> None:
    """Stage 6 adds smart-home adapters beside these, not instead of them."""

    from homemind.infra.family.device_runtime import DeviceRuntimeManager

    for method in (
        "create_pairing_code",
        "complete_pairing",
        "heartbeat",
        "rotate_token",
        "revoke_token",
        "enqueue_command",
        "approve_command",
        "cancel_command",
        "next_pending_command",
        "report_command_result",
        "acknowledge_command",
        "list_commands",
        "list_devices",
        "mark_stale_devices_offline",
    ):
        assert callable(getattr(DeviceRuntimeManager, method)), method


def test_task_repo_round_trip_is_unchanged_by_new_migrations(tmp_path: Path) -> None:
    """The Stage 1 migration must not disturb the existing task table."""

    from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
    from homemind.infra.db.services import HomeMindServices
    from homemind.infra.family.manager import FamilyManager
    from homemind.infra.family.tasks import FamilyTaskManager

    pool = SqlitePool(tmp_path / "octop.db")
    run_octop_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    tasks = FamilyTaskManager(families, services.family_task_repo)
    user = User(1, "owner", Role.USER, "Owner")
    family = families.create_family(
        user, name="Contract Family", timezone="Asia/Shanghai", locale="zh"
    )

    task = tasks.create(family.id, user, title="Renew warranty", due_at=1893456000)
    assert tasks.list(family.id, user)[0].id == task.id
    assert tasks.update(family.id, task.id, user, {"status": "DONE"}).status == "DONE"
