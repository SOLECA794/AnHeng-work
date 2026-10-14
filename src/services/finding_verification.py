"""T22 manual verification and same-source rule review with atomic audit writes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re


class VerificationError(ValueError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class Principal:
    """Trusted authentication result; never construct this from request body/headers."""
    user_id: int
    role: str
    scope_ids: frozenset[int]
    active: bool = True


@dataclass(frozen=True)
class RuleReview:
    outcome: str
    source_sha256: str
    reviewed_sha256: str
    rule_version: str


@dataclass(frozen=True)
class FindingSnapshot:
    finding_id: int
    task_id: int
    scope_id: int
    status: str
    scope_enabled: bool = True
    source_sha256: str | None = None


REASONS = {
    "EVIDENCE_REVIEWED": "已核对证据并确认风险",
    "EXPECTED_PUBLIC_CONTENT": "内容已获批准公开，不属于泄露",
    "RULE_FALSE_MATCH": "原始证据不符合规则，属于误报",
    "AUTHORIZED_CHANGE": "已核对属于授权变更",
    "RULE_REVIEW_MATCH": "同源证据按指定规则版本复核仍命中",
    "RULE_REVIEW_FALSE_MATCH": "同源证据复核确认原规则错误匹配",
}
_MANUAL_REASONS = {
    "CONFIRMED": {"EVIDENCE_REVIEWED"},
    "FALSE_POSITIVE": {"EXPECTED_PUBLIC_CONTENT", "RULE_FALSE_MATCH", "AUTHORIZED_CHANGE"},
}
_STATUSES = {"OPEN", "CONFIRMED", "FALSE_POSITIVE", "FIXED", "RETESTED"}
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SHANGHAI = timezone(timedelta(hours=8))


def _id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**63


def require_principal(principal, *, write: bool = True) -> Principal:
    if not isinstance(principal, Principal) or not _id(principal.user_id) or principal.active is not True:
        raise VerificationError(40100, "身份缺失或无效")
    if not isinstance(principal.scope_ids, frozenset) or any(not _id(value) for value in principal.scope_ids):
        raise VerificationError(40100, "授权上下文无效")
    if principal.role not in (("SEC_OPS",) if write else ("SEC_OPS", "RULE_ADMIN")):
        raise VerificationError(40300, "无此操作权限")
    return principal


def _timestamp(value=None) -> str:
    try:
        if value is None:
            value = datetime.now(_SHANGHAI)
        if isinstance(value, str):
            if len(value) < 19:
                raise ValueError
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            raise ValueError
        if value.tzinfo is not None:
            value = value.astimezone(_SHANGHAI).replace(tzinfo=None)
        return value.replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError):
        raise VerificationError(40001, "处理时间不合法") from None


def _digest(value: str) -> str:
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        raise VerificationError(40001, "文本编码不合法") from None


def _event(finding_id, principal, snapshot, at, *, method, status=None, reason_code=None,
           note_sha256=None, review_sha256=None, result="SUCCESS", error_code=0):
    return {
        "finding_id": finding_id if _id(finding_id) else None,
        "scope_id": snapshot.scope_id if snapshot else None,
        "task_id": snapshot.task_id if snapshot else None,
        "actor_id": principal.user_id if isinstance(principal, Principal) and _id(principal.user_id) else None,
        "from_status": snapshot.status if snapshot and snapshot.status in _STATUSES else None,
        "to_status": status if isinstance(status, str) and status in _STATUSES else None,
        "method": method, "reason_code": reason_code if isinstance(reason_code, str) and reason_code in REASONS else None,
        "reason": REASONS.get(reason_code, "未执行状态变更") if isinstance(reason_code, str) else "未执行状态变更",
        "at": at, "note_sha256": note_sha256, "review_sha256": review_sha256,
        "result": result, "error_code": error_code,
    }


def validate_audit_event(event) -> None:
    """Reject legacy/corrupt payloads before persisting or exporting audit metadata."""
    fields = {"finding_id", "scope_id", "task_id", "actor_id", "from_status", "to_status",
              "method", "reason_code", "reason", "at", "note_sha256", "review_sha256", "result", "error_code"}
    if not isinstance(event, dict) or set(event) != fields:
        raise ValueError("invalid verification audit fields")
    for key in ("finding_id", "scope_id", "task_id", "actor_id"):
        if event[key] is not None and not _id(event[key]):
            raise ValueError("invalid audit identifier")
    for key in ("from_status", "to_status"):
        if event[key] is not None and (not isinstance(event[key], str) or event[key] not in _STATUSES):
            raise ValueError("invalid audit state")
    if not isinstance(event["method"], str) or event["method"] not in {"MANUAL", "RULE_REVIEW", "READ"}:
        raise ValueError("invalid audit method")
    reason = event["reason_code"]
    if reason is not None and (not isinstance(reason, str) or reason not in REASONS):
        raise ValueError("invalid audit reason")
    if event["reason"] != REASONS.get(reason, "未执行状态变更"):
        raise ValueError("invalid audit reason label")
    if not isinstance(event["at"], str) or _timestamp(event["at"]) != event["at"]:
        raise ValueError("invalid audit timestamp")
    for key in ("note_sha256", "review_sha256"):
        if event[key] is not None and (not isinstance(event[key], str) or not _SHA.fullmatch(event[key])):
            raise ValueError("invalid audit fingerprint")
    if (not isinstance(event["error_code"], int) or isinstance(event["error_code"], bool)
        or event["error_code"] not in {0, 40001, 40100, 40300, 40310, 40400, 40902, 40903, 50000}):
        raise ValueError("invalid audit error code")
    if event["result"] == "SUCCESS":
        permitted = (_MANUAL_REASONS if event["method"] == "MANUAL" else {
            "CONFIRMED": {"RULE_REVIEW_MATCH"}, "FALSE_POSITIVE": {"RULE_REVIEW_FALSE_MATCH"},
        } if event["method"] == "RULE_REVIEW" else {})
        if (any(event[key] is None for key in ("finding_id", "scope_id", "task_id", "actor_id"))
            or event["from_status"] != "OPEN" or event["to_status"] not in permitted
            or reason not in permitted[event["to_status"]] or event["error_code"] != 0
            or (event["method"] == "RULE_REVIEW" and event["review_sha256"] is None)):
            raise ValueError("inconsistent successful verification audit")
    elif event["result"] != "BLOCKED" or event["error_code"] == 0:
        raise ValueError("invalid blocked verification audit")


class VerificationService:
    """Repository transactions own commit/rollback; exceptions never expose DB payloads."""
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _load(tx, finding_id, principal):
        if not _id(finding_id):
            raise VerificationError(40001, "风险编号不合法")
        snapshot = tx.get_finding(finding_id)
        if snapshot is None:
            raise VerificationError(40400, "风险不存在")
        if snapshot.scope_id not in principal.scope_ids or not snapshot.scope_enabled:
            raise VerificationError(40310, "资产未授权或已停用")
        return snapshot

    def _blocked(self, finding_id, principal, snapshot, at, method, error, *, status=None,
                 reason_code=None, note_sha256=None, review_sha256=None):
        event = _event(finding_id, principal, snapshot, at, method=method, status=status,
                       reason_code=reason_code, note_sha256=note_sha256, review_sha256=review_sha256,
                       result="BLOCKED", error_code=error.code)
        try:
            with self.repository.transaction() as tx:
                tx.add_audit(event)
        except Exception:
            raise VerificationError(50000, "审计写入失败，操作未完成") from None

    def verify(self, finding_id, status, reason_code, principal, *, note=None,
               observed_at=None, expected_status="OPEN") -> dict:
        return self._change(finding_id, principal, observed_at, status=status,
                            reason_code=reason_code, note=note, expected_status=expected_status)

    def review(self, finding_id, review: RuleReview, principal, *, observed_at=None) -> dict:
        """Accept a trusted offline reviewer result for the original archived source only."""
        return self._change(finding_id, principal, observed_at, review=review, method="RULE_REVIEW")

    def _change(self, finding_id, principal, observed_at, *, status=None, reason_code=None,
                note=None, expected_status="OPEN", review=None, method="MANUAL"):
        snapshot, note_hash, review_hash = None, None, None
        at = _timestamp()  # A safe timestamp is also available for malformed requests' audit.
        try:
            principal = require_principal(principal)
            at = _timestamp(observed_at)
            if note is not None:
                if not isinstance(note, str) or len(note) > 1000:
                    raise VerificationError(40001, "备注格式不合法")
                note_hash = _digest(note)
            with self.repository.transaction() as tx:
                snapshot = self._load(tx, finding_id, principal)
                if expected_status != "OPEN" or snapshot.status != "OPEN":
                    raise VerificationError(40902, "当前状态不允许验证")
                if method == "RULE_REVIEW":
                    if (not isinstance(review, RuleReview) or not isinstance(review.outcome, str)
                        or review.outcome not in {"MATCH", "FALSE_MATCH", "INCONCLUSIVE"}):
                        raise VerificationError(40001, "规则复核结果不合法")
                    if any(not isinstance(value, str) or not _SHA.fullmatch(value) for value in (review.source_sha256, review.reviewed_sha256)):
                        raise VerificationError(40001, "证据指纹不合法")
                    if not isinstance(review.rule_version, str) or not review.rule_version.strip() or len(review.rule_version) > 64:
                        raise VerificationError(40001, "规则版本不合法")
                    review_hash = _digest(json.dumps({"outcome": review.outcome, "source": review.source_sha256,
                                                    "reviewed": review.reviewed_sha256, "rule_version": review.rule_version},
                                                   sort_keys=True, separators=(",", ":")))
                    if (snapshot.source_sha256 != review.source_sha256 or review.source_sha256 != review.reviewed_sha256
                        or review.outcome == "INCONCLUSIVE"):
                        raise VerificationError(40903, "缺少同源确定证据，保留待确认状态")
                    status, reason_code = (("CONFIRMED", "RULE_REVIEW_MATCH") if review.outcome == "MATCH"
                                           else ("FALSE_POSITIVE", "RULE_REVIEW_FALSE_MATCH"))
                elif (not isinstance(status, str) or status not in _MANUAL_REASONS
                      or not isinstance(reason_code, str) or reason_code not in _MANUAL_REASONS[status]):
                    raise VerificationError(40001, "目标状态或处理理由不合法")
                expected_source = {"source_sha256": review.source_sha256} if method == "RULE_REVIEW" else {}
                if not tx.compare_and_set(finding_id, snapshot.scope_id, "OPEN", status, at, **expected_source):
                    raise VerificationError(40902, "风险已被其他操作处理")
                event = _event(finding_id, principal, snapshot, at, method=method, status=status,
                               reason_code=reason_code, note_sha256=note_hash, review_sha256=review_hash)
                tx.add_audit(event)
            return {"findingId": finding_id, "scopeId": snapshot.scope_id, "taskId": snapshot.task_id,
                    "status": status, "verification": event}
        except VerificationError as error:
            self._blocked(finding_id, principal, snapshot, at, method, error, status=status,
                          reason_code=reason_code, note_sha256=note_hash, review_sha256=review_hash)
            raise
        except Exception:
            raise VerificationError(50000, "验证事务失败，请查询当前状态后重试") from None

    def get(self, finding_id, principal, *, limit=100) -> dict:
        snapshot = None
        at = _timestamp()
        try:
            principal = require_principal(principal, write=False)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
                raise VerificationError(40001, "查询条数不合法")
            with self.repository.transaction() as tx:
                snapshot = self._load(tx, finding_id, principal)
                trail = tx.list_audits(finding_id, limit)
            return {"findingId": finding_id, "scopeId": snapshot.scope_id, "taskId": snapshot.task_id,
                    "status": snapshot.status, "trail": trail}
        except VerificationError as error:
            self._blocked(finding_id, principal, snapshot, at, "READ", error)
            raise
        except Exception:
            raise VerificationError(50000, "验证记录读取失败") from None
