"""SQLAlchemy 仓储实现（T12/T13）。

规格依据：
- SDD-01 §3：`models/`（ORM，由 B 交付）+ `db/`（连接与脚本）
- SDD-02 §4.2 `auth_scope`、§4.4 `schedule`、§7 脚本管理

约定：服务层只依赖仓储**接口**（`AssetRepository` / `ScheduleRepository`），
内存实现用于开发自测，本模块提供落库实现——**服务层一行不改**。

两处必须由仓储层吸收的差异，否则「换实现不动服务层」这条约定会被破坏：

1. **时间**：ORM 的时间列是 `TIMESTAMP WITHOUT TIME ZONE`（读出为 naive），
   而服务层用 aware UTC 时钟比较（如 `ScheduleService.due()`）。
   读写两侧都要转换，否则会抛 `TypeError: can't compare offset-naive and offset-aware`。
2. **布尔**：`deleted` / `enabled` 在库里是 `INTEGER`（0/1），服务层是 `bool`。

另外 `schedule` 表**没有 `deleted` 列**，故计划的删除是真 DELETE（见 `ScheduleService.delete`）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.models import AuthScope, Schedule
from src.services.assets import AssetRecord
from src.services.schedules import ScheduleRecord


def _to_aware_utc(value: datetime | None) -> datetime | None:
    """库里是 naive（无时区），读出时按 UTC 补全，供服务层与 aware 时钟比较。"""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _to_naive_utc(value: datetime | None) -> datetime | None:
    """写入前统一归到 UTC 并去掉时区（列是 WITHOUT TIME ZONE）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _timestamps(created_at: datetime | None, updated_at: datetime | None) -> dict[str, Any]:
    """仅在服务层确实给了时间时才写入，避免覆盖列的 server_default。"""
    values: dict[str, Any] = {}
    if created_at is not None:
        values["created_at"] = _to_naive_utc(created_at)
    if updated_at is not None:
        values["updated_at"] = _to_naive_utc(updated_at)
    return values


class SqlAlchemyAssetRepository:
    """授权资产落库（SDD-02 §4.2 `auth_scope`）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, record: AssetRecord) -> AssetRecord:
        with self._session_factory() as session:
            orm = AuthScope(
                domain=record.domain,
                scope_type=record.scope_type,
                scope_value=record.scope_value,
                auth_note=record.auth_note,
                owner=record.owner,
                status=record.status,
                deleted=1 if record.deleted else 0,
                **_timestamps(record.created_at, record.updated_at),
            )
            session.add(orm)
            session.commit()
            session.refresh(orm)
            return self._to_record(orm)

    def get(self, asset_id: int) -> AssetRecord | None:
        with self._session_factory() as session:
            orm = session.get(AuthScope, asset_id)
            return self._to_record(orm) if orm is not None else None

    def find_by_domain(self, domain: str) -> AssetRecord | None:
        # 含已逻辑删除记录：库上 domain 有 UNIQUE 约束，重复录入要能提前判定为 40901
        with self._session_factory() as session:
            orm = session.scalars(
                select(AuthScope).where(AuthScope.domain == domain)
            ).first()
            return self._to_record(orm) if orm is not None else None

    def list(self, *, include_deleted: bool = False) -> list[AssetRecord]:
        statement = select(AuthScope).order_by(AuthScope.id)
        if not include_deleted:
            statement = statement.where(AuthScope.deleted == 0)
        with self._session_factory() as session:
            return [self._to_record(orm) for orm in session.scalars(statement)]

    def save(self, record: AssetRecord) -> AssetRecord:
        with self._session_factory() as session:
            orm = session.get(AuthScope, record.id)
            if orm is None:
                raise KeyError(f"授权资产不存在：{record.id}")
            orm.domain = record.domain
            orm.scope_type = record.scope_type
            orm.scope_value = record.scope_value
            orm.auth_note = record.auth_note
            orm.owner = record.owner
            orm.status = record.status
            orm.deleted = 1 if record.deleted else 0
            if record.updated_at is not None:
                orm.updated_at = _to_naive_utc(record.updated_at)
            session.commit()
            session.refresh(orm)
            return self._to_record(orm)

    @staticmethod
    def _to_record(orm: AuthScope) -> AssetRecord:
        return AssetRecord(
            id=orm.id,
            domain=orm.domain,
            scope_type=orm.scope_type,
            scope_value=orm.scope_value,
            auth_note=orm.auth_note,
            owner=orm.owner,
            status=orm.status,
            deleted=bool(orm.deleted),
            created_at=_to_aware_utc(orm.created_at),
            updated_at=_to_aware_utc(orm.updated_at),
        )


class SqlAlchemyScheduleRepository:
    """巡检计划落库（SDD-02 §4.4 `schedule`）。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add(self, record: ScheduleRecord) -> ScheduleRecord:
        with self._session_factory() as session:
            orm = Schedule(
                name=record.name,
                scope_id=record.scope_id,
                cron=record.cron,
                interval_type=record.interval_type,
                execute_window=record.execute_window,
                enabled=1 if record.enabled else 0,
                last_run_at=_to_naive_utc(record.last_run_at),
                next_run_at=_to_naive_utc(record.next_run_at),
                **_timestamps(record.created_at, record.updated_at),
            )
            session.add(orm)
            session.commit()
            session.refresh(orm)
            return self._to_record(orm)

    def get(self, schedule_id: int) -> ScheduleRecord | None:
        with self._session_factory() as session:
            orm = session.get(Schedule, schedule_id)
            return self._to_record(orm) if orm is not None else None

    def list(self) -> list[ScheduleRecord]:
        with self._session_factory() as session:
            statement = select(Schedule).order_by(Schedule.id)
            return [self._to_record(orm) for orm in session.scalars(statement)]

    def save(self, record: ScheduleRecord) -> ScheduleRecord:
        with self._session_factory() as session:
            orm = session.get(Schedule, record.id)
            if orm is None:
                raise KeyError(f"巡检计划不存在：{record.id}")
            orm.name = record.name
            orm.scope_id = record.scope_id
            orm.cron = record.cron
            orm.interval_type = record.interval_type
            orm.execute_window = record.execute_window
            orm.enabled = 1 if record.enabled else 0
            orm.last_run_at = _to_naive_utc(record.last_run_at)
            orm.next_run_at = _to_naive_utc(record.next_run_at)
            if record.updated_at is not None:
                orm.updated_at = _to_naive_utc(record.updated_at)
            session.commit()
            session.refresh(orm)
            return self._to_record(orm)

    def remove(self, schedule_id: int) -> None:
        """真删除（`schedule` 表无 `deleted` 列）。"""
        with self._session_factory() as session:
            orm = session.get(Schedule, schedule_id)
            if orm is not None:
                session.delete(orm)
                session.commit()

    @staticmethod
    def _to_record(orm: Schedule) -> ScheduleRecord:
        return ScheduleRecord(
            id=orm.id,
            name=orm.name,
            scope_id=orm.scope_id,
            cron=orm.cron,
            interval_type=orm.interval_type,
            execute_window=orm.execute_window,
            enabled=bool(orm.enabled),
            last_run_at=_to_aware_utc(orm.last_run_at),
            next_run_at=_to_aware_utc(orm.next_run_at),
            created_at=_to_aware_utc(orm.created_at),
            updated_at=_to_aware_utc(orm.updated_at),
        )
