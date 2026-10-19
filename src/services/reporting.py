"""T26 bounded, frozen inspection reports. No untrusted source text is exported."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import re

from src.services.finding_verification import VerificationError, _id, _timestamp, require_principal

MAX_TASKS = MAX_FINDINGS = 1000
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
STATUSES = ("OPEN", "CONFIRMED", "FALSE_POSITIVE", "FIXED", "RETESTED")
TEMPLATES = {
    "SENSITIVE": ("敏感信息暴露", "可能泄露个人信息或系统凭据。", "移除公开敏感内容，轮换已暴露凭据，修正访问控制并复测。"),
    "VULN": ("漏洞风险", "可能影响应用或数据安全。", "核实漏洞证据，升级修补受影响组件并复测。"),
    "WEAK_CONFIG": ("配置风险", "不安全配置可能扩大攻击面。", "按最小权限加固配置，关闭无用服务并复测。"),
    "TAMPER": ("页面篡改", "页面完整性可能受损。", "核对授权变更，恢复可信版本，排查入侵来源并复测。"),
    "MALICIOUS_LINK": ("恶意外链", "可能将访问者导向不可信站点。", "移除异常链接，排查注入来源，更新基线并复测。"),
    "MALICIOUS_SCRIPT": ("恶意脚本", "可能在访问者浏览器执行异常代码。", "隔离异常脚本，排查注入点，恢复可信资源并复测。"),
}
SHA = re.compile(r"[0-9a-f]{64}\Z")
SHANGHAI = timezone(timedelta(hours=8))


def period(begin, end):
    try:
        if not all(isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v, flags=re.ASCII) for v in (begin, end)):
            raise ValueError
        first, last = date.fromisoformat(begin), date.fromisoformat(end)
        if last < first or (last - first).days >= 366 or last == date.max:
            raise ValueError
        return first, last, datetime.combine(first, time()), datetime.combine(last + timedelta(days=1), time())
    except (TypeError, ValueError, OverflowError):
        raise VerificationError(40001, "日期须为 YYYY-MM-DD，首尾包含且不超过 366 天") from None


def pagination(page, size):
    if not _id(page) or page > 100000 or not _id(size) or size > 100:
        raise VerificationError(40001, "分页参数不合法")
    return page, size


def build_snapshot(scope, page_count, tasks, findings, begin, end, at, *, task_timezone=timezone.utc):
    """Input ORM objects belong to a single repository read snapshot."""
    task_ids = {t.id for t in tasks}
    if len(tasks) > MAX_TASKS or len(findings) > MAX_FINDINGS:
        raise VerificationError(40903, "数据超过报告上限，请缩小时间范围")
    # Domain is the one intended asset identifier; never copy arbitrary scope notes.
    domain = scope.domain
    if not isinstance(domain, str) or not re.fullmatch(r"[A-Za-z0-9.-]{1,100}", domain):
        raise VerificationError(40903, "资产域名不符合报告契约")
    groups = defaultdict(list)
    daily_tasks, daily_keys = Counter(), defaultdict(set)
    safe_tasks = []
    for task in tasks:
        if task.status not in {"QUEUED", "DISCOVERING", "SCANNING", "ANALYZING", "DONE", "FAILED"}:
            raise VerificationError(40903, "任务状态不符合报告契约")
        task_stamp = _timestamp(task.created_at.replace(tzinfo=task_timezone) if task.created_at.tzinfo is None else task.created_at)
        day = task_stamp[:10]
        daily_tasks[day] += 1
        safe_tasks.append({"taskId": task.id, "status": task.status, "createdAt": task_stamp})
    days = {t["taskId"]: t["createdAt"][:10] for t in safe_tasks}
    for finding in findings:
        if (finding.scope_id != scope.id or finding.task_id not in task_ids or
                finding.finding_type not in TEMPLATES or finding.severity not in SEVERITIES or
                finding.status not in STATUSES or not isinstance(finding.dedup_key, str) or
                not finding.dedup_key or len(finding.dedup_key) > 64):
            raise VerificationError(40903, "风险数据不符合报告契约")
        groups[finding.dedup_key].append(finding)
        daily_keys[days[finding.task_id]].add(finding.dedup_key)
    details = []
    for records in groups.values():
        latest = max(records, key=lambda f: (f.last_seen, f.id))
        title, impact, suggestion = TEMPLATES[latest.finding_type]
        source = (latest.raw_data or {}).get("source_sha256") if isinstance(latest.raw_data, dict) else None
        if not isinstance(source, str) or not SHA.fullmatch(source):
            source = hashlib.sha256((latest.evidence or "").encode("utf-8")).hexdigest()
        details.append({
            "findingId": latest.id, "type": latest.finding_type, "title": title,
            "severity": latest.severity, "status": latest.status,
            "taskIds": sorted({f.task_id for f in records}), "findingIds": sorted(f.id for f in records),
            "firstSeen": min(_timestamp(f.first_seen) for f in records),
            "lastSeen": max(_timestamp(f.last_seen) for f in records),
            "evidenceSha256": source, "impact": impact, "suggestion": suggestion,
        })
    details.sort(key=lambda f: (SEVERITIES.index(f["severity"]), f["findingId"]))
    trend, cursor = [], date.fromisoformat(begin)
    while cursor <= date.fromisoformat(end):
        day = cursor.isoformat()
        trend.append({"date": day, "tasks": daily_tasks[day], "distinctRisks": len(daily_keys[day])})
        cursor += timedelta(days=1)
    return {
        "templateVersion": "T26-v1", "generatedAt": at,
        "periodBegin": begin, "periodEnd": end,
        "basis": "上海自然日，按任务创建日归属；任务和风险状态为生成时现状。每日去重数不可相加作为区间总数。",
        "asset": {"scopeId": scope.id, "domain": domain, "pageCount": page_count},
        "summary": {"tasks": len(tasks), "findingRows": len(findings), "distinctRisks": len(details),
                    "activeRisks": sum(f["status"] in {"OPEN", "CONFIRMED"} for f in details),
                    "bySeverity": {key: sum(f["severity"] == key for f in details) for key in SEVERITIES},
                    "byStatus": {key: sum(f["status"] == key for f in details) for key in STATUSES}},
        "tasks": safe_tasks, "trend": trend, "findings": details,
        "evidencePolicy": "仅导出证据 SHA256，不包含敏感原文、URL 路径/查询或原始工具响应；凭受控 Finding ID 回查原证据。",
    }


class ReportService:
    def __init__(self, repository, storage):
        self.repository, self.storage = repository, storage

    def generate(self, scope_id, begin, end, principal, *, at=None):
        principal = require_principal(principal, write=False)
        if not _id(scope_id):
            raise VerificationError(40001, "资产 ID 不合法")
        interval = period(begin, end)
        if scope_id not in principal.scope_ids:
            raise VerificationError(40310, "无此资产权限")
        stamp = _timestamp(at)
        identifier, snapshot = self.repository.prepare(scope_id, interval, principal, stamp)
        try:
            token = self.storage.publish(snapshot)
            return self.repository.finish(identifier, token, principal)
        except Exception as error:
            # Do not delete a published bundle: a commit's acknowledgement can be ambiguous.
            self.repository.fail(identifier, principal)
            if isinstance(error, VerificationError):
                raise
            raise VerificationError(50000, "报告生成失败") from None

    def list(self, principal, *, scope_id=None, page=1, page_size=20):
        principal = require_principal(principal, write=False)
        pagination(page, page_size)
        if scope_id is not None:
            if not _id(scope_id):
                raise VerificationError(40001, "资产 ID 不合法")
            if scope_id not in principal.scope_ids:
                raise VerificationError(40310, "无此资产权限")
        return self.repository.list(principal, scope_id, page, page_size)

    def get(self, identifier, principal):
        principal = require_principal(principal, write=False)
        if not _id(identifier):
            raise VerificationError(40001, "报告 ID 不合法")
        return self.repository.get(identifier, principal)[0]

    def download(self, identifier, format, principal):
        principal = require_principal(principal, write=False)
        if not _id(identifier) or not isinstance(format, str) or format not in {"html", "pdf"}:
            raise VerificationError(40001, "报告 ID 或格式不合法")
        metadata, token = self.repository.get(identifier, principal)
        if metadata["status"] != "DONE":
            raise VerificationError(40902, "报告尚未就绪")
        try:
            content = self.storage.read(token, format)
        except Exception:
            raise VerificationError(50000, "报告文件缺失或完整性校验失败") from None
        # Recheck grants/scope after I/O and persist successful download audit before returning bytes.
        self.repository.downloaded(identifier, token, format, principal)
        return content
