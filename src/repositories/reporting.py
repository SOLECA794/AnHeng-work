"""SQLAlchemy T26 repository; PostgreSQL consistent snapshot, no schema changes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json

from sqlalchemy import func, select, text

from src.models.entities import AgentLog, AuthScope, DetectionTask, Finding, Report, SitePage
from src.services.finding_verification import VerificationError, _timestamp
from src.services.reporting import MAX_FINDINGS, MAX_TASKS, SHANGHAI, build_snapshot


def metadata(row):
    return {"reportId": row.id, "scopeId": row.scope_id, "periodBegin": row.period_begin.isoformat(),
            "periodEnd": row.period_end.isoformat(), "reportType": row.report_type, "status": row.status,
            "createdAt": _timestamp(row.created_at),
            "filePath": f"/api/reports/{row.id}/file?format=html" if row.status == "DONE" else None,
            "pdfPath": f"/api/reports/{row.id}/file?format=pdf" if row.status == "DONE" else None}


class SqlAlchemyReportRepository:
    def __init__(self, sessions, *, task_timestamp_zone="UTC"):
        if task_timestamp_zone not in {"UTC", "Asia/Shanghai"}:
            raise ValueError("task timestamp zone must be explicitly supported")
        self.sessions = sessions
        self.task_timezone = timezone.utc if task_timestamp_zone == "UTC" else SHANGHAI

    @contextmanager
    def snapshot(self):
        with self.sessions() as session:
            if session.get_bind().dialect.name == "postgresql":
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            elif session.get_bind().dialect.name == "sqlite":
                session.execute(text("BEGIN"))
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def scope(session, scope_id, principal):
        if scope_id not in principal.scope_ids:
            raise VerificationError(40310, "无此资产权限")
        scope = session.get(AuthScope, scope_id)
        if not scope or scope.deleted != 0 or scope.status != "ENABLED":
            raise VerificationError(40310, "资产不存在或已停用")
        return scope

    def report(self, session, identifier, principal):
        row = session.get(Report, identifier)
        if row is None:
            raise VerificationError(40400, "报告不存在")
        self.scope(session, row.scope_id, principal)
        return row

    @staticmethod
    def audit(session, row, principal, action, result="SUCCESS", format=None):
        summary = {"reportId": row.id, "scopeId": row.scope_id}
        if format is not None:
            summary["format"] = format
        session.add(AgentLog(user_id=principal.user_id, log_type="OPERATION", action=action,
                             params_summary=json.dumps(summary, separators=(",", ":")), result_status=result))

    def prepare(self, scope_id, interval, principal, at):
        first, last, begin, end = interval
        begin, end = (value.replace(tzinfo=SHANGHAI).astimezone(self.task_timezone).replace(tzinfo=None) for value in (begin, end))
        with self.snapshot() as session:
            scope = self.scope(session, scope_id, principal)
            tasks = session.scalars(select(DetectionTask).where(
                DetectionTask.scope_id == scope_id, DetectionTask.created_at >= begin,
                DetectionTask.created_at < end).order_by(DetectionTask.id).limit(MAX_TASKS + 1)).all()
            findings = session.scalars(select(Finding).join(DetectionTask, Finding.task_id == DetectionTask.id).where(
                DetectionTask.scope_id == scope_id, DetectionTask.created_at >= begin,
                DetectionTask.created_at < end).order_by(Finding.id).limit(MAX_FINDINGS + 1)).all()
            page_count = session.scalar(select(func.count()).select_from(SitePage).where(SitePage.scope_id == scope_id))
            snapshot = build_snapshot(scope, page_count, tasks, findings, first.isoformat(), last.isoformat(), at,
                                      task_timezone=self.task_timezone)
            row = Report(scope_id=scope_id, period_begin=first, period_end=last, report_type="MANUAL",
                         status="GENERATING", created_at=datetime.fromisoformat(at))
            session.add(row)
            session.flush()
            snapshot["reportId"] = row.id
            self.audit(session, row, principal, "REPORT_BEGIN")
            return row.id, snapshot

    def finish(self, identifier, token, principal):
        with self.sessions.begin() as session:
            row = self.report(session, identifier, principal)
            if row.status != "GENERATING":
                raise VerificationError(40902, "报告状态不允许完成")
            row.file_path, row.status = token, "DONE"
            self.audit(session, row, principal, "REPORT_DONE")
            return metadata(row)

    def fail(self, identifier, principal):
        with self.sessions.begin() as session:
            row = session.get(Report, identifier)
            if row is not None and row.status == "GENERATING":
                row.status, row.file_path = "FAILED", None
                self.audit(session, row, principal, "REPORT_FAILED", "FAILED")

    def get(self, identifier, principal):
        with self.sessions() as session:
            row = self.report(session, identifier, principal)
            return metadata(row), row.file_path

    def downloaded(self, identifier, token, format, principal):
        with self.sessions.begin() as session:
            row = self.report(session, identifier, principal)
            if row.status != "DONE" or row.file_path != token:
                raise VerificationError(40902, "报告状态已变化")
            self.audit(session, row, principal, "REPORT_DOWNLOAD", format=format)

    def list(self, principal, scope_id, page, page_size):
        with self.snapshot() as session:
            if scope_id is not None:
                self.scope(session, scope_id, principal)
            query = select(Report).join(AuthScope, Report.scope_id == AuthScope.id).where(
                Report.scope_id.in_(principal.scope_ids), AuthScope.deleted == 0, AuthScope.status == "ENABLED")
            if scope_id is not None:
                query = query.where(Report.scope_id == scope_id)
            total = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.scalars(query.order_by(Report.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
            return {"total": total, "page": page, "pageSize": page_size, "items": [metadata(row) for row in rows]}
