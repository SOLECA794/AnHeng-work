"""T21 pure normalization and historical deduplication; no network or database I/O.

The caller supplies trusted task/asset context and observation time. Untrusted
tool payloads are fingerprinted, rather than copied into exported risk records.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


class FindingValidationError(ValueError):
    pass


class NonRiskFindingError(FindingValidationError):
    """Fingerprints are context for assessment, not independently actionable risks."""


RISK_TYPES = {"VULN", "WEAK_CONFIG", "MALICIOUS_LINK", "MALICIOUS_SCRIPT", "TAMPER", "SENSITIVE"}
SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
STATUSES = {"OPEN", "CONFIRMED", "FALSE_POSITIVE", "FIXED", "RETESTED"}
_TITLES = {"VULN": "漏洞规则命中", "WEAK_CONFIG": "弱配置规则命中",
           "MALICIOUS_LINK": "可疑外链规则命中", "MALICIOUS_SCRIPT": "可疑脚本规则命中",
           "TAMPER": "页面篡改规则命中", "SENSITIVE": "敏感信息规则命中"}
_SHANGHAI = timezone(timedelta(hours=8))
_HASH = re.compile(r"^[0-9a-f]{64}$")
_KEY = re.compile(r"^[0-9a-f]{32}$")
_TOOL = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
_IMPACT = "该规则命中提示可能存在安全风险，需结合资产与业务场景复核。"
_SUGGESTION = "核对命中规则和授权资源，确认后修复相应内容或配置并复测。"
_FIELDS = {"type", "severity", "title", "url", "evidence", "impact", "suggestion", "tool_name",
           "status", "task_id", "scope_id", "page_id", "dedup_key", "first_seen", "last_seen", "occurrence", "raw"}


def _json(value) -> str:
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(text) > 1_000_000:
            raise FindingValidationError("payload exceeds normalization size limit")
        return text
    except (TypeError, ValueError) as exc:
        if isinstance(exc, FindingValidationError):
            raise
        raise FindingValidationError("expected finite JSON-compatible data") from None


def _sha(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _alias(data: Mapping, names: tuple[str, ...], default=None):
    values = [data[name] for name in names if name in data]
    if values and any(value != values[0] for value in values[1:]):
        raise FindingValidationError("conflicting field aliases")
    return values[0] if values else default


def _positive_id(value, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise FindingValidationError("identifiers must be positive integers")
    return value


def _time(value) -> str:
    try:
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
        raise FindingValidationError("invalid observation time") from None


def normalize_url(url: str) -> str:
    """Canonicalize identity without dropping query values or reordering duplicate values."""
    if not isinstance(url, str) or not url.strip() or len(url) > 8192:
        raise FindingValidationError("invalid target URL")
    url = url.strip()
    if "\\" in url or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise FindingValidationError("invalid target URL")
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError
        host = parts.hostname.lower()
        host = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
        port = parts.port
        netloc = host if port is None or port == {"http": 80, "https": 443}[parts.scheme] else f"{host}:{port}"
        if re.search(r"%(?![0-9a-fA-F]{2})", parts.path + parts.query):
            raise ValueError
        path = re.sub(r"%([0-9a-fA-F]{2})", lambda match: (
            chr(int(match[1], 16)) if chr(int(match[1], 16)) in _UNRESERVED else "%" + match[1].upper()
        ), parts.path or "/")
        # RFC-style dot segments; retain repeated slashes and the trailing slash.
        segments = []
        for segment in path.split("/"):
            if segment == ".":
                continue
            if segment == "..":
                if len(segments) > 1:
                    segments.pop()
            else:
                segments.append(segment)
        if path.endswith(("/.", "/..")):
            segments.append("")
        path = "/".join(segments) or "/"
        path = quote(path, safe="/%:@!$&'()*+,;=-._~")
        query = parse_qsl(parts.query, keep_blank_values=True, encoding="utf-8", errors="strict")
        query.sort(key=lambda item: item[0])  # Stable sort retains order within the same parameter.
        return urlunsplit((parts.scheme, netloc, path, urlencode(query), ""))
    except (TypeError, ValueError, UnicodeError):
        raise FindingValidationError("invalid target URL") from None


def _feature(source: Mapping, raw: Mapping, kind: str, supplied):
    if supplied is not None:
        return {"shared": supplied}
    if source.get("evidence_feature") is not None:
        return {"shared": source["evidence_feature"]}
    if raw.get("template-id"):
        return {"template": raw["template-id"], "matcher": raw.get("matcher-name")}
    if raw.get("pluginId") is not None:
        param = source.get("param", raw.get("param"))
        if param is None and isinstance(source.get("evidence"), str):
            evidence = source["evidence"]
            param = evidence.rsplit("；参数：", 1)[-1] if "；参数：" in evidence else None
        return {"plugin": raw["pluginId"], "param": param}
    rule = source.get("rule_id", raw.get("rule_id"))
    if rule:
        feature = {"rule": rule}
        if kind == "SENSITIVE":
            feature["location"] = source.get("location", raw.get("location"))
            feature["rule_type"] = source.get("rule_type", raw.get("rule_type"))
        return feature
    evidence = source.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip() or "[REDACTED]" in evidence:
        raise FindingValidationError("stable evidence identity is required")
    return {"evidence": " ".join(evidence.split())}


def normalize_finding(source, *, task_id: int, scope_id: int, tool_name: str,
                      seen_at: str | datetime, evidence_feature=None, identity_url: str | None = None) -> dict:
    """Return SDD-01 fields with safe provenance and a stable non-security MD5 key."""
    if is_dataclass(source) and not isinstance(source, type):
        source = asdict(source)
    if not isinstance(source, Mapping):
        raise FindingValidationError("finding must be a mapping or dataclass")
    source = dict(source)
    source_hash = _sha(source)
    task_id, scope_id = _positive_id(task_id), _positive_id(scope_id)
    page_id = _positive_id(_alias(source, ("page_id", "pageId")), optional=True)
    if not isinstance(tool_name, str) or not _TOOL.fullmatch(tool_name):
        raise FindingValidationError("invalid tool identifier")
    seen_at = _time(seen_at)
    kind = _alias(source, ("type", "finding_type", "findingType"))
    if not isinstance(kind, str):
        raise FindingValidationError("risk type is required")
    kind = kind.upper()
    if kind == "FINGERPRINT":
        raise NonRiskFindingError("fingerprints must be handled separately from risk findings")
    if kind not in RISK_TYPES:
        raise FindingValidationError("unsupported risk type")
    severity, status = source.get("severity", "INFO"), source.get("status", "OPEN")
    if not isinstance(severity, str) or severity.upper() not in SEVERITIES:
        raise FindingValidationError("invalid severity")
    if not isinstance(status, str) or status.upper() not in STATUSES:
        raise FindingValidationError("invalid finding status")
    raw = _alias(source, ("raw", "raw_data", "rawData"), {})
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise FindingValidationError("raw metadata must be an object")
    canonical_url = normalize_url(source.get("url"))
    target = normalize_url(identity_url) if identity_url is not None else canonical_url
    opaque = raw.get("target_sha256")
    if opaque is not None:
        if not isinstance(opaque, str) or not _HASH.fullmatch(opaque):
            raise FindingValidationError("invalid opaque target fingerprint")
        if identity_url is None:
            target = "sha256:" + opaque
    feature = _feature(source, raw, kind, evidence_feature)
    if evidence_feature is None and source.get("evidence_feature") is None:
        # A tool-local rule identifier is not proof of semantic equivalence.
        feature = {"tool": tool_name, "local_feature": feature}
    feature_hash = _sha(feature)
    target_hash = hashlib.sha256(target.encode("utf-8")).hexdigest()
    identity = {"target_sha256": target_hash, "feature_sha256": feature_hash}
    dedup_key = hashlib.md5(_json([target, kind, feature_hash]).encode("utf-8"), usedforsecurity=False).hexdigest()
    parts = urlsplit(canonical_url)
    display_path = "/" if parts.path == "/" else "/[REDACTED_PATH]"
    display_url = urlunsplit((parts.scheme, parts.netloc, display_path, "", ""))
    return {
        "type": kind, "severity": severity.upper(), "title": _TITLES[kind], "url": display_url,
        "evidence": f"[REDACTED] (sha256={feature_hash})",
        "impact": _IMPACT, "suggestion": _SUGGESTION,
        "tool_name": tool_name, "status": status.upper(), "task_id": task_id,
        "scope_id": scope_id, "page_id": page_id, "dedup_key": dedup_key,
        "first_seen": seen_at, "last_seen": seen_at, "occurrence": 1,
        "raw": {"identity": identity, "source_sha256": source_hash,
                "observations": [{"task_id": task_id, "tool_name": tool_name, "page_id": page_id,
                                  "seen_at": seen_at, "source_sha256": source_hash}]},
    }


def _validate_record(value) -> dict:
    if not isinstance(value, Mapping):
        raise FindingValidationError("expected a normalized record")
    if not _FIELDS <= set(value) or set(value) - _FIELDS - {"id"}:
        raise FindingValidationError("expected normalized safe record fields")
    record = deepcopy(dict(value))
    if any(not isinstance(record[name], str) or record[name] not in allowed for name, allowed in (
        ("type", RISK_TYPES), ("severity", SEVERITIES), ("status", STATUSES)
    )):
        raise FindingValidationError("invalid normalized enums")
    if record["title"] != _TITLES[record["type"]] or record["impact"] != _IMPACT or record["suggestion"] != _SUGGESTION:
        raise FindingValidationError("untrusted display text is not accepted in normalized history")
    if not isinstance(record["tool_name"], str) or not _TOOL.fullmatch(record["tool_name"]):
        raise FindingValidationError("invalid normalized tool")
    if "id" in record:
        _positive_id(record["id"])
    _positive_id(record.get("scope_id"))
    _positive_id(record.get("task_id"))
    _positive_id(record.get("page_id"), optional=True)
    _positive_id(record["occurrence"])
    try:
        parts = urlsplit(record["url"])
        if (parts.path not in {"/", "/[REDACTED_PATH]"} or parts.query or parts.fragment
            or normalize_url(urlunsplit((parts.scheme, parts.netloc, "/", "", ""))) != urlunsplit((parts.scheme, parts.netloc, "/", "", ""))):
            raise ValueError
    except (TypeError, ValueError):
        raise FindingValidationError("invalid redacted display URL") from None
    if not isinstance(record.get("dedup_key"), str) or not _KEY.fullmatch(record["dedup_key"]):
        raise FindingValidationError("invalid deduplication key")
    record["first_seen"], record["last_seen"] = _time(record.get("first_seen")), _time(record.get("last_seen"))
    if record["first_seen"] > record["last_seen"]:
        raise FindingValidationError("invalid observation time range")
    raw = record.get("raw")
    if not isinstance(raw, dict) or set(raw) != {"identity", "source_sha256", "observations"} or not isinstance(raw.get("identity"), dict):
        raise FindingValidationError("normalized identity is required")
    identity = raw["identity"]
    if set(identity) != {"target_sha256", "feature_sha256"} or any(
        not isinstance(value, str) or not _HASH.fullmatch(value) for value in identity.values()
    ):
        raise FindingValidationError("invalid normalized identity")
    if not isinstance(raw["source_sha256"], str) or not _HASH.fullmatch(raw["source_sha256"]):
        raise FindingValidationError("invalid normalized source fingerprint")
    if record["evidence"] != f"[REDACTED] (sha256={identity['feature_sha256']})":
        raise FindingValidationError("invalid redacted evidence")
    observations = raw.get("observations")
    if not isinstance(observations, list) or not observations or len(observations) > 10_000:
        raise FindingValidationError("observation provenance is required")
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != {"task_id", "tool_name", "page_id", "seen_at", "source_sha256"}:
            raise FindingValidationError("invalid observation provenance")
        _positive_id(observation.get("task_id"))
        _positive_id(observation.get("page_id"), optional=True)
        if not isinstance(observation.get("tool_name"), str) or not _TOOL.fullmatch(observation["tool_name"]):
            raise FindingValidationError("invalid observation tool")
        if not isinstance(observation.get("source_sha256"), str) or not _HASH.fullmatch(observation["source_sha256"]):
            raise FindingValidationError("invalid observation source fingerprint")
        observation["seen_at"] = _time(observation.get("seen_at"))
    if record["occurrence"] != len({item["task_id"] for item in observations}) or record["task_id"] not in {item["task_id"] for item in observations}:
        raise FindingValidationError("inconsistent inspection-round provenance")
    if record["first_seen"] > min(item["seen_at"] for item in observations) or record["last_seen"] != max(item["seen_at"] for item in observations):
        raise FindingValidationError("inconsistent observation timestamps")
    return record


def deduplicate_findings(findings: Sequence[Mapping], *, existing: Sequence[Mapping] = ()) -> list[dict]:
    """Merge exact identities, counting distinct task rounds; never change verification states."""
    groups = {}
    history = {}
    for prior, records in ((True, existing), (False, findings)):
        for value in records:
            record = _validate_record(value)
            key = (record["scope_id"], record["dedup_key"])
            if key in groups and (record["type"], record["raw"]["identity"]) != (
                groups[key][0]["type"], groups[key][0]["raw"]["identity"]
            ):
                raise FindingValidationError("deduplication key has conflicting identities")
            groups.setdefault(key, []).append(record)
            if prior:
                if key in history and history[key]["status"] != record["status"]:
                    raise FindingValidationError("historical verification statuses conflict")
                history[key] = min(history.get(key, record), record, key=lambda item: item["first_seen"])
    merged = []
    for key, records in sorted(groups.items()):
        canonical = min(records, key=lambda item: (item["first_seen"], item["task_id"], item["tool_name"]))
        result = deepcopy(canonical)
        if key in history:
            result["status"] = history[key]["status"]
            if "id" in history[key]:
                result["id"] = history[key]["id"]
        observations = {}
        for record in records:
            for item in record["raw"]["observations"]:
                observation_key = (item["task_id"], item["tool_name"], item["page_id"], item["source_sha256"])
                if observation_key not in observations or item["seen_at"] > observations[observation_key]["seen_at"]:
                    observations[observation_key] = deepcopy(item)
        result["raw"]["observations"] = sorted(observations.values(), key=lambda item: (
            item["seen_at"], item["task_id"], item["tool_name"], item["page_id"] or 0, item["source_sha256"]
        ))
        result["occurrence"] = len({item["task_id"] for item in observations.values()})
        result["first_seen"] = min(item["first_seen"] for item in records)
        result["last_seen"] = max(item["last_seen"] for item in records)
        merged.append(result)
    return merged


def to_orm_values(record: Mapping) -> dict:
    """Field conversion only; the caller owns historical lookup and transaction upsert."""
    record = _validate_record(record)
    keys = ("task_id", "page_id", "scope_id", "severity", "title", "url", "evidence",
            "impact", "suggestion", "tool_name", "dedup_key", "status", "occurrence")
    values = {key: record[key] for key in keys}
    values["finding_type"] = record["type"]
    values["raw_data"] = deepcopy(record["raw"])
    values["first_seen"] = datetime.fromisoformat(record["first_seen"])
    values["last_seen"] = datetime.fromisoformat(record["last_seen"])
    return values
