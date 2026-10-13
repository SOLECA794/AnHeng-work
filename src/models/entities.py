"""ORM entities aligned with SDD-02.

The models intentionally keep status/type values as strings. This lets the
service accept new reviewed values (for example MALICIOUS_SCRIPT) without an
ORM migration solely to extend a Python enum.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, SoftDeleteMixin, TimestampMixin


class SysUser(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "sys_user"
    __table_args__ = (Index("idx_user_role", "role"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(100), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(50))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)


class AuthScope(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "auth_scope"
    __table_args__ = (Index("idx_scope_status", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_value: Mapped[str | None] = mapped_column(String(500))
    auth_note: Mapped[str | None] = mapped_column(String(500))
    owner: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class SitePage(CreatedAtMixin, Base):
    __tablename__ = "site_page"
    __table_args__ = (Index("idx_page_scope", "scope_id"), Index("idx_page_status", "status"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope_id: Mapped[int] = mapped_column(ForeignKey("auth_scope.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_url: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.current_timestamp())
    last_seen_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.current_timestamp())


class Schedule(TimestampMixin, Base):
    __tablename__ = "schedule"
    __table_args__ = (Index("idx_schedule_scope", "scope_id", "enabled"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    scope_id: Mapped[int] = mapped_column(ForeignKey("auth_scope.id"), nullable=False)
    cron: Mapped[str | None] = mapped_column(String(100))
    interval_type: Mapped[str | None] = mapped_column(String(20))
    execute_window: Mapped[str | None] = mapped_column(String(50))
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)
    last_run_at: Mapped[datetime | None] = mapped_column()
    next_run_at: Mapped[datetime | None] = mapped_column()


class DetectionTask(CreatedAtMixin, Base):
    __tablename__ = "detection_task"
    __table_args__ = (Index("idx_task_scope", "scope_id", "status"), Index("idx_task_time", "created_at"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("schedule.id"))
    scope_id: Mapped[int] = mapped_column(ForeignKey("auth_scope.id"), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


class Finding(TimestampMixin, Base):
    __tablename__ = "finding"
    __table_args__ = (
        UniqueConstraint("task_id", "dedup_key", name="uk_finding_dedup"),
        Index("idx_finding_scope", "scope_id"),
        Index("idx_finding_type", "finding_type", "severity"),
        Index("idx_finding_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("detection_task.id"), nullable=False)
    page_id: Mapped[int | None] = mapped_column(ForeignKey("site_page.id"))
    scope_id: Mapped[int] = mapped_column(ForeignKey("auth_scope.id"), nullable=False)
    finding_type: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str | None] = mapped_column(String(500))
    evidence: Mapped[str | None] = mapped_column(Text)
    impact: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    occurrence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)
    first_seen: Mapped[datetime] = mapped_column(nullable=False, server_default=func.current_timestamp())
    last_seen: Mapped[datetime] = mapped_column(nullable=False, server_default=func.current_timestamp())


class Alert(CreatedAtMixin, Base):
    __tablename__ = "alert"
    __table_args__ = (Index("idx_alert_finding", "finding_id"), Index("idx_alert_status", "status"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("finding.id"), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    read_status: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", default=0)


class Baseline(CreatedAtMixin, Base):
    __tablename__ = "baseline"
    __table_args__ = (UniqueConstraint("page_id", name="uk_baseline_page"), Index("idx_baseline_page", "page_id"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("site_page.id"), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SensitiveRule(TimestampMixin, Base):
    __tablename__ = "sensitive_rule"
    __table_args__ = (Index("idx_rule_type", "rule_type", "enabled"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    sample_demo: Mapped[str | None] = mapped_column(Text)
    scope_bind: Mapped[str | None] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)


class Report(CreatedAtMixin, Base):
    __tablename__ = "report"
    __table_args__ = (Index("idx_report_scope", "scope_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope_id: Mapped[int] = mapped_column(ForeignKey("auth_scope.id"), nullable=False)
    period_begin: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    report_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class AgentLog(CreatedAtMixin, Base):
    __tablename__ = "agent_log"
    __table_args__ = (Index("idx_log_user", "user_id"), Index("idx_log_type", "log_type"), Index("idx_log_time", "created_at"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("detection_task.id"))
    log_type: Mapped[str] = mapped_column(String(20), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    params_summary: Mapped[str | None] = mapped_column(String(500))
    result_status: Mapped[str] = mapped_column(String(20), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
