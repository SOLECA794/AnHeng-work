"""巡检计划与调度服务单测（T13）。

规格依据：
- SRS-02 F1.3 巡检周期配置（每日/每周/自定义 cron、执行时段、启停）
- SRS-02 F5.1 定时巡检（计划按周期触发；任务状态完整；失败可重试）
- SDD-03 §4.3 巡检计划接口、§3 错误码（40902 状态不允许）
- SDD-02 §4.4 schedule（cron / interval_type / execute_window / enabled / next_run_at）
- SDD-01 §5.7 调度与定时巡检

说明：B 的持久化层未产出，按仓储接口注入内存实现；B 到位后只加 SQLAlchemy 实现。
时间通过注入的 `now` 固定，保证 next_run_at 断言可复现。

运行：python tests/unit/test_schedule_service.py -v
"""

import pathlib
import sys
import unittest
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.errors import ErrorCode  # noqa: E402
from src.core.exceptions import ServiceError  # noqa: E402
from src.services.assets import AssetService, InMemoryAssetRepository  # noqa: E402
from src.services.schedules import (  # noqa: E402
    InMemoryScheduleRepository,
    ScheduleService,
)

# 2026-10-13 是周二
NOW = datetime(2026, 10, 13, 10, 30)


def _asset_service() -> AssetService:
    service = AssetService(InMemoryAssetRepository())
    service.create({"domain": "example.com"})
    service.create({"domain": "localhost", "scope_type": "PATH", "scope_value": "/dvwa"})
    return service


class _Base(unittest.TestCase):
    def setUp(self):
        self.assets = _asset_service()
        self.service = ScheduleService(
            InMemoryScheduleRepository(), self.assets, now=lambda: NOW
        )
        self.scope_id = self.assets.list()[0].id


class ScheduleCreateTest(_Base):
    def test_creates_daily_schedule_with_next_run(self):
        record = self.service.create(
            {"name": "每日巡检", "scope_id": self.scope_id, "execute_window": "02:00-06:00"}
        )
        self.assertEqual(record.interval_type, "DAILY")
        self.assertTrue(record.enabled)
        self.assertEqual(record.next_run_at, datetime(2026, 10, 14, 2, 0))

    def test_daily_window_later_today_stays_today(self):
        record = self.service.create(
            {"name": "晚间巡检", "scope_id": self.scope_id, "execute_window": "23:00-23:30"}
        )
        self.assertEqual(record.next_run_at, datetime(2026, 10, 13, 23, 0))

    def test_weekly_schedule_lands_on_monday(self):
        record = self.service.create(
            {"name": "每周巡检", "scope_id": self.scope_id, "interval_type": "WEEKLY"}
        )
        self.assertEqual(record.next_run_at, datetime(2026, 10, 19, 2, 0))

    def test_manual_schedule_has_no_next_run(self):
        record = self.service.create(
            {"name": "手动巡检", "scope_id": self.scope_id, "interval_type": "MANUAL"}
        )
        self.assertIsNone(record.next_run_at)

    def test_cron_schedule_computes_next_run(self):
        record = self.service.create(
            {"name": "自定义巡检", "scope_id": self.scope_id, "cron": "0 3 * * 0"}
        )
        # 2026-10-13 周二 → 下一个周日 2026-10-18 03:00
        self.assertEqual(record.next_run_at, datetime(2026, 10, 18, 3, 0))

    def test_rejects_missing_name(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.create({"scope_id": self.scope_id})
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)

    def test_rejects_unregistered_scope_with_40400(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.create({"name": "x", "scope_id": 999})
        self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    def test_rejects_disabled_asset_with_40310(self):
        asset = self.assets.list()[0]
        self.assets.update(asset.id, {"status": "DISABLED"})
        with self.assertRaises(ServiceError) as ctx:
            self.service.create({"name": "x", "scope_id": asset.id})
        self.assertEqual(ctx.exception.code, ErrorCode.UNAUTHORIZED_ASSET)

    def test_rejects_unknown_interval_type(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.create(
                {"name": "x", "scope_id": self.scope_id, "interval_type": "HOURLY"}
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)

    def test_rejects_invalid_cron(self):
        for cron in ("0 2 * *", "60 2 * * *", "0 2 * * abc"):
            with self.subTest(cron=cron):
                with self.assertRaises(ServiceError) as ctx:
                    self.service.create(
                        {"name": "x", "scope_id": self.scope_id, "cron": cron}
                    )
                self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)

    def test_rejects_cron_on_manual_schedule(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.create(
                {
                    "name": "x",
                    "scope_id": self.scope_id,
                    "interval_type": "MANUAL",
                    "cron": "0 2 * * *",
                }
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)

    def test_rejects_malformed_execute_window(self):
        for window in ("2:00-6:00", "02:00", "06:00-02:00", "aa:bb-cc:dd"):
            with self.subTest(window=window):
                with self.assertRaises(ServiceError) as ctx:
                    self.service.create(
                        {"name": "x", "scope_id": self.scope_id, "execute_window": window}
                    )
                self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)


class ScheduleQueryTest(_Base):
    def setUp(self):
        super().setUp()
        self.record = self.service.create({"name": "每日巡检", "scope_id": self.scope_id})

    def test_get_returns_record(self):
        self.assertEqual(self.service.get(self.record.id).name, "每日巡检")

    def test_get_missing_raises_40400(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.get(999)
        self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    def test_list_returns_all(self):
        self.service.create({"name": "第二个", "scope_id": self.scope_id})
        self.assertEqual(len(self.service.list()), 2)

    def test_list_filters_by_enabled(self):
        self.service.set_enabled(self.record.id, False)
        self.assertEqual(self.service.list(enabled=True), [])
        self.assertEqual(len(self.service.list(enabled=False)), 1)


class ScheduleUpdateTest(_Base):
    def setUp(self):
        super().setUp()
        self.record = self.service.create({"name": "每日巡检", "scope_id": self.scope_id})

    def test_updates_name(self):
        self.assertEqual(self.service.update(self.record.id, {"name": "改名"}).name, "改名")

    def test_changing_cron_recomputes_next_run(self):
        updated = self.service.update(self.record.id, {"cron": "0 5 * * *"})
        self.assertEqual(updated.cron, "0 5 * * *")
        self.assertEqual(updated.next_run_at, datetime(2026, 10, 14, 5, 0))

    def test_update_missing_raises_40400(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.update(999, {"name": "x"})
        self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    def test_update_validates_cron(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.update(self.record.id, {"cron": "bad"})
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_PARAMETER)

    def test_disabling_clears_next_run(self):
        disabled = self.service.set_enabled(self.record.id, False)
        self.assertFalse(disabled.enabled)
        self.assertIsNone(disabled.next_run_at)

    def test_re_enabling_recomputes_next_run(self):
        self.service.set_enabled(self.record.id, False)
        enabled = self.service.set_enabled(self.record.id, True)
        self.assertTrue(enabled.enabled)
        self.assertEqual(enabled.next_run_at, datetime(2026, 10, 14, 2, 0))


class ScheduleDispatchTest(_Base):
    def setUp(self):
        super().setUp()
        self.record = self.service.create({"name": "每日巡检", "scope_id": self.scope_id})

    def test_due_returns_schedules_at_or_before_reference(self):
        self.assertEqual(self.service.due(NOW), [])
        self.assertEqual(len(self.service.due(datetime(2026, 10, 14, 2, 0))), 1)

    def test_due_excludes_disabled_schedules(self):
        self.service.set_enabled(self.record.id, False)
        self.assertEqual(self.service.due(datetime(2026, 10, 20, 0, 0)), [])

    def test_due_excludes_manual_schedules(self):
        self.service.create(
            {"name": "手动", "scope_id": self.scope_id, "interval_type": "MANUAL"}
        )
        self.assertEqual(len(self.service.due(datetime(2026, 10, 20, 0, 0))), 1)

    def test_mark_run_updates_last_run_and_advances_next_run(self):
        run_at = datetime(2026, 10, 14, 2, 0)
        updated = self.service.mark_run(self.record.id, run_at)
        self.assertEqual(updated.last_run_at, run_at)
        self.assertEqual(updated.next_run_at, datetime(2026, 10, 15, 2, 0))

    def test_trigger_now_returns_schedule(self):
        self.assertEqual(self.service.trigger_now(self.record.id).id, self.record.id)

    def test_trigger_now_on_disabled_schedule_raises_40902(self):
        self.service.set_enabled(self.record.id, False)
        with self.assertRaises(ServiceError) as ctx:
            self.service.trigger_now(self.record.id)
        self.assertEqual(ctx.exception.code, ErrorCode.STATE_NOT_ALLOWED)


class ScheduleDeleteTest(_Base):
    def setUp(self):
        super().setUp()
        self.record = self.service.create({"name": "每日巡检", "scope_id": self.scope_id})

    def test_delete_marks_deleted(self):
        self.assertTrue(self.service.delete(self.record.id).deleted)

    def test_deleted_schedule_is_not_found_afterwards(self):
        self.service.delete(self.record.id)
        with self.assertRaises(ServiceError) as ctx:
            self.service.get(self.record.id)
        self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    def test_delete_missing_raises_40400(self):
        with self.assertRaises(ServiceError) as ctx:
            self.service.delete(999)
        self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    def test_deleted_schedule_disappears_from_list(self):
        self.service.delete(self.record.id)
        self.assertEqual(self.service.list(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
