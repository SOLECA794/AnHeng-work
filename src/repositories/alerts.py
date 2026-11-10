"""T24 persistence; asset-row serialization covers risk state, inbox and audit."""
from contextlib import contextmanager
from datetime import datetime
import json
import re

from sqlalchemy import func, select, update

from src.models import AgentLog, Alert, AuthScope, Finding
from src.services.alerts import AlertError, TITLES


class AlertTransaction:
    def __init__(self, session):
        self.session = session

    def _scope_lock(self, scope_id):
        scope = self.session.scalar(select(AuthScope).where(AuthScope.id == scope_id).with_for_update())
        if scope is None:
            raise AlertError(40400, "资产不存在")
        return scope

    def lock_risk(self, finding_id):
        scope_id = self.session.scalar(select(Finding.scope_id).where(Finding.id == finding_id))
        if scope_id is None:
            raise AlertError(40400, "风险不存在")
        scope = self._scope_lock(scope_id)
        finding = self.session.scalar(select(Finding).where(Finding.id == finding_id).with_for_update())
        if finding is None or finding.scope_id != scope_id:
            raise AlertError(40902, "风险身份已变化")
        if not isinstance(finding.dedup_key, str) or re.fullmatch(r"[0-9a-f]{32}", finding.dedup_key) is None:
            raise AlertError(40001, "风险去重身份不合法")
        identity = (Finding.scope_id == scope_id, Finding.dedup_key == finding.dedup_key)
        mismatch = self.session.scalar(select(Finding.id).where(*identity, Finding.finding_type != finding.finding_type).limit(1))
        if mismatch is not None:
            raise AlertError(40902, "风险身份存在类型冲突")
        anchor = self.session.scalar(select(Finding).where(*identity).order_by(Finding.id).limit(1).with_for_update())
        if anchor.raw_data is not None and not isinstance(anchor.raw_data, dict):
            raise AlertError(50000, "风险元数据不合法")
        return scope, finding, anchor

    def alert_belongs(self, alert_id, scope_id, key):
        return self.session.scalar(select(Alert.id).join(Finding, Finding.id == Alert.finding_id).where(
            Alert.id == alert_id, Finding.scope_id == scope_id, Finding.dedup_key == key)) is not None

    def latest_alert(self, scope_id, key):
        return self.session.scalar(select(Alert).join(Finding, Finding.id == Alert.finding_id).where(
            Finding.scope_id == scope_id, Finding.dedup_key == key).order_by(Alert.id.desc()).limit(1))

    def save_state(self, anchor, raw):
        # Alert metadata must not move the Finding's actual verification/fix timestamp.
        self.session.execute(update(Finding).where(Finding.id == anchor.id).values(
            raw_data=raw, updated_at=anchor.updated_at))

    def create_alert(self, finding, at):
        alert = Alert(finding_id=finding.id, severity=finding.severity, title=TITLES[finding.finding_type],
                      content=f"风险 #{finding.id}，级别 {finding.severity}；请核对脱敏证据并处理。",
                      status="NEW", read_status=0, created_at=datetime.fromisoformat(at))
        self.session.add(alert)
        self.session.flush()
        return alert

    def audit(self, principal, finding, action, at, values):
        if action not in {"alert.process", "alert.read", "alert.status"}:
            raise ValueError("invalid alert audit action")
        summary = dict(finding_id=finding.id, scope_id=finding.scope_id, **values)
        if set(summary) - {"finding_id", "scope_id", "outcome", "cycle", "alert_id", "policy_sha256", "status", "read_status"}:
            raise ValueError("invalid alert audit fields")
        text = json.dumps(summary, sort_keys=True, separators=(",", ":"))
        if len(text) > 500:
            raise ValueError("alert audit too long")
        self.session.add(AgentLog(user_id=principal.user_id, task_id=finding.task_id, log_type="OPERATION",
                                 action=action, params_summary=text, result_status="SUCCESS", created_at=datetime.fromisoformat(at)))
        self.session.flush()

    def lock_alert(self, alert_id):
        scope_id = self.session.scalar(select(Finding.scope_id).join(Alert, Alert.finding_id == Finding.id).where(Alert.id == alert_id))
        if scope_id is None:
            raise AlertError(40400, "告警不存在")
        scope = self._scope_lock(scope_id)
        row = self.session.execute(select(Alert, Finding).join(Finding, Alert.finding_id == Finding.id).where(
            Alert.id == alert_id, Finding.scope_id == scope_id).with_for_update()).first()
        if row is None:
            raise AlertError(40902, "告警关联已变化")
        return scope, row[1], row[0]

    def list_alerts(self, scopes, page, page_size, status, severity, scope_id):
        conditions = [Finding.scope_id.in_(sorted(scopes)), AuthScope.status == "ENABLED", AuthScope.deleted == 0]
        if status is not None:
            conditions.append(Alert.status == status)
        if severity is not None:
            conditions.append(Alert.severity == severity)
        if scope_id is not None:
            conditions.append(Finding.scope_id == scope_id)
        query = select(Alert, Finding).join(Finding, Alert.finding_id == Finding.id).join(AuthScope, Finding.scope_id == AuthScope.id).where(*conditions)
        count = self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = self.session.execute(query.order_by(Alert.created_at.desc(), Alert.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
        return rows, count


class SqlAlchemyAlertRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @contextmanager
    def transaction(self):
        with self.session_factory() as session:
            with session.begin():
                if session.get_bind().dialect.name == "sqlite":
                    # SQLite ignores FOR UPDATE: acquire the DB writer reservation before reading.
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                yield AlertTransaction(session)
