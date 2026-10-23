"""T23 deterministic grading after T21 deduplication; no I/O except explicit policy loading.

Asset context must come from trusted server-side discovery/asset metadata.
The output contains only fixed text, enums and fingerprints; never tool prose.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType

from src.services.findings import FindingValidationError, RISK_TYPES, to_orm_values


class GradingValidationError(ValueError):
    """Invalid grading input, with messages that exclude input values."""


LEVELS = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")
PAGE_IMPORTANCE = frozenset({"NORMAL", "HOME", "LOGIN", "CORE", "UNKNOWN"})
EXPOSURES = frozenset({"INTERNET", "INTERNAL", "RESTRICTED", "UNKNOWN"})
DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "config" / "risk-grading.json"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_TOOL = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TEXT = {
    "VULN": ("漏洞规则命中可能影响相关组件的机密性、完整性或可用性，具体利用条件需复核。",
             "核实漏洞编号、受影响版本与利用条件；升级到修复版本或实施厂商缓解措施，再复测。"),
    "WEAK_CONFIG": ("弱配置可能扩大信息暴露或访问边界，需核实业务必要性。",
                    "核实命中的配置项；关闭不必要功能、限制访问或补齐安全配置，再复测。"),
    "MALICIOUS_LINK": ("可疑外链可能将访问者导向不可信内容，需核实链接来源及业务用途。",
                       "复核外链与信誉命中依据；移除未经授权的链接，排查模板或内容发布入口，再复测。"),
    "MALICIOUS_SCRIPT": ("可疑脚本、框架或跳转可能影响访问者会话与页面行为，需复核是否为授权内容。",
                         "定位并核实脚本、框架及跳转来源；移除未经授权的引用，排查注入入口，涉及凭据时轮换并复测。"),
    "TAMPER": ("页面与基线不一致可能影响内容可信度，需核实是否为授权发布。",
               "比对差异与发布记录；确认非授权变更后恢复可信内容，排查变更入口并重新复测。"),
    "SENSITIVE": ("敏感信息规则命中可能暴露内部资料或凭据，需核实信息性质及可访问范围。",
                  "定位并移除公开敏感内容、限制资源访问；涉及凭据时及时轮换，清理缓存与副本后复测。"),
}
_PAGE_TEXT = {"NORMAL": "普通页面", "HOME": "首页", "LOGIN": "登录页", "CORE": "核心路径", "UNKNOWN": "页面重要性未知"}
_EXPOSURE_TEXT = {"INTERNET": "互联网暴露", "INTERNAL": "内网范围", "RESTRICTED": "受限访问范围", "UNKNOWN": "暴露范围未知"}


def _choice(value, choices, message):
    if not isinstance(value, str) or value not in choices:
        raise GradingValidationError(message)
    return value


def _identifier(value, pattern, message):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise GradingValidationError(message)
    return value


def _decimal_score(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise GradingValidationError("invalid CVSS score")
    if isinstance(value, str) and (len(value) > 32 or re.fullmatch(r"(?:0|[1-9][0-9]?)(?:\.[0-9]+)?", value) is None):
        raise GradingValidationError("invalid CVSS score")
    try:
        score = Decimal(str(value))
        if not score.is_finite() or not 0 <= score <= 10 or score != score.quantize(Decimal("0.1")):
            raise GradingValidationError("invalid CVSS score")
        return score
    except (InvalidOperation, ValueError, OverflowError):
        raise GradingValidationError("invalid CVSS score") from None


def cvss_to_severity(score) -> str:
    """Map an existing CVSS v3.1 score (FIRST table 14), without computing a vector."""
    score = _decimal_score(score)
    if score == 0:
        return "INFO"
    if score < 4:
        return "LOW"
    if score < 7:
        return "MEDIUM"
    if score < 9:
        return "HIGH"
    return "CRITICAL"


class GradingPolicy:
    """Validated immutable snapshot of a JSON policy, identified by its full digest."""

    def __init__(self, data: Mapping):
        if not isinstance(data, Mapping) or set(data) != {"version", "page_floors", "exposure_floors", "rule_overrides"}:
            raise GradingValidationError("invalid policy fields")
        version = _identifier(data["version"], _VERSION, "invalid policy version")
        floors = []
        for key, choices in (("page_floors", {"HOME", "LOGIN", "CORE"}), ("exposure_floors", {"INTERNET", "INTERNAL", "RESTRICTED"})):
            value = data[key]
            if not isinstance(value, Mapping) or not set(value) <= choices:
                raise GradingValidationError("invalid impact floors")
            floors.append({name: _choice(level, LEVELS, "invalid floor severity") for name, level in value.items()})
        rules = data["rule_overrides"]
        if not isinstance(rules, list) or len(rules) > 256:
            raise GradingValidationError("invalid rule overrides")
        overrides = {}
        canonical_rules = []
        for rule in rules:
            if not isinstance(rule, Mapping) or set(rule) != {"type", "tool_name", "rule_id", "severity"}:
                raise GradingValidationError("invalid rule override fields")
            kind = _choice(rule["type"], RISK_TYPES - {"VULN"}, "invalid override risk type")
            tool = _identifier(rule["tool_name"], _TOOL, "invalid override tool")
            rule_id = _identifier(rule["rule_id"], _ID, "invalid rule identifier")
            severity = _choice(rule["severity"], LEVELS, "invalid override severity")
            key = (kind, tool, rule_id)
            if key in overrides:
                raise GradingValidationError("duplicate rule override")
            overrides[key] = severity
            canonical_rules.append(dict(type=kind, tool_name=tool, rule_id=rule_id, severity=severity))
        canonical = dict(version=version, page_floors=floors[0], exposure_floors=floors[1],
                         rule_overrides=sorted(canonical_rules, key=lambda item: (item["type"], item["tool_name"], item["rule_id"])))
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        if len(payload) > 65_536:
            raise GradingValidationError("policy size limit exceeded")
        # Read-only properties, with immutable values throughout.
        object.__setattr__(self, "_values", (version, hashlib.sha256(payload).hexdigest(),
                                          MappingProxyType(floors[0]), MappingProxyType(floors[1]), MappingProxyType(overrides)))

    def __setattr__(self, name, value):
        raise AttributeError("grading policy is immutable")

    @property
    def version(self):
        return self._values[0]

    @property
    def sha256(self):
        return self._values[1]

    @property
    def page_floors(self):
        return self._values[2]

    @property
    def exposure_floors(self):
        return self._values[3]

    @property
    def rule_overrides(self):
        return self._values[4]


def _unique_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise GradingValidationError("duplicate policy JSON key")
        data[key] = value
    return data


def load_grading_policy(path: str | Path = DEFAULT_POLICY) -> GradingPolicy:
    """Read at most 64 KiB from an explicitly local policy; never fetch a remote URL."""
    try:
        with Path(path).open("rb") as stream:
            payload = stream.read(65_537)
        if len(payload) > 65_536:
            raise GradingValidationError("policy size limit exceeded")
        data = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
        return GradingPolicy(data)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, RecursionError):
        raise GradingValidationError("invalid policy file") from None


@dataclass(frozen=True)
class GradingContext:
    scope_id: int
    target_sha256: str
    page_importance: str = "UNKNOWN"
    exposure: str = "UNKNOWN"
    cvss_score: str | int | float | Decimal | None = None
    rule_id: str | None = None

    def __post_init__(self):
        if isinstance(self.scope_id, bool) or not isinstance(self.scope_id, int) or self.scope_id < 1:
            raise GradingValidationError("invalid context scope")
        _identifier(self.target_sha256, _HASH, "invalid context target fingerprint")
        _choice(self.page_importance, PAGE_IMPORTANCE, "invalid page importance")
        _choice(self.exposure, EXPOSURES, "invalid exposure")
        if self.cvss_score is not None:
            object.__setattr__(self, "cvss_score", _decimal_score(self.cvss_score))
        if self.rule_id is not None:
            _identifier(self.rule_id, _ID, "invalid rule identifier")


def grade_finding(record: Mapping, context: GradingContext, policy: GradingPolicy) -> dict:
    """Grade a canonical T21 record; preserve its redacted evidence and verification state."""
    if not isinstance(context, GradingContext) or not isinstance(policy, GradingPolicy):
        raise GradingValidationError("trusted context and validated policy are required")
    try:
        values = to_orm_values(record)
    except (FindingValidationError, TypeError, ValueError, UnicodeError, RecursionError, KeyError, AttributeError, OverflowError):
        raise GradingValidationError("invalid normalized finding") from None
    if (values["scope_id"] != context.scope_id
            or values["raw_data"]["identity"]["target_sha256"] != context.target_sha256):
        raise GradingValidationError("grading context does not match finding")
    kind = values["finding_type"]
    if context.cvss_score is not None and kind != "VULN":
        raise GradingValidationError("CVSS is only accepted for vulnerabilities")
    if kind == "VULN" and context.rule_id is not None:
        raise GradingValidationError("vulnerability grading does not accept rule overrides")
    reasons = []
    if kind == "VULN" and context.cvss_score is not None:
        base = cvss_to_severity(context.cvss_score)
        source = "CVSS_3_1"
        reasons.append("CVSS_MAPPED")
    else:
        override = policy.rule_overrides.get((kind, values["tool_name"], context.rule_id))
        base = override if override is not None else values["severity"]
        source = "RULE_OVERRIDE" if override is not None else "DETECTOR_SEVERITY"
        reasons.append("RULE_MATCHED" if override is not None else "CVSS_MISSING_FALLBACK" if kind == "VULN" else "DETECTOR_FALLBACK")
    severity = base
    if base != "INFO":
        for code, floor in (("IMPORTANT_PAGE_FLOOR", policy.page_floors.get(context.page_importance)),
                            ("EXPOSURE_FLOOR", policy.exposure_floors.get(context.exposure))):
            if floor is not None:
                reasons.append(code)
                severity = max(severity, floor, key=LEVELS.index)
    else:
        reasons.append("INFO_PRESERVED")
    if context.page_importance == "UNKNOWN":
        reasons.append("PAGE_UNKNOWN")
    if context.exposure == "UNKNOWN":
        reasons.append("EXPOSURE_UNKNOWN")
    impact, suggestion = _TEXT[kind]
    if severity == "INFO":
        impact = "当前规则等级为信息提示，不代表已确认存在可利用风险；仍需结合证据复核。"
    impact += f"影响面：{_PAGE_TEXT[context.page_importance]}，{_EXPOSURE_TEXT[context.exposure]}。"
    trace = {
        "policy_version": policy.version, "policy_sha256": policy.sha256,
        "base_source": source, "base_severity": base, "severity": severity,
        "page_importance": context.page_importance, "exposure": context.exposure,
        "cvss_score": float(context.cvss_score) if context.cvss_score is not None else None,
        "rule_sha256": hashlib.sha256(context.rule_id.encode("ascii")).hexdigest() if context.rule_id else None,
        "reason_codes": reasons,
    }
    return dict(severity=severity, evidence=values["evidence"], impact=impact, suggestion=suggestion, grading=trace)


def graded_orm_values(record: Mapping, context: GradingContext, policy: GradingPolicy) -> dict:
    """Build ORM insert/update values after deduplication; does not execute a database write."""
    grade = grade_finding(record, context, policy)
    values = to_orm_values(record)
    values.update({name: grade[name] for name in ("severity", "impact", "suggestion")})
    values["raw_data"]["grading"] = deepcopy(grade["grading"])
    return values
