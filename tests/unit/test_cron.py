"""5 段 cron 求值器单测。

规格依据：SRS-02 F1.3 巡检周期配置（每日/每周/自定义 cron）、SDD-02 §4.4 schedule.cron、
SDD-01 §5.7 调度与定时巡检（计划按时触发）。

说明：本机未安装 croniter/APScheduler，故自行实现最小可用的标准 5 段 cron 求值，
避免为一个字段引入外部依赖；字段语义遵循 POSIX cron。

运行：python tests/unit/test_cron.py -v
"""

import pathlib
import sys
import unittest
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.cron import next_run, parse_cron  # noqa: E402


def _after(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M")


class CronParseTest(unittest.TestCase):
    def test_parses_wildcard_fields(self):
        spec = parse_cron("* * * * *")
        self.assertEqual(len(spec.minutes), 60)
        self.assertEqual(len(spec.hours), 24)
        self.assertEqual(len(spec.months), 12)

    def test_parses_lists_ranges_and_steps(self):
        spec = parse_cron("*/15 9-17 1,15 * *")
        self.assertEqual(spec.minutes, frozenset({0, 15, 30, 45}))
        self.assertEqual(spec.days_of_month, frozenset({1, 15}))
        self.assertIn(9, spec.hours)
        self.assertIn(17, spec.hours)

    def test_step_within_range(self):
        self.assertEqual(parse_cron("0 9-17/4 * * *").hours, frozenset({9, 13, 17}))

    def test_maps_sunday_alias_seven_to_zero(self):
        self.assertEqual(parse_cron("0 0 * * 7").days_of_week, frozenset({0}))

    def test_rejects_wrong_field_count(self):
        for expression in ("* * * *", "* * * * * *", ""):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    parse_cron(expression)

    def test_rejects_out_of_range_values(self):
        for expression in ("60 * * * *", "* 24 * * *", "* * 32 * *", "* * * 13 *", "* * * * 8"):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    parse_cron(expression)

    def test_rejects_non_numeric_values(self):
        with self.assertRaises(ValueError):
            parse_cron("0 2 * * abc")

    def test_rejects_reversed_range(self):
        with self.assertRaises(ValueError):
            parse_cron("0 17-9 * * *")


class CronNextRunTest(unittest.TestCase):
    def test_every_minute_advances_one_minute(self):
        self.assertEqual(
            next_run(parse_cron("* * * * *"), _after("2026-10-13 10:30")),
            _after("2026-10-13 10:31"),
        )

    def test_daily_job_later_today(self):
        self.assertEqual(
            next_run(parse_cron("0 2 * * *"), _after("2026-10-13 01:00")),
            _after("2026-10-13 02:00"),
        )

    def test_daily_job_rolls_to_tomorrow_when_passed(self):
        self.assertEqual(
            next_run(parse_cron("0 2 * * *"), _after("2026-10-13 10:30")),
            _after("2026-10-14 02:00"),
        )

    def test_result_is_strictly_after_reference_time(self):
        self.assertEqual(
            next_run(parse_cron("30 1 * * *"), _after("2026-10-13 01:30")),
            _after("2026-10-14 01:30"),
        )

    def test_monthly_job_picks_first_of_next_month(self):
        self.assertEqual(
            next_run(parse_cron("0 0 1 * *"), _after("2026-10-13 10:30")),
            _after("2026-11-01 00:00"),
        )

    def test_day_of_week_monday(self):
        # 2026-10-13 是周二；下一个周一为 2026-10-19
        self.assertEqual(
            next_run(parse_cron("0 3 * * 1"), _after("2026-10-13 10:30")),
            _after("2026-10-19 03:00"),
        )

    def test_day_of_month_or_day_of_week_uses_either(self):
        # 13 号 或 周五：2026-10-13 是周二，最近的周五是 10-16
        self.assertEqual(
            next_run(parse_cron("0 0 13 * 5"), _after("2026-10-13 10:30")),
            _after("2026-10-16 00:00"),
        )

    def test_returns_none_for_impossible_schedule(self):
        # 2 月 31 日永远不存在
        self.assertIsNone(next_run(parse_cron("0 2 31 2 *"), _after("2026-10-13 10:30")))

    def test_preserves_timezone_awareness(self):
        from datetime import timezone

        aware = datetime(2026, 10, 13, 10, 30, tzinfo=timezone.utc)
        result = next_run(parse_cron("0 2 * * *"), aware)
        self.assertEqual(result.tzinfo, timezone.utc)
        self.assertEqual(result, datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
