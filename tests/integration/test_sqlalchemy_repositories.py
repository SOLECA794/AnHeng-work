"""T12/T13 的真库持久化集成测试（PostgreSQL 16）。

规格依据：
- SDD-01 §3：`models/`（ORM）+ `db/`（连接与脚本）
- SDD-02 §4.2 `auth_scope`、§4.4 `schedule`、§7 脚本管理
- 约定：仓储接口 + 内存实现用于开发自测；本文验证 **换成 SQLAlchemy 实现后服务层一行不改**

前置：PostgreSQL 可用（默认 Docker 容器 `anheng-pg`，已应用 `db/schema.sql`）：
    docker run -d --name anheng-pg -e POSTGRES_PASSWORD=anheng-dev \\
        -e POSTGRES_DB=anheng -p 5432:5432 postgres:16
    docker exec -i anheng-pg psql -U postgres -d anheng < db/schema.sql

可通过环境变量覆盖连接串：ANHENG_TEST_DB_URL

**关键验证点**：ORM 的时间列是 `TIMESTAMP WITHOUT TIME ZONE`（返回 naive），而服务层用
aware UTC 时钟。仓储必须负责转换，否则 `ScheduleService.due()` 会在比较时抛 TypeError——
这正是「换实现不动服务层」这一约定最容易被破坏的地方。

运行：python tests/integration/test_sqlalchemy_repositories.py -v
"""

import os
import pathlib
import sys
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from src.core.errors import ErrorCode  # noqa: E402
from src.core.exceptions import ServiceError  # noqa: E402
from src.services.assets import AssetService  # noqa: E402
from src.services.persistence import (  # noqa: E402
    SqlAlchemyAssetRepository,
    SqlAlchemyScheduleRepository,
)
from src.services.schedules import ScheduleService  # noqa: E402

DB_URL = os.environ.get(
    "ANHENG_TEST_DB_URL",
    "postgresql+psycopg2://postgres:anheng-dev@127.0.0.1:5432/anheng",
)
NOW = datetime(2026, 10, 14, 10, 30, tzinfo=timezone.utc)


class _DbTestCase(unittest.TestCase):
    engine = None
    factory = None

    @classmethod
    def setUpClass(cls):
        if _DbTestCase.engine is None:
            try:
                engine = create_engine(DB_URL)
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
            except Exception as exc:  # 无库时跳过，而不是报错
                raise unittest.SkipTest(f"PostgreSQL 不可达（{DB_URL}）：{exc}")
            _DbTestCase.engine = engine
            _DbTestCase.factory = sessionmaker(bind=engine, expire_on_commit=False)
        cls.engine = _DbTestCase.engine
        cls.factory = _DbTestCase.factory

    def setUp(self):
        with self.engine.begin() as conn:
            conn.execute(text("TRUNCATE auth_scope, schedule RESTART IDENTITY CASCADE"))

    def _assets(self) -> AssetService:
        # 每次都新建仓储与仓储会话工厂，模拟新的进程/请求
        return AssetService(SqlAlchemyAssetRepository(self.factory))

    def _schedules(self, assets: AssetService) -> ScheduleService:
        return ScheduleService(
            SqlAlchemyScheduleRepository(self.factory), assets, now=lambda: NOW
        )


class AssetPersistenceTest(_DbTestCase):
    def test_created_asset_survives_a_fresh_repository(self):
        created = self._assets().create({"domain": "example.com"})
        # 全新仓储实例仍能读到 → 证明真的落库，而非进程内存储
        self.assertEqual(self._assets().get(created.id).domain, "example.com")

    def test_all_scope_fields_round_trip(self):
        created = self._assets().create(
            {
                "domain": "localhost",
                "scope_type": "PATH",
                "scope_value": "/dvwa",
                "auth_note": "本机自建靶场",
                "owner": "安全部",
            }
        )
        reread = self._assets().get(created.id)
        self.assertEqual(reread.scope_type, "PATH")
        self.assertEqual(reread.scope_value, "/dvwa")
        self.assertEqual(reread.auth_note, "本机自建靶场")
        self.assertEqual(reread.owner, "安全部")
        self.assertEqual(reread.status, "ENABLED")
        self.assertFalse(reread.deleted)

    def test_duplicate_domain_is_rejected_against_database_state(self):
        self._assets().create({"domain": "example.com"})
        with self.assertRaises(ServiceError) as ctx:
            self._assets().create({"domain": "EXAMPLE.com"})
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_OPERATION)

    def test_update_persists(self):
        created = self._assets().create({"domain": "example.com"})
        self._assets().update(created.id, {"owner": "运维部", "scope_type": "SUBDOMAIN"})
        self.assertEqual(self._assets().get(created.id).owner, "运维部")

    def test_disable_marks_deleted_and_hides_from_list(self):
        created = self._assets().create({"domain": "example.com"})
        self._assets().disable(created.id)
        fresh = self._assets()
        self.assertEqual(fresh.list(), [])
        with self.assertRaises(ServiceError) as ctx:
            fresh.get(created.id)
        self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    def test_authorization_boundary_survives_reload(self):
        self._assets().create(
            {"domain": "localhost", "scope_type": "PATH", "scope_value": "/dvwa"}
        )
        fresh = self._assets()
        self.assertTrue(fresh.is_authorized("http://localhost/dvwa/login.php"))
        self.assertFalse(fresh.is_authorized("http://localhost/other"))
        with self.assertRaises(ServiceError) as ctx:
            fresh.ensure_authorized("http://evil.test/")
        self.assertEqual(ctx.exception.code, ErrorCode.UNAUTHORIZED_ASSET)


class SchedulePersistenceTest(_DbTestCase):
    def setUp(self):
        super().setUp()
        self.asset = self._assets().create({"domain": "example.com"})

    def test_schedule_persists_with_next_run(self):
        record = self._schedules(self._assets()).create(
            {"name": "每日巡检", "scope_id": self.asset.id, "execute_window": "02:00-06:00"}
        )
        reread = self._schedules(self._assets()).get(record.id)
        self.assertEqual(reread.name, "每日巡检")
        self.assertEqual(reread.next_run_at, datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc))

    def test_due_compares_db_timestamps_with_the_aware_service_clock(self):
        """DB 列是 naive，服务层是 aware UTC——仓储必须转换，否则这里会抛 TypeError。"""
        schedules = self._schedules(self._assets())
        schedules.create({"name": "每日巡检", "scope_id": self.asset.id})
        fresh = self._schedules(self._assets())
        self.assertEqual(fresh.due(NOW), [])
        self.assertEqual(len(fresh.due(datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc))), 1)

    def test_disable_clears_next_run_in_database(self):
        schedules = self._schedules(self._assets())
        record = schedules.create({"name": "每日巡检", "scope_id": self.asset.id})
        schedules.set_enabled(record.id, False)
        self.assertIsNone(self._schedules(self._assets()).get(record.id).next_run_at)

    def test_mark_run_persists_last_run_and_advances_next_run(self):
        schedules = self._schedules(self._assets())
        record = schedules.create({"name": "每日巡检", "scope_id": self.asset.id})
        run_at = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
        schedules.mark_run(record.id, run_at)
        reread = self._schedules(self._assets()).get(record.id)
        self.assertEqual(reread.last_run_at, run_at)
        self.assertEqual(reread.next_run_at, datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc))

    def test_delete_hides_schedule_from_list(self):
        schedules = self._schedules(self._assets())
        record = schedules.create({"name": "每日巡检", "scope_id": self.asset.id})
        schedules.delete(record.id)
        self.assertEqual(self._schedules(self._assets()).list(), [])

    def test_schedule_rejects_disabled_asset_from_database(self):
        self._assets().update(self.asset.id, {"status": "DISABLED"})
        with self.assertRaises(ServiceError) as ctx:
            self._schedules(self._assets()).create(
                {"name": "x", "scope_id": self.asset.id}
            )
        self.assertEqual(ctx.exception.code, ErrorCode.UNAUTHORIZED_ASSET)


if __name__ == "__main__":
    unittest.main(verbosity=2)
