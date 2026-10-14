"""SQLAlchemy persistence for T22; status and success audit share a transaction."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json

from sqlalchemy import exists, select, update

from src.models.entities import AgentLog, AuthScope, Finding
from src.services.finding_verification import FindingSnapshot, REASONS, validate_audit_event


_EVENT_FIELDS = {"finding_id", "scope_id", "task_id", "actor_id", "from_status", "to_status",
                 "method", "reason_code", "reason", "at", "note_sha256", "review_sha256", "result", "error_code"}
_SUMMARY_FIELDS = ("finding_id", "scope_id", "from_status", "to_status", "method", "reason_code",
                   "note_sha256", "review_sha256", "error_code")


class SqlAlchemyVerificationTransaction:
    def __init__(self, session):
        self.session = session

    def get_finding(self, finding_id) -> FindingSnapshot | None:
        row = self.session.execute(select(
            Finding.id, Finding.task_id, Finding.scope_id, Finding.status, Finding.raw_data,
            AuthScope.status.label("scope_status"), AuthScope.deleted.label("scope_deleted"),
        ).join(AuthScope, AuthScope.id == Finding.scope_id).where(Finding.id == finding_id)).mappings().first()
        if row is None:
            return None
        raw = row["raw_data"]
        return FindingSnapshot(row["id"], row["task_id"], row["scope_id"], row["status"],
                               row["scope_status"] == "ENABLED" and row["scope_deleted"] == 0,
                               raw.get("source_sha256") if isinstance(raw, dict) else None)

    def compare_and_set(self, finding_id, scope_id, expected_status, new_status, at, *, source_sha256=None) -> bool:
        statement = update(Finding).where(
            Finding.id == finding_id, Finding.scope_id == scope_id, Finding.status == expected_status,
            exists(select(AuthScope.id).where(AuthScope.id == scope_id, AuthScope.status == "ENABLED", AuthScope.deleted == 0)),
        )
        if source_sha256 is not None:
            statement = statement.where(Finding.raw_data["source_sha256"].as_string() == source_sha256)
        result = self.session.execute(statement.values(status=new_status, updated_at=datetime.fromisoformat(at)))
        return result.rowcount == 1

    def add_audit(self, event) -> None:
        if set(event) != _EVENT_FIELDS:
            raise ValueError("invalid verification event fields")
        validate_audit_event(event)
        summary = {key: event[key] for key in _SUMMARY_FIELDS if event[key] is not None}
        text = json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if len(text) > 500:
            raise ValueError("verification audit exceeds the existing column limit")
        finding_id = event["finding_id"]
        self.session.add(AgentLog(
            user_id=event["actor_id"], task_id=event["task_id"], log_type="OPERATION", tool_name=None,
            action=f"finding.verify:{finding_id}" if finding_id is not None else "finding.verify:invalid",
            params_summary=text, result_status=event["result"], created_at=datetime.fromisoformat(event["at"]),
        ))
        self.session.flush()  # Audit failures must roll back the pending status update.

    def list_audits(self, finding_id, limit) -> list[dict]:
        rows = self.session.scalars(select(AgentLog).where(
            AgentLog.action == f"finding.verify:{finding_id}", AgentLog.log_type == "OPERATION",
        ).order_by(AgentLog.id.desc()).limit(limit)).all()
        events = []
        for row in rows:
            summary = json.loads(row.params_summary or "{}")
            if not isinstance(summary, dict) or set(summary) - set(_SUMMARY_FIELDS):
                raise ValueError("unexpected verification audit metadata")
            event = {key: summary.get(key) for key in _SUMMARY_FIELDS}
            event.update(actor_id=row.user_id, task_id=row.task_id, result=row.result_status,
                         at=row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                         reason=REASONS.get(summary.get("reason_code"), "未执行状态变更"))
            validate_audit_event(event)
            if event["finding_id"] != finding_id:
                raise ValueError("verification audit belongs to another finding")
            events.append(event)
        return events


class SqlAlchemyVerificationRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @contextmanager
    def transaction(self):
        with self.session_factory() as session:
            with session.begin():
                yield SqlAlchemyVerificationTransaction(session)
