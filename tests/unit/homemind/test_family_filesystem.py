from __future__ import annotations

from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.filesystem import FamilyFilesystemManager
from homemind.infra.family.manager import FamilyManager, PermissionEffect
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transactions import FamilyTransactionManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import Role, User


def _setup(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    user = User(1, "owner", Role.USER, "Owner")
    family = families.create_family(
        user, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.txt").write_text("family note", encoding="utf-8")
    source = FamilyAssetManager(
        services.family_repo, services.family_asset_repo
    ).scan_directory(family.id, user, directory=str(root))
    filesystem = FamilyFilesystemManager(
        families, services.family_asset_repo, services.family_transaction_repo
    )
    transactions = FamilyTransactionManager(
        families,
        FamilyContextManager(families, services.family_context_repo),
        FamilyTaskManager(families, services.family_task_repo),
        services.family_transaction_repo,
        UserRepo(pool),
        filesystem,
    )
    return families, filesystem, transactions, family, user, root, source.source_id


def test_read_operations_are_bounded_and_audited(tmp_path: Path) -> None:
    families, filesystem, transactions, family, user, _, source_id = _setup(tmp_path)

    assert filesystem.read(
        family.id, user, source_id=source_id, path="note.txt"
    ) == "family note"
    assert [row.path for row in filesystem.list(family.id, user, source_id=source_id)] == [
        "note.txt"
    ]
    assert [row.path for row in filesystem.search(
        family.id, user, source_id=source_id, query="note"
    )] == ["note.txt"]
    with pytest.raises(HomeMindError):
        filesystem.read(family.id, user, source_id=source_id, path="../outside.txt")
    assert len(transactions.list_audit(family.id, user)) == 3


def test_delete_requires_approval_and_is_recoverable(tmp_path: Path) -> None:
    families, _, transactions, family, user, root, source_id = _setup(tmp_path)
    owner = families.repo.list_members(family.id)[0]
    families.create_permission(
        family.id,
        user,
        subject_member_id=owner.id,
        space_id=None,
        action="filesystem.delete",
        effect=PermissionEffect.ALLOW,
        expires_at=None,
    )

    transaction, approval = transactions.plan(
        family.id,
        user,
        action="filesystem.delete",
        payload={"source_id": source_id, "path": "note.txt"},
    )

    assert transaction.status == "WAITING_APPROVAL"
    assert approval is not None
    assert (root / "note.txt").exists()
    completed = transactions.approve(family.id, approval.id, user)
    assert completed.status == "COMPLETED"
    assert not (root / "note.txt").exists()
    assert list((root / ".homemind-trash" / transaction.id).rglob("note.txt"))


def test_rejected_move_does_not_modify_files(tmp_path: Path) -> None:
    families, _, transactions, family, user, root, source_id = _setup(tmp_path)
    owner = families.repo.list_members(family.id)[0]
    families.create_permission(
        family.id,
        user,
        subject_member_id=owner.id,
        space_id=None,
        action="filesystem.move",
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )
    _, approval = transactions.plan(
        family.id,
        user,
        action="filesystem.move",
        payload={"source_id": source_id, "path": "note.txt", "destination": "moved.txt"},
    )
    assert approval is not None

    transactions.reject(family.id, approval.id, user)

    assert (root / "note.txt").read_text(encoding="utf-8") == "family note"
    assert not (root / "moved.txt").exists()
