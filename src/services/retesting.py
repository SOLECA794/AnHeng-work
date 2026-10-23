"""T25 fresh retesting with durable reservations and atomic alert lifecycle updates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from uuid import UUID, uuid4, uuid5

from src.services.finding_verification import VerificationError, require_principal, _timestamp
from src.services.alerts import AlertService, _scope


class RetestError(VerificationError):
    pass


@dataclass(frozen=True)
class RetestSnapshot:
    finding_id: int
    scope_id: int
    task_id: int
    kind: str
    tool_name: str
    dedup_key: str
    status: str
    revision: str
    latest_at: str
    domain: str
    scope_type: str
    scope_value: str | None
    target_sha256: str | None


@dataclass(frozen=True)
class RetestResult:
    outcome: str
    observed_at: str | None = None
    source_sha256: str | None = None
    rules_sha256: str | None = None
    coverage_complete: bool = False


_SHA = re.compile(r"[0-9a-f]{64}\Z")
_STATUSES = {"OPEN", "CONFIRMED", "FALSE_POSITIVE", "FIXED", "RETESTED"}
_OUTCOMES = {"RUNNING", "MATCH", "CLEAN", "INCONCLUSIVE", "CONFLICT", "EXPIRED"}
_ALERTS = {"ALERT_CREATED", "DUPLICATE", "RESOLVED", "STALE", "SUPPRESSED", "DISABLED", "BELOW_THRESHOLD"}


def request_key(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError):
        raise RetestError(40001, "复测请求编号不合法") from None


def fingerprint(value):
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        if len(payload) > 1_000_000:
            raise ValueError
        return hashlib.sha256(payload.encode("ascii")).hexdigest()
    except (TypeError, ValueError, RecursionError):
        raise RetestError(50000, "复测来源元数据不合法") from None


def validate_receipt(data):
    fields = {"request_id", "from_status", "status", "outcome", "observed_at", "source_sha256", "rules_sha256", "alert_id", "alert_outcome", "error_code"}
    if not isinstance(data, dict) or set(data) - fields or not {"request_id", "from_status", "status", "outcome", "error_code"} <= set(data):
        raise RetestError(50000, "复测历史数据不合法")
    request_key(data["request_id"])
    if (not isinstance(data["status"], str) or data["status"] not in _STATUSES
            or not isinstance(data["from_status"], str) or data["from_status"] not in _STATUSES
            or not isinstance(data["outcome"], str) or data["outcome"] not in _OUTCOMES
            or type(data["error_code"]) is not int or data["error_code"] not in {0, 40001, 40310, 40902, 40903, 50000, 50002}):
        raise RetestError(50000, "复测历史枚举不合法")
    for name in ("source_sha256", "rules_sha256"):
        if name in data and (not isinstance(data[name], str) or not _SHA.fullmatch(data[name])):
            raise RetestError(50000, "复测历史摘要不合法")
    if "observed_at" in data and (not isinstance(data["observed_at"], str) or _timestamp(data["observed_at"]) != data["observed_at"]):
        raise RetestError(50000, "复测历史时间不合法")
    if "alert_id" in data and (type(data["alert_id"]) is not int or not 0 < data["alert_id"] < 2**63):
        raise RetestError(50000, "复测历史告警编号不合法")
    if "alert_outcome" in data and (not isinstance(data["alert_outcome"], str) or data["alert_outcome"] not in _ALERTS):
        raise RetestError(50000, "复测历史告警结果不合法")
    outcome = data["outcome"]
    success = data["error_code"] == 0
    if (outcome in {"MATCH", "CLEAN"} and (not success or not {"observed_at", "source_sha256", "rules_sha256"} <= set(data))
            or outcome == "MATCH" and data["status"] != "CONFIRMED"
            or outcome == "CLEAN" and data["status"] not in {"FIXED", "RETESTED"}
            or outcome == "RUNNING" and (not success or data["status"] != data["from_status"])
            or outcome in {"CONFLICT", "INCONCLUSIVE", "EXPIRED"} and success):
        raise RetestError(50000, "复测历史状态不一致")


def receipt(key, before, status, outcome, *, error=0, result=None, alert=None):
    data = dict(request_id=key, from_status=before, status=status, outcome=outcome, error_code=error)
    if result is not None:
        data.update(observed_at=result.observed_at, source_sha256=result.source_sha256, rules_sha256=result.rules_sha256)
    if alert:
        data.update(alert_id=alert["alertId"], alert_outcome=alert["outcome"])
    data = {name: value for name, value in data.items() if value is not None}
    validate_receipt(data)
    return data


def validate_result(result, snapshot, started, completed):
    if not isinstance(result, RetestResult) or result.outcome not in ("MATCH", "CLEAN") or result.coverage_complete is not True:
        raise RetestError(40903, "复测未取得完整确定结果，保留原状态")
    if any(not isinstance(value, str) or not _SHA.fullmatch(value) for value in (result.source_sha256, result.rules_sha256)):
        raise RetestError(40903, "复测缺少有效来源或规则摘要")
    observed = _timestamp(result.observed_at) if result.observed_at is not None else ""
    if not observed or observed != result.observed_at or not started <= observed <= completed or observed <= snapshot.latest_at:
        raise RetestError(40903, "复测观测时间不满足新鲜度要求")


class RetestService:
    def __init__(self, repository, runner=None, *, alert_policy=None, clock=None):
        self.repository, self.runner = repository, runner
        self.alerts = AlertService(repository, alert_policy)
        self.clock = clock

    def now(self):
        return _timestamp(self.clock() if self.clock else None)

    def retest(self, finding_id, principal, *, request_id=None):
        principal = require_principal(principal)
        if type(finding_id) is not int or not 0 < finding_id < 2**63:
            raise RetestError(40001, "风险编号不合法")
        key = request_key(request_id if request_id is not None else str(uuid4()))
        started = self.now()
        try:
            with self.repository.transaction() as tx:
                scope, finding, _ = tx.lock_risk(finding_id)
                _scope(principal, scope)
                if finding.status not in _STATUSES:
                    raise RetestError(50000, "风险状态不合法")
                previous = tx.run(finding_id, key)
                if previous:
                    if previous.user_id != principal.user_id:
                        raise RetestError(40902, "复测请求属于其他处理人")
                    if previous.result_status != "RUNNING":
                        return tx.public_run(previous, finding, replayed=True)
                pending = tx.pending(finding)
                if previous and previous.result_status == "RUNNING" and not pending:
                    raise RetestError(40902, "复测执行记录与占用不一致，请先核查")
                if pending:
                    age = (datetime.fromisoformat(started) - datetime.fromisoformat(pending["started_at"])).total_seconds()
                    if age < 300:
                        raise RetestError(40902, "该风险正在复测，请稍后查询")
                    tx.finish_run(finding, pending["request_id"], pending["actor_id"], started,
                                  receipt(pending["request_id"], pending["from_status"], finding.status, "EXPIRED", error=40903))
                    tx.clear_pending(finding)
                    if pending["request_id"] == key:
                        return tx.public_run(tx.run(finding_id, key), finding, replayed=True)
                if finding.status == "FALSE_POSITIVE":
                    raise RetestError(40902, "误报风险不参与修复复测")
                snapshot = tx.snapshot(scope, finding)
                tx.reserve(finding, key, principal.user_id, started, snapshot)
            # No asset/database lock is held while the detector runs.
            try:
                result = self.runner.run(snapshot) if self.runner else RetestResult("INCONCLUSIVE")
                runner_error = None
            except Exception:
                result, runner_error = None, RetestError(50002, "复测执行失败，保留原状态")
            completed = self.now()
            failure = runner_error
            with self.repository.transaction() as tx:
                scope, finding, _ = tx.lock_risk(finding_id)
                pending = tx.pending(finding)
                if (not pending or pending["request_id"] != key or pending["actor_id"] != principal.user_id
                        or pending["revision"] != snapshot.revision or pending["from_status"] != snapshot.status
                        or pending["started_at"] != started):
                    raise RetestError(40902, "复测请求已过期或被替换")
                try:
                    _scope(principal, scope)
                    if tx.snapshot(scope, finding).revision != snapshot.revision:
                        raise RetestError(40902, "检测期间风险或资产已变化，请重新复测")
                    if failure:
                        raise failure
                    validate_result(result, snapshot, started, completed)
                except VerificationError as error:
                    failure = error
                if failure:
                    data = receipt(key, snapshot.status, finding.status, "CONFLICT" if failure.code in {40902, 40310} else "INCONCLUSIVE", error=failure.code)
                    tx.clear_pending(finding)
                else:
                    status = "CONFIRMED" if result.outcome == "MATCH" else "RETESTED" if snapshot.status in {"FIXED", "RETESTED"} else "FIXED"
                    tx.apply_result(finding, status, result)
                    tx.clear_pending(finding)
                    alert = self.alerts.process_transaction(tx, finding.id, principal, completed)
                    data = receipt(key, snapshot.status, status, result.outcome, result=result, alert=alert)
                tx.finish_run(finding, key, principal.user_id, completed, data)
                response = tx.public_run(tx.run(finding_id, key), finding)
            if failure:
                raise failure
            return response
        except VerificationError:
            raise
        except Exception:
            raise RetestError(50000, "复测事务失败，请查询执行记录后重试") from None

    def history(self, finding_id, principal, *, limit=50):
        principal = require_principal(principal, write=False)
        if type(finding_id) is not int or not 0 < finding_id < 2**63 or type(limit) is not int or not 1 <= limit <= 100:
            raise RetestError(40001, "查询参数不合法")
        try:
            with self.repository.transaction() as tx:
                scope, finding, _ = tx.lock_risk(finding_id)
                _scope(principal, scope)
                return dict(findingId=finding.id, scopeId=scope.id, items=[tx.public_run(row, finding) for row in tx.history(finding_id, limit)])
        except VerificationError:
            raise
        except Exception:
            raise RetestError(50000, "复测历史查询失败") from None

    def retest_scope(self, scope_id, principal, *, request_id=None, limit=20, after_id=0):
        principal = require_principal(principal)
        key = request_key(request_id if request_id is not None else str(uuid4()))
        if type(scope_id) is not int or not 0 < scope_id < 2**63 or type(limit) is not int or not 1 <= limit <= 20 or type(after_id) is not int or not 0 <= after_id < 2**63:
            raise RetestError(40001, "批量复测参数不合法")
        try:
            with self.repository.transaction() as tx:
                scope = tx._scope_lock(scope_id)
                _scope(principal, scope)
                identifiers = tx.scope_findings(scope_id, limit, after_id)
        except VerificationError:
            raise
        except Exception:
            raise RetestError(50000, "批量复测查询失败") from None
        results = []
        for identifier in identifiers:
            try:
                results.append(self.retest(identifier, principal, request_id=str(uuid5(UUID(key), str(identifier)))))
            except VerificationError as error:
                results.append(dict(findingId=identifier, errorCode=error.code, message="该项复测未完成，请查询历史"))
        return dict(scopeId=scope_id, requestId=key, items=results, nextAfterId=identifiers[-1] if identifiers else None)
