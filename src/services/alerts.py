"""T24 transactional in-app alerts and durable per-risk lifecycle deduplication."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from src.services.finding_verification import Principal, VerificationError, require_principal, _timestamp


class AlertError(VerificationError):
    pass


LEVELS = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")
TITLES = {"VULN": "漏洞风险告警", "WEAK_CONFIG": "弱配置风险告警", "MALICIOUS_LINK": "可疑外链告警",
          "MALICIOUS_SCRIPT": "可疑脚本告警", "TAMPER": "页面篡改告警", "SENSITIVE": "敏感信息告警"}
DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "config" / "alert-policy.json"


@dataclass(frozen=True)
class AlertPolicy:
    version: str = "t24-v1.0.0"
    enabled: bool = True
    min_severity: str = "HIGH"
    always_types: frozenset[str] = frozenset({"TAMPER", "SENSITIVE"})

    def __post_init__(self):
        if (not isinstance(self.version, str) or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}", self.version) is None
                or type(self.enabled) is not bool or not isinstance(self.min_severity, str)
                or self.min_severity not in LEVELS[1:] or not isinstance(self.always_types, frozenset)
                or any(not isinstance(item, str) or item not in TITLES for item in self.always_types)):
            raise AlertError(40001, "告警配置不合法")

    @property
    def sha256(self):
        data = dict(version=self.version, enabled=self.enabled, min_severity=self.min_severity,
                    always_types=sorted(self.always_types))
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def matches(self, kind, severity):
        _enum(kind, TITLES)
        _enum(severity, LEVELS)
        return self.enabled and severity != "INFO" and (
            kind in self.always_types or LEVELS.index(severity) >= LEVELS.index(self.min_severity))


def load_alert_policy(path=DEFAULT_POLICY):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AlertError(40001, "告警配置包含重复字段")
            result[key] = value
        return result
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(8193)
        if len(data) > 8192:
            raise ValueError
        data = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
        if not isinstance(data, dict) or set(data) != {"version", "enabled", "min_severity", "always_types"}:
            raise ValueError
        types = data["always_types"]
        if not isinstance(types, list) or len(types) > len(TITLES) or any(not isinstance(value, str) for value in types) or len(set(types)) != len(types):
            raise ValueError
        return AlertPolicy(data["version"], data["enabled"], data["min_severity"], frozenset(types))
    except (OSError, TypeError, ValueError, UnicodeError, RecursionError):
        raise AlertError(40001, "告警配置不合法") from None


def _id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise AlertError(40001, "标识不合法")
    return value


def _enum(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise AlertError(40001, "枚举参数不合法")
    return value


def _identity(principal, mode):
    if mode != "read":
        return require_principal(principal, write=mode != "list")
    # SDD-03 permits all defined roles to mark read, still within explicit grants.
    if (not isinstance(principal, Principal) or not isinstance(principal.role, str)
            or principal.role not in {"ADMIN", "SEC_OPS", "RULE_ADMIN"}):
        raise AlertError(40100 if principal is None else 40300, "身份或权限无效")
    from dataclasses import replace
    require_principal(replace(principal, role="SEC_OPS"))
    return principal


def _scope(principal, scope):
    if scope.id not in principal.scope_ids or scope.status != "ENABLED" or scope.deleted != 0:
        raise AlertError(40310, "资产未授权或已停用")


def _validate_state(state):
    if (not isinstance(state, dict) or set(state) != {"version", "phase", "cycle", "watermark", "alert_id"}
            or type(state["version"]) is not int or state["version"] != 1
            or state["phase"] not in ("ACTIVE", "RESOLVED") or type(state["cycle"]) is not int
            or not 1 <= state["cycle"] < 2**31):
        raise AlertError(50000, "告警周期数据不合法")
    try:
        if not isinstance(state["watermark"], str) or _timestamp(state["watermark"]) != state["watermark"]:
            raise ValueError
        if state["alert_id"] is not None:
            _id(state["alert_id"])
    except (ValueError, TypeError):
        raise AlertError(50000, "告警周期数据不合法") from None


def alert_view(alert, finding):
    _enum(finding.finding_type, TITLES)
    _enum(alert.severity, LEVELS)
    _enum(alert.status, ("NEW", "HANDLED", "CLOSED"))
    if (type(alert.read_status) is not int or alert.read_status not in (0, 1)
            or not isinstance(alert.created_at, datetime)):
        raise AlertError(50000, "告警数据不合法")
    return dict(id=alert.id, findingId=alert.finding_id, scopeId=finding.scope_id,
                severity=alert.severity, title=TITLES[finding.finding_type],
                content=f"风险 #{alert.finding_id}，级别 {alert.severity}；请核对脱敏证据并处理。",
                status=alert.status, readStatus=alert.read_status,
                createdAt=_timestamp(alert.created_at))


class AlertService:
    def __init__(self, repository, policy=None):
        self.repository = repository
        self.policy = policy if policy is not None else load_alert_policy()
        if not isinstance(self.policy, AlertPolicy):
            raise AlertError(40001, "需要已校验告警配置")

    def process(self, finding_id, principal, *, observed_at=None):
        principal = _identity(principal, "write")
        _id(finding_id)
        at = _timestamp(observed_at)
        try:
            with self.repository.transaction() as tx:
                result = self.process_transaction(tx, finding_id, principal, at)
            return result
        except VerificationError:
            raise
        except Exception:
            raise AlertError(50000, "告警事务失败，请查询当前结果后重试") from None

    def process_transaction(self, tx, finding_id, principal, at):
        """Join an existing authorized transaction (T25); never commits by itself."""
        principal = _identity(principal, "write")
        _id(finding_id)
        scope, finding, anchor = tx.lock_risk(finding_id)
        _scope(principal, scope)
        _enum(finding.finding_type, TITLES)
        _enum(finding.severity, LEVELS)
        _enum(finding.status, ("OPEN", "CONFIRMED", "FALSE_POSITIVE", "FIXED", "RETESTED"))
        raw = dict(anchor.raw_data or {})
        state = raw.get("alerting")
        source_time = finding.updated_at if finding.status in {"FIXED", "RETESTED", "FALSE_POSITIVE"} else finding.last_seen
        if not isinstance(source_time, datetime):
            raise AlertError(50000, "风险来源时间不合法")
        timestamp = _timestamp(source_time)
        if "alerting" in raw:
            _validate_state(state)
            state = dict(state)
            if state["alert_id"] is not None and not tx.alert_belongs(state["alert_id"], scope.id, finding.dedup_key):
                raise AlertError(50000, "告警关联不一致")
        else:
            previous = tx.latest_alert(scope.id, finding.dedup_key)
            state = dict(version=1, phase="ACTIVE", cycle=1, watermark=timestamp,
                         alert_id=previous.id if previous else None)

        alert = None
        if finding.status == "FALSE_POSITIVE":
            outcome = "SUPPRESSED"
        elif timestamp < state["watermark"] or (state["phase"] == "RESOLVED" and finding.status in {"OPEN", "CONFIRMED"} and timestamp <= state["watermark"]):
            outcome = "STALE"
        elif finding.status in {"FIXED", "RETESTED"}:
            state.update(phase="RESOLVED", watermark=timestamp)
            outcome = "RESOLVED"
        else:
            if state["phase"] == "RESOLVED":
                state.update(phase="ACTIVE", cycle=state["cycle"] + 1, alert_id=None)
            state["watermark"] = timestamp
            if state["alert_id"] is not None:
                outcome = "DUPLICATE"
            elif not self.policy.matches(finding.finding_type, finding.severity):
                outcome = "BELOW_THRESHOLD" if self.policy.enabled else "DISABLED"
            else:
                alert = tx.create_alert(finding, at)
                state["alert_id"] = alert.id
                outcome = "ALERT_CREATED"
        _validate_state(state)
        if outcome != "SUPPRESSED":
            raw["alerting"] = state
            tx.save_state(anchor, raw)
        tx.audit(principal, finding, "alert.process", at, dict(outcome=outcome, cycle=state["cycle"],
                 alert_id=state["alert_id"], policy_sha256=self.policy.sha256))
        result = dict(outcome=outcome, findingId=finding.id, scopeId=scope.id, cycle=state["cycle"],
                      alertId=state["alert_id"], notification="IN_APP" if alert else None)
        return result

    def list(self, principal, *, page=1, page_size=20, status=None, severity=None, scope_id=None):
        principal = _identity(principal, "list")
        if type(page) is not int or not 1 <= page <= 100_000 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise AlertError(40001, "分页参数不合法")
        if status is not None:
            _enum(status, ("NEW", "HANDLED", "CLOSED"))
        if severity is not None:
            _enum(severity, LEVELS)
        if scope_id is not None and _id(scope_id) not in principal.scope_ids:
            raise AlertError(40310, "资产未授权")
        try:
            with self.repository.transaction() as tx:
                rows, count = tx.list_alerts(principal.scope_ids, page, page_size, status, severity, scope_id)
                return dict(items=[alert_view(alert, finding) for alert, finding in rows], total=count, page=page, pageSize=page_size)
        except VerificationError:
            raise
        except Exception:
            raise AlertError(50000, "告警查询失败") from None

    def mark_read(self, alert_id, principal, *, observed_at=None):
        return self._manage(alert_id, principal, "read", None, observed_at)

    def set_status(self, alert_id, status, principal, *, observed_at=None):
        return self._manage(alert_id, principal, "write", status, observed_at)

    def _manage(self, alert_id, principal, mode, status, observed_at):
        principal = _identity(principal, mode)
        _id(alert_id)
        at = _timestamp(observed_at)
        if mode == "write":
            _enum(status, ("HANDLED", "CLOSED"))
        try:
            with self.repository.transaction() as tx:
                scope, finding, alert = tx.lock_alert(alert_id)
                _scope(principal, scope)
                alert_view(alert, finding)  # Validate persisted enums before any mutation.
                if mode == "read":
                    changed = alert.read_status == 0
                    alert.read_status = 1
                    action = "alert.read"
                else:
                    if alert.status == "CLOSED" and status != "CLOSED":
                        raise AlertError(40902, "已关闭告警不可重新处理")
                    changed = alert.status != status
                    alert.status = status
                    action = "alert.status"
                if changed:
                    tx.audit(principal, finding, action, at, dict(alert_id=alert.id, status=alert.status, read_status=alert.read_status))
                result = alert_view(alert, finding)
            return result
        except VerificationError:
            raise
        except Exception:
            raise AlertError(50000, "告警事务失败，请查询当前结果后重试") from None
