"""T20: scan supplied content offline; never fetch a URL or persist raw evidence."""
from __future__ import annotations

import hashlib
import json
import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import regex

from src.tools.contract import ToolManifest, ToolResult


class RuleValidationError(ValueError):
    """Invalid configuration; messages deliberately exclude rule patterns."""


class UnauthorizedTargetError(ValueError):
    code = 40310


class ScanLimitError(ValueError):
    """A bounded scan failed; callers must not treat it as a clean result."""


def _canonical_path(path: str) -> str:
    for _ in range(4):
        decoded = unquote(path, errors="strict")
        if decoded == path:
            break
        path = decoded
    else:
        if unquote(path, errors="strict") != path:
            raise ValueError("ambiguous path encoding")
    if "\\" in path or any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise ValueError("invalid path")
    return posixpath.normpath("/" + path.lstrip("/"))


def _target(url: str) -> tuple[str, str, int, str]:
    if not isinstance(url, str) or "\\" in url or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValueError("invalid target URL")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError("target must be an HTTP URL without credentials")
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    return parts.scheme, parts.hostname.lower(), port, _canonical_path(parts.path)


@dataclass(frozen=True)
class AuthorizedScope:
    scope_id: str
    origin: str
    path_prefix: str = "/"

    def __post_init__(self) -> None:
        _target(self.origin)
        parts = urlsplit(self.origin)
        if not self.scope_id or parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("scope requires an ID and a bare HTTP origin")
        if not isinstance(self.path_prefix, str) or not self.path_prefix.startswith("/"):
            raise ValueError("scope path prefix must be absolute")
        _canonical_path(self.path_prefix)

    def allows(self, url: str) -> bool:
        try:
            scheme, host, port, path = _target(url)
            origin = _target(self.origin)
            prefix = _canonical_path(self.path_prefix)
            return (scheme, host, port) == origin[:3] and (
                prefix == "/" or path == prefix or path.startswith(prefix.rstrip("/") + "/")
            )
        except (TypeError, ValueError, UnicodeError):
            return False


@dataclass(frozen=True)
class SensitiveRule:
    id: str
    name: str
    rule_type: str
    pattern: str
    severity: str = "MEDIUM"
    enabled: bool = True
    scope_bind: str | None = None


@dataclass(frozen=True)
class ScanDocument:
    url: str
    text: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)


def load_rules(path: str | Path) -> list[SensitiveRule]:
    """Load a JSON rule list from an explicitly supplied local configuration."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise RuleValidationError("rules must be a JSON list of objects")
        rules = [SensitiveRule(**item) for item in data]
        SensitiveScanner(rules)  # Validate immediately, even if a rule is disabled.
        return rules
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuleValidationError("invalid rule configuration") from exc


class SensitiveScanner:
    def __init__(self, rules: Sequence[SensitiveRule], *, regex_timeout: float = 0.05,
                 max_content_chars: int = 1_000_000, max_findings: int = 1000):
        if regex_timeout <= 0 or max_content_chars < 1 or max_findings < 1:
            raise ValueError("scan limits must be positive")
        self.rules = tuple(rules)
        self.regex_timeout = regex_timeout
        self.max_content_chars = max_content_chars
        self.max_findings = max_findings
        self._patterns = {}
        ids = set()
        for rule in self.rules:
            if not isinstance(rule, SensitiveRule):
                raise RuleValidationError("expected SensitiveRule")
            if not isinstance(rule.id, str) or not rule.id or rule.id in ids:
                raise RuleValidationError("rule IDs must be nonempty and unique")
            ids.add(rule.id)
            if not isinstance(rule.name, str) or not rule.name:
                raise RuleValidationError("rule name is required")
            if rule.rule_type not in {"KEYWORD", "REGEX", "FILETYPE", "SAMPLE"}:
                raise RuleValidationError("unsupported rule type")
            if not isinstance(rule.pattern, str) or not rule.pattern:
                raise RuleValidationError("rule pattern is required")
            if rule.severity not in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}:
                raise RuleValidationError("unsupported severity")
            if not isinstance(rule.enabled, bool) or (rule.scope_bind is not None and not isinstance(rule.scope_bind, str)):
                raise RuleValidationError("invalid rule enabled/scope binding")
            if rule.rule_type == "REGEX":
                try:
                    self._patterns[rule.id] = regex.compile(rule.pattern)
                except regex.error as exc:
                    raise RuleValidationError("invalid regular expression") from None
            if rule.rule_type == "FILETYPE" and (
                not rule.pattern.startswith(".") or "/" in rule.pattern or "\\" in rule.pattern
            ):
                raise RuleValidationError("file type must be a filename or suffix beginning with a dot")
            if rule.rule_type == "SAMPLE" and rule.pattern.startswith("sha256:"):
                digest = rule.pattern[7:]
                if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
                    raise RuleValidationError("invalid SHA256 sample fingerprint")

    def scan(self, document: ScanDocument, scopes: Sequence[AuthorizedScope]) -> list[dict]:
        # Check authorization before reading or processing content.
        matched_scopes = [scope for scope in scopes if scope.allows(document.url)]
        if not matched_scopes:
            raise UnauthorizedTargetError("40310: target is outside authorized scopes")
        if not isinstance(document.text, str) or not isinstance(document.headers, Mapping):
            raise ValueError("document text/headers have invalid types")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in document.headers.items()):
            raise ValueError("header names and values must be strings")
        path = _target(document.url)[3]
        if len(document.text) + sum(len(k) + len(v) for k, v in document.headers.items()) + len(document.url) > self.max_content_chars:
            raise ScanLimitError("content limit exceeded")
        applicable_rules = [rule for rule in self.rules if rule.enabled and any(
            not rule.scope_bind or rule.scope_bind == scope.scope_id for scope in matched_scopes
        )]
        sources = [("body", document.text, None)] + [
            ("header", value, name) for name, value in document.headers.items()
        ]
        findings = []
        matched_values = set()  # Transient only; never included in output/logs.
        for rule in applicable_rules:
            scope = next(s for s in matched_scopes if not rule.scope_bind or rule.scope_bind == s.scope_id)
            if rule.rule_type == "FILETYPE":
                basename = posixpath.basename(path)
                if basename.lower().endswith(rule.pattern.lower()):
                    start = len(path) - len(rule.pattern)
                    findings.append(self._finding(rule, document, scope, "url_path", start, len(path), None, ""))
                    self._check_count(findings)
                continue
            for source, content, header in sources:
                try:
                    for start, end in self._matches(rule, content):
                        if start == end:  # Zero-length regex matches cannot provide evidence.
                            continue
                        matched_values.add(content[start:end])
                        findings.append(self._finding(rule, document, scope, source, start, end,
                                                      header, ""))
                        self._check_count(findings)
                except TimeoutError as exc:
                    raise ScanLimitError("regular expression time limit exceeded") from exc
        if findings:
            parts = urlsplit(document.url)
            # Context-dependent regex and hash rules may not re-match metadata.
            # Also mask literal values actually found in any input source.
            safe_path = self._redact_metadata(path, applicable_rules, matched_values)
            safe_url = urlunsplit((parts.scheme, parts.netloc, quote(safe_path, safe="/:@-._~"), "", ""))
            safe_headers = {name: self._redact_metadata(name, applicable_rules, matched_values)
                            for name in document.headers}
            for finding in findings:
                finding["url"] = safe_url
                name = finding["location"].get("header")
                if name is not None:
                    finding["location"]["header"] = safe_headers[name]
                    finding["raw_data"]["location"]["header"] = safe_headers[name]
        return findings

    def _matches(self, rule: SensitiveRule, content: str):
        if rule.rule_type == "REGEX":
            for match in self._patterns[rule.id].finditer(content, timeout=self.regex_timeout):
                yield match.span()
        elif rule.rule_type == "SAMPLE" and rule.pattern.startswith("sha256:"):
            if hashlib.sha256(content.encode("utf-8")).hexdigest() == rule.pattern[7:].lower():
                yield 0, len(content)
        else:
            offset = 0
            while True:
                start = content.find(rule.pattern, offset)
                if start < 0:
                    break
                end = start + len(rule.pattern)
                yield start, end
                offset = end

    def _check_count(self, findings: list) -> None:
        if len(findings) > self.max_findings:
            raise ScanLimitError("finding count limit exceeded")

    def _redact_metadata(self, text: str, rules: Sequence[SensitiveRule], matched_values: set[str]) -> str:
        """Mask sensitive matches even when they appear in URL paths/header names."""
        spans = set()
        try:
            for rule in rules:
                if rule.rule_type != "FILETYPE":
                    for start, end in self._matches(rule, text):
                        if start != end:
                            spans.add((start, end))
                            if len(spans) > self.max_findings:
                                raise ScanLimitError("metadata match count limit exceeded")
        except TimeoutError:
            raise ScanLimitError("metadata regular expression time limit exceeded") from None
        for value in matched_values:
            offset = 0
            while (start := text.find(value, offset)) >= 0:
                end = start + len(value)
                spans.add((start, end))
                if len(spans) > self.max_findings:
                    raise ScanLimitError("metadata match count limit exceeded")
                offset = end
        merged = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        offset, chunks = 0, []
        for start, end in merged:
            chunks.extend((text[offset:start], "[REDACTED]"))
            offset = end
        chunks.append(text[offset:])
        return "".join(chunks)

    @staticmethod
    def _finding(rule, document, scope, source, start, end, header, safe_url) -> dict:
        location = {"source": source, "start": start, "end": end}
        if header is not None:
            location["header"] = header
        return {
            "finding_type": "SENSITIVE", "severity": rule.severity,
            "title": "敏感信息规则命中", "url": safe_url,
            "scope_id": scope.scope_id, "rule_id": rule.id, "rule_type": rule.rule_type,
            "location": location, "evidence": f"[REDACTED] (length={end - start})",
            "impact": "公开内容可能暴露单位定义的敏感信息。",
            "suggestion": "移除公开敏感内容，限制资源访问；涉及凭据时及时轮换。",
            "tool_name": "sensitive-scanner", "status": "OPEN",
            "raw_data": {"rule_id": rule.id, "rule_type": rule.rule_type,
                         "location": location.copy(), "sample": "[REDACTED]", "match_length": end - start,
                         "target_sha256": hashlib.sha256(document.url.encode("utf-8")).hexdigest()},
        }


class SensitiveToolAdapter:
    """T11 adapter for already acquired text/headers; never performs target I/O."""
    def __init__(self, scanner: SensitiveScanner, scopes: Sequence[AuthorizedScope]):
        self.manifest = ToolManifest(
            id="sensitive-scanner", name="敏感信息离线检测器",
            description="授权范围内的关键词、正则、文件类型与样例匹配；证据脱敏；不抓取目标",
            categories=["SENSITIVE"], execution_mode="local", read_only=True,
        )
        self.scanner = scanner
        self.scopes = tuple(scopes)

    def non_destructive_check(self, target: str, params: dict) -> bool:
        return isinstance(params, dict) and any(scope.allows(target) for scope in self.scopes) and set(params) <= {"text", "headers"}

    def run(self, target: str, params: dict) -> ToolResult:
        if not self.non_destructive_check(target, params):
            return ToolResult(tool_id=self.manifest.id, status="REJECTED", error="40310: target or parameters not permitted")
        try:
            findings = self.scanner.scan(ScanDocument(target, params.get("text", ""), params.get("headers", {})), self.scopes)
            return ToolResult(tool_id=self.manifest.id, status="SUCCESS", findings=findings)
        except UnauthorizedTargetError:
            return ToolResult(tool_id=self.manifest.id, status="REJECTED", error="40310: target is outside authorized scopes")
        except ScanLimitError:
            return ToolResult(tool_id=self.manifest.id, status="FAILED", error="offline scan limit exceeded")
        except (TypeError, ValueError):
            return ToolResult(tool_id=self.manifest.id, status="FAILED", error="invalid offline document")
