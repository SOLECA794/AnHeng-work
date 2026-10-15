"""5 段 cron 求值器。

依据：SRS-02 F1.3 巡检周期配置（每日/每周/自定义 cron）、SDD-02 §4.4 `schedule.cron`、
SDD-01 §5.7 调度与定时巡检。

本机未安装 croniter / APScheduler，故自行实现最小可用的标准 5 段 cron，字段语义遵循 POSIX：

```
分 时 日 月 周
```

- 支持 `*`、`a`、`a-b`、`a,b`、`*/n`、`a-b/n`；
- 周字段 `0`=周日 … `6`=周六，并接受 `7` 作为周日别名；
- 日与周**同时受限**时满足其一即触发（POSIX 语义）。

后续若引入 APScheduler（SDD-01 §2 技术栈），本模块可作为触发时间计算器保留，
也可替换为 `CronTrigger`——替换点集中在本文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

# 搜索上限：覆盖「2 月 29 日」等长周期组合（闰年间隔最长 8 年）
_MAX_LOOKAHEAD_DAYS = 366 * 8

# (字段名, 最小值, 最大值, 是否接受 7 作为周日)
_FIELDS = (
    ("minute", 0, 59, False),
    ("hour", 0, 23, False),
    ("day_of_month", 1, 31, False),
    ("month", 1, 12, False),
    ("day_of_week", 0, 7, True),
)

_ALL_DAYS_OF_MONTH = frozenset(range(1, 32))
_ALL_DAYS_OF_WEEK = frozenset(range(0, 7))


@dataclass(frozen=True)
class CronSpec:
    minutes: frozenset
    hours: frozenset
    days_of_month: frozenset
    months: frozenset
    days_of_week: frozenset

    @property
    def day_of_month_restricted(self) -> bool:
        return self.days_of_month != _ALL_DAYS_OF_MONTH

    @property
    def day_of_week_restricted(self) -> bool:
        return self.days_of_week != _ALL_DAYS_OF_WEEK


def _parse_field(text: str, low: int, high: int, sunday_alias: bool) -> frozenset:
    values: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"cron 字段为空：{text!r}")

        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            step = int(step_text)  # 非数字在此抛 ValueError
            if step < 1:
                raise ValueError(f"cron 步长非法：{step_text!r}")

        if part == "*":
            start, end = low, high
        elif "-" in part:
            start_text, _, end_text = part.partition("-")
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)

        if start < low or end > high or start > end:
            raise ValueError(f"cron 取值越界：{part!r}（允许 {low}-{high}）")
        values.update(range(start, end + 1, step))

    if sunday_alias and 7 in values:
        values.discard(7)
        values.add(0)
    return frozenset(values)


def parse_cron(expression: str) -> CronSpec:
    """解析 5 段 cron 表达式；非法表达式抛 ValueError。"""
    parts = (expression or "").split()
    if len(parts) != 5:
        raise ValueError(f"cron 表达式必须为 5 段（分 时 日 月 周）：{expression!r}")

    minute, hour, day_of_month, month, day_of_week = (
        _parse_field(text, low, high, alias)
        for text, (_, low, high, alias) in zip(parts, _FIELDS)
    )
    return CronSpec(minute, hour, day_of_month, month, day_of_week)


def validate_cron(expression: str) -> bool:
    """表达式是否合法（供接口层做参数校验）。"""
    try:
        parse_cron(expression)
    except ValueError:
        return False
    return True


def _cron_weekday(target: date) -> int:
    """Python 周一=0 → cron 周日=0。"""
    return (target.weekday() + 1) % 7


def _day_matches(spec: CronSpec, target: date) -> bool:
    if target.month not in spec.months:
        return False

    day_of_month_ok = target.day in spec.days_of_month
    day_of_week_ok = _cron_weekday(target) in spec.days_of_week

    if spec.day_of_month_restricted and spec.day_of_week_restricted:
        return day_of_month_ok or day_of_week_ok
    if spec.day_of_month_restricted:
        return day_of_month_ok
    if spec.day_of_week_restricted:
        return day_of_week_ok
    return True


def next_run(spec: CronSpec, after: datetime) -> datetime | None:
    """返回严格晚于 `after` 的下一次触发时间；搜索窗口内无解返回 None。

    保留 `after` 的时区信息（naive 入参得 naive 结果）。
    """
    candidate_date = after.date()
    for _ in range(_MAX_LOOKAHEAD_DAYS):
        if _day_matches(spec, candidate_date):
            for hour in sorted(spec.hours):
                for minute in sorted(spec.minutes):
                    candidate = datetime.combine(
                        candidate_date, time(hour, minute), tzinfo=after.tzinfo
                    )
                    if candidate > after:
                        return candidate
        candidate_date += timedelta(days=1)
    return None
