"""T25 persistence on existing Finding/AgentLog tables, joining T24 transactions."""
from contextlib import contextmanager
from datetime import datetime
import json
import re

from sqlalchemy import func, select, update

from src.models import AgentLog, Finding
from src.repositories.alerts import AlertTransaction
from src.services.retesting import RetestError, RetestSnapshot, fingerprint, receipt, request_key, validate_receipt
from src.services.finding_verification import _timestamp


class RetestTransaction(AlertTransaction):
    def snapshot(self, scope, finding):
        raw = dict(finding.raw_data or {})
        business = {key: value for key, value in raw.items() if key not in {"alerting", "retest_pending"}}
        times = self.session.execute(select(func.max(Finding.last_seen), func.max(Finding.updated_at)).where(
            Finding.scope_id == finding.scope_id, Finding.dedup_key == finding.dedup_key)).first()
        if not all(isinstance(value, datetime) for value in (*times, finding.updated_at, finding.last_seen)):
            raise RetestError(50000, "风险业务时间不合法")
        latest = max(_timestamp(value) for value in times)
        data = [finding.id, finding.task_id, finding.scope_id, finding.finding_type, finding.tool_name,
                finding.dedup_key, finding.status, finding.severity, _timestamp(finding.updated_at),
                _timestamp(finding.last_seen), business, latest, scope.domain, scope.scope_type,
                scope.scope_value, scope.status, scope.deleted, _timestamp(scope.updated_at)]
        identity = raw.get("identity")
        target = identity.get("target_sha256") if isinstance(identity, dict) else None
        return RetestSnapshot(finding.id, finding.scope_id, finding.task_id, finding.finding_type, finding.tool_name,
                              finding.dedup_key, finding.status, fingerprint(data), latest,
                              scope.domain, scope.scope_type, scope.scope_value, target)

    def pending(self, finding):
        raw = finding.raw_data or {}
        if "retest_pending" not in raw:
            return None
        value = raw["retest_pending"]
        if (not isinstance(value, dict) or set(value) != {"request_id", "actor_id", "started_at", "revision", "from_status"}
                or type(value["actor_id"]) is not int or not 0 < value["actor_id"] < 2**63):
            raise RetestError(50000, "复测占用数据不合法")
        request_key(value["request_id"])
        if not isinstance(value["revision"], str) or re.fullmatch(r"[0-9a-f]{64}", value["revision"]) is None or value["from_status"] not in ("OPEN", "CONFIRMED", "FIXED", "RETESTED"):
            raise RetestError(50000, "复测占用快照不合法")
        if not isinstance(value["started_at"], str) or _timestamp(value["started_at"]) != value["started_at"]:
            raise RetestError(50000, "复测占用时间不合法")
        return dict(value)

    def _save_raw(self, finding, raw):
        self.session.execute(update(Finding).where(Finding.id == finding.id).values(raw_data=raw, updated_at=finding.updated_at))

    def reserve(self, finding, key, actor_id, at, snapshot):
        raw = dict(finding.raw_data or {})
        raw["retest_pending"] = dict(request_id=key, actor_id=actor_id, started_at=at,
                                     revision=snapshot.revision, from_status=snapshot.status)
        self._save_raw(finding, raw)
        self.finish_run(finding, key, actor_id, at, receipt(key, snapshot.status, snapshot.status, "RUNNING"))

    def clear_pending(self, finding):
        raw = dict(finding.raw_data or {})
        raw.pop("retest_pending", None)
        self._save_raw(finding, raw)

    def apply_result(self, finding, status, result):
        finding.status = status
        finding.updated_at = datetime.fromisoformat(result.observed_at)
        if result.outcome == "MATCH":
            finding.last_seen = datetime.fromisoformat(result.observed_at)
        self.session.flush()

    def run(self, finding_id, key):
        return self.session.scalar(select(AgentLog).where(AgentLog.log_type == "OPERATION",
            AgentLog.action == f"finding.retest:{finding_id}:{key}").order_by(AgentLog.id.desc()).limit(1))

    def finish_run(self, finding, key, actor_id, at, data):
        validate_receipt(data)
        text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if len(text) > 500:
            raise RetestError(50000, "复测记录超出审计字段限制")
        self.session.add(AgentLog(user_id=actor_id, task_id=finding.task_id, log_type="OPERATION",
            action=f"finding.retest:{finding.id}:{key}", params_summary=text,
            result_status="RUNNING" if data["outcome"] == "RUNNING" else "SUCCESS" if data["error_code"] == 0 else "BLOCKED",
            created_at=datetime.fromisoformat(at)))
        self.session.flush()

    def public_run(self, row, finding, *, replayed=False):
        try:
            data = json.loads(row.params_summary)
            validate_receipt(data)
            expected = "RUNNING" if data["outcome"] == "RUNNING" else "SUCCESS" if data["error_code"] == 0 else "BLOCKED"
            if (row.action != f"finding.retest:{finding.id}:{data['request_id']}" or row.result_status != expected
                    or not isinstance(row.created_at, datetime) or type(row.user_id) is not int or row.user_id < 1
                    or finding.status not in ("OPEN", "CONFIRMED", "FALSE_POSITIVE", "FIXED", "RETESTED")):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise RetestError(50000, "复测历史记录不合法") from None
        return dict(findingId=finding.id, scopeId=finding.scope_id, actorId=row.user_id, currentStatus=finding.status,
                    at=_timestamp(row.created_at), replayed=replayed, **data)

    def history(self, finding_id, limit):
        return self.session.scalars(select(AgentLog).where(AgentLog.log_type == "OPERATION",
            AgentLog.action.like(f"finding.retest:{finding_id}:%")).order_by(AgentLog.id.desc()).limit(limit)).all()

    def scope_findings(self, scope_id, limit, after_id):
        latest = select(func.max(Finding.id)).where(Finding.scope_id == scope_id).group_by(Finding.dedup_key)
        return self.session.scalars(select(Finding.id).where(Finding.id.in_(latest), Finding.id > after_id,
            Finding.status != "FALSE_POSITIVE").order_by(Finding.id).limit(limit)).all()


class SqlAlchemyRetestRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @contextmanager
    def transaction(self):
        with self.session_factory() as session:
            with session.begin():
                if session.get_bind().dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                yield RetestTransaction(session)
