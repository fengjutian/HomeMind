"""Diagnostic: trace idempotent plan."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r'D:\github\HomeMind\src')

from homemind.infra.db.migrate import run_migrations as run_hm
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from homemind.infra.db.repos.family_transactions import FamilyTransactionRepo
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.db.repos.family_tasks import FamilyTaskRepo
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transactions import FamilyTransactionManager
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import Role, User
from homemind.infra.family.permissions import PermissionEffect


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        pool = SqlitePool(Path(d) / "x.db")
        run_migrations(pool)
        run_hm(pool)
        with pool.connect() as c:
            c.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) VALUES (1, 'o', 'x', 'user', 0, 'zh', 1)"
            )
        family_repo = FamilyRepo(pool)
        fm = FamilyManager(family_repo)
        cr = FamilyContextRepo(pool)
        cm = FamilyContextManager(fm, cr)
        tm = FamilyTaskManager(fm, FamilyTaskRepo(pool))
        user_repo = UserRepo(pool)
        txn_repo = FamilyTransactionRepo(pool)
        txn_mgr = FamilyTransactionManager(fm, cm, tm, txn_repo, user_repo)
        owner = User(id=1, username="o", role=Role.USER, display_name="O")
        fam = fm.create_family(owner, name="F", timezone="UTC", locale="zh")
        member = fm.repo.list_members(fam.id)[0]
        fm.create_permission(
            fam.id, owner,
            subject_member_id=member.id, space_id=None,
            action="task.create", effect=PermissionEffect.REQUIRE_CONFIRMATION,
            expires_at=None,
        )
        t1, a1 = txn_mgr.plan(
            fam.id, owner, action="task.create",
            payload={"title": "x"}, idempotency_key="k1",
        )
        print(f"first txn: {t1.id} status={t1.status}")
        print("rows after first plan:")
        with pool.connect() as c:
            for r in c.execute(
                "SELECT transaction_id, status, idempotency_key FROM homemind_family_transactions"
            ).fetchall():
                print(f"  {dict(r)}")
        try:
            t2, a2 = txn_mgr.plan(
                fam.id, owner, action="task.create",
                payload={"title": "x"}, idempotency_key="k1",
            )
            print(f"second txn: {t2.id} status={t2.status}")
        except Exception as e:
            print(f"second plan failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
