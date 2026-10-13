"""巡检计划与调度服务（T13）。

规格依据：
- SRS-02 F1.3 巡检周期配置（每日/每周/自定义 cron、执行时段、启停）
- SRS-02 F5.1 定时巡检（计划按周期触发；任务状态完整；失败可重试）
- SDD-03 §4.3 巡检计划接口、§3 错误码
- SDD-02 §4.4 schedule（cron / interval_type / execute_window / enabled / next_run_at）
- SDD-01 §5.7 调度与定时巡检

设计：
- 存储经 `ScheduleRepository` 接口隔离；当前提供内存实现供开发自测，
  B 的持久化层（`models/` + `db/`）到位后新增 SQLAlchemy 实现即可，服务层不改；
- `next_run_at` 由本服务按 `interval_type` / `cron` / `execute_window` 计算
  （复用 `services.cron`），调度循环（APScheduler）接入时只需消费 `due()` 与 `mark_run()`；
- `interval_type` 语义：`DAILY` 每日窗口起点、`WEEKLY` 每周一窗口起点、
  `MANUAL` 仅手动触发（无 `next_run_at`，且不接受 cron）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, time, timezone
from typing import Any, Callable, Mapping, Protocol

from src.core.errors import ErrorCode
from src.core.exceptions import ServiceError
from src.services.assets import AssetRecord, AssetService
from src.services.cron import next_run, parse_cron, validate_cron

INTERVAL_DAILY = "DAILY"
INTERVAL_WEEKLY = "WEEKLY"
INTERVAL_MANUAL = "MANUAL"
INTERVAL_TYPES = (INTERVAL_DAILY, INTERVAL_WEEKLY, INTERVAL_MANUAL)

DEFAULT_WINDOW_START = time(2, 0)
_WEEKLY_CRON_WEEKDAY = 1  # 周一

_WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ScheduleRecord:
    """巡检计划（对应 SDD-02 schedule）。"""

    name: str
    scope_id: int
    id: int | None = None
    interval_type: str = INTERVAL_DAILY
    cron: str | None = None
    execute_window: str | None = None
    enabled: bool = True
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    deleted: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ScheduleRepository(Protocol):
    """仓储接口；B 的持久化到位后由 SQLAlchemy 实现替换内存实现。"""

    def add(self, record: ScheduleRecord) -> ScheduleRecord: ...
    def get(self, schedule_id: int) -> ScheduleRecord | None: ...
    def list(self, *, include_deleted: bool = False) -> list[ScheduleRecord]: ...
    def save(self, record: ScheduleRecord) -> ScheduleRecord: ...


class InMemoryScheduleRepository:
    """内存实现：供开发自测与单测使用；进程重启即失，不得用于生产。"""

    def __init__(self) -> None:
        self._items: dict[int, ScheduleRecord] = {}
        self._next_id = 1

    def add(self, record: ScheduleRecord) -> ScheduleRecord:
        record.id = self._next_id
        self._next_id += 1
        self._items[record.id] = record
        return record

    def get(self, schedule_id: int) -> ScheduleRecord | None:
        return self._items.get(schedule_id)

    def list(self, *, include_deleted: bool = False) -> list[ScheduleRecord]:
        return [
            record
            for record in self._items.values()
            if include_deleted or not record.deleted
        ]

    def save(self, record: ScheduleRecord) -> ScheduleRecord:
        self._items[record.id] = record
        return record


class ScheduleService:
    """巡检计划服务：计划 CRUD、周期计算、到期查询与手动触发。"""

    def __init__(
        self,
        repository: ScheduleRepository,
        asset_service: AssetService,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._assets = asset_service
        self._now = now or _utcnow

    # ---- 创建与修改（SRS-02 F1.3） ----

    def create(self, payload: Mapping[str, Any]) -> ScheduleRecord:
        name = self._validate_name(payload.get("name"))
        asset = self._require_active_asset(payload.get("scope_id"))
        interval_type = self._validate_interval_type(
            payload.get("interval_type", INTERVAL_DAILY)
        )
        cron = self._validate_cron(payload.get("cron"), interval_type)
        window = self._validate_window(payload.get("execute_window"))

        reference = self._now()
        record = ScheduleRecord(
            name=name,
            scope_id=asset.id,
            interval_type=interval_type,
            cron=cron,
            execute_window=window,
            enabled=True,
            next_run_at=self._compute_next_run(interval_type, cron, window, reference),
            created_at=reference,
            updated_at=reference,
        )
        return self._repository.add(record)

    def update(self, schedule_id: int, payload: Mapping[str, Any]) -> ScheduleRecord:
        record = self._require(schedule_id)

        name = self._validate_name(payload.get("name", record.name))
        interval_type = self._validate_interval_type(
            payload.get("interval_type", record.interval_type)
        )
        cron = self._validate_cron(payload.get("cron", record.cron), interval_type)
        window = self._validate_window(payload.get("execute_window", record.execute_window))
        enabled = bool(payload.get("enabled", record.enabled))

        reference = self._now()
        updated = replace(
            record,
            name=name,
            interval_type=interval_type,
            cron=cron,
            execute_window=window,
            enabled=enabled,
            next_run_at=self._compute_next_run(interval_type, cron, window, reference)
            if enabled
            else None,
            updated_at=reference,
        )
        return self._repository.save(updated)

    def set_enabled(self, schedule_id: int, enabled: bool) -> ScheduleRecord:
        record = self._require(schedule_id)
        reference = self._now()
        updated = replace(
            record,
            enabled=enabled,
            next_run_at=self._compute_next_run(
                record.interval_type, record.cron, record.execute_window, reference
            )
            if enabled
            else None,
            updated_at=reference,
        )
        return self._repository.save(updated)

    def delete(self, schedule_id: int) -> ScheduleRecord:
        record = self._require(schedule_id)
        deleted = replace(
            record,
            deleted=True,
            enabled=False,
            next_run_at=None,
            updated_at=self._now(),
        )
        return self._repository.save(deleted)

    # ---- 查询（SDD-03 §4.3） ----

    def get(self, schedule_id: int) -> ScheduleRecord:
        return self._require(schedule_id)

    def list(self, *, enabled: bool | None = None) -> list[ScheduleRecord]:
        records = self._repository.list()
        if enabled is not None:
            records = [record for record in records if record.enabled == enabled]
        return records

    # ---- 调度（SDD-01 §5.7） ----

    def due(self, reference: datetime | None = None) -> list[ScheduleRecord]:
        """到期应执行的计划；供调度循环（APScheduler 接入点）消费。"""
        reference = reference or self._now()
        return [
            record
            for record in self._repository.list()
            if record.enabled
            and record.next_run_at is not None
            and record.next_run_at <= reference
        ]

    def mark_run(self, schedule_id: int, at: datetime | None = None) -> ScheduleRecord:
        """记录一次执行完成，并据此次时间推进下次执行时间。"""
        record = self._require(schedule_id)
        run_at = at or self._now()
        updated = replace(
            record,
            last_run_at=run_at,
            next_run_at=self._compute_next_run(
                record.interval_type, record.cron, record.execute_window, run_at
            ),
            updated_at=run_at,
        )
        return self._repository.save(updated)

    def trigger_now(self, schedule_id: int) -> ScheduleRecord:
        """手动触发一次（生成检测任务由调用方完成）。"""
        record = self._require(schedule_id)
        if not record.enabled:
            raise ServiceError(
                ErrorCode.STATE_NOT_ALLOWED, f"计划已停用，不能触发：{record.name}"
            )
        return record

    # ---- 内部 ----

    def _require(self, schedule_id: int) -> ScheduleRecord:
        record = self._repository.get(schedule_id)
        if record is None or record.deleted:
            raise ServiceError(ErrorCode.NOT_FOUND, f"巡检计划不存在：{schedule_id}")
        return record

    def _require_active_asset(self, scope_id: Any) -> AssetRecord:
        asset = self._assets.get(scope_id)  # 不存在或已删除 → 40400
        if not asset.active:
            raise ServiceError(
                ErrorCode.UNAUTHORIZED_ASSET,
                f"授权资产已停用，不能创建巡检计划：{asset.domain}",
            )
        return asset

    def _compute_next_run(
        self,
        interval_type: str,
        cron: str | None,
        execute_window: str | None,
        reference: datetime,
    ) -> datetime | None:
        if interval_type == INTERVAL_MANUAL:
            return None

        start = self._window_start(execute_window)
        expression = cron
        if not expression:
            weekdays = "*" if interval_type == INTERVAL_DAILY else str(_WEEKLY_CRON_WEEKDAY)
            expression = f"{start.minute} {start.hour} * * {weekdays}"
        return next_run(parse_cron(expression), reference)

    @staticmethod
    def _validate_name(raw: Any) -> str:
        if not isinstance(raw, str) or not raw.strip():
            raise ServiceError(ErrorCode.INVALID_PARAMETER, "计划名称不能为空")
        return raw.strip()

    @staticmethod
    def _validate_interval_type(raw: Any) -> str:
        if raw not in INTERVAL_TYPES:
            raise ServiceError(
                ErrorCode.INVALID_PARAMETER,
                f"非法周期类型：{raw}（可选 {'/'.join(INTERVAL_TYPES)}）",
            )
        return str(raw)

    @staticmethod
    def _validate_cron(raw: Any, interval_type: str) -> str | None:
        if raw is None or raw == "":
            return None
        if interval_type == INTERVAL_MANUAL:
            raise ServiceError(
                ErrorCode.INVALID_PARAMETER, "手动计划不应配置 cron（不会自动触发）"
            )
        if not isinstance(raw, str) or not validate_cron(raw):
            raise ServiceError(ErrorCode.INVALID_PARAMETER, f"cron 表达式非法：{raw}")
        return raw

    @staticmethod
    def _validate_window(raw: Any) -> str | None:
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise ServiceError(ErrorCode.INVALID_PARAMETER, "执行时段格式非法")
        matched = _WINDOW_RE.match(raw)
        if not matched:
            raise ServiceError(
                ErrorCode.INVALID_PARAMETER, f"执行时段格式非法：{raw}（应为 HH:MM-HH:MM）"
            )
        start = time(int(matched.group(1)), int(matched.group(2)))
        end = time(int(matched.group(3)), int(matched.group(4)))
        if start >= end:
            raise ServiceError(
                ErrorCode.INVALID_PARAMETER, f"执行时段起止颠倒：{raw}"
            )
        return raw

    @staticmethod
    def _window_start(window: str | None) -> time:
        if not window:
            return DEFAULT_WINDOW_START
        matched = _WINDOW_RE.match(window)
        if not matched:  # 理论上不会走到：创建/更新已校验
            return DEFAULT_WINDOW_START
        return time(int(matched.group(1)), int(matched.group(2)))
