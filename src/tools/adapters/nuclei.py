"""Nuclei 适配器（T15）。

依据：
- SDD-04 T11 工具注册表契约（ToolManifest / ToolAdapter / ToolResult）
- SRS-02 F3.1 常见漏洞检测（组件/版本比对 → 非破坏验证）
- SDD-01 §5.3 工具层设计、§5.5 结果归一化、§5.8 非破坏与合规
- 工具清单 §2.1 Nuclei 非破坏声明：仅发送检测请求，不利用、不注入
- SDD-02 §4.6 finding 字段

非破坏实现要点：
- 显式 `-no-interactsh`：禁用带外交互（OOB 反连），目标侧无回连；
- `-etags fuzz,intrusive,dos`：排除模糊测试、侵入式与拒绝服务类模板；
- `non_destructive_check` 拒绝非 http(s) 目标、网段/CIDR 目标与破坏性参数。

category 口径按 SDD-02（`VULN` / `WEAK_CONFIG`）。SDD-04 §1 的 category 词表与本口径不一致，
待确认后此处可能调整，详见 `docs/01-项目治理/04-C组对SDD-04工具注册表契约的核对反馈.md`。
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Callable
from urllib.parse import urlparse

from src.tools.contract import RateLimit, ToolManifest, ToolResult

TOOL_ID = "nuclei"
DEFAULT_BINARY = "nuclei"

# 仅允许 http/https 单目标
_ALLOWED_SCHEMES = frozenset({"http", "https"})
# 破坏性/越权参数：出现即拒绝（工具清单 §2.3 禁止项）
_FORBIDDEN_PARAMS = frozenset(
    {"interactsh_url", "fuzz", "headless", "payload", "exploit", "brute", "sqlmap"}
)
# 排除的模板标签：模糊测试 / 侵入式 / 拒绝服务
_EXCLUDED_TAGS = "fuzz,intrusive,dos"
_UNSAFE_TAGS = frozenset({"fuzz", "fuzzing", "intrusive", "dos"})
# 命中的模板 tags 含以下标记时归为弱配置，否则归为漏洞
_WEAK_CONFIG_MARKERS = frozenset(
    {"misconfig", "exposure", "config", "ssl", "tls", "headers", "default-login"}
)

# 网段 / CIDR / IP 区间：范围扩大即越权，不允许作为目标
_CIDR_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b")
_RANGE_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}-\d{1,3}(?:\.\d{1,3}){3}\b")

_SEVERITY_MAP = {
    "critical": "CRITICAL",
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
    "info": "INFO",
    "unknown": "INFO",
}


def _subprocess_executor(command: list[str], timeout_seconds: int) -> str:
    """默认执行器：调用本机 nuclei 二进制，返回 stdout。"""
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    return completed.stdout


class NucleiAdapter:
    """Nuclei 工具适配器，实现 SDD-04 契约的 `manifest` / `non_destructive_check` / `run`。"""

    def __init__(
        self,
        *,
        executor: Callable[[list[str], int], str] | None = None,
        binary: str = DEFAULT_BINARY,
    ) -> None:
        self._binary = binary
        self._executor = executor or _subprocess_executor
        self.manifest = ToolManifest(
            id=TOOL_ID,
            name="Nuclei",
            description="模板化漏洞与弱配置检测；仅发送检测请求，不利用、不注入（非破坏）",
            version="0.1.0",
            categories=["VULN", "WEAK_CONFIG"],
            execution_mode="local",
            read_only=True,
            enabled=True,
            rate_limit=RateLimit(requests_per_minute=30, burst=1),
            timeout_seconds=300,
        )

    # ---- 非破坏校验（SDD-04 §3、SDD-01 §5.8） ----

    def non_destructive_check(self, target: str, params: dict[str, Any]) -> bool:
        if not isinstance(target, str) or not target.strip():
            return False
        target = target.strip()

        parsed = urlparse(target)
        if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.hostname:
            return False
        if _CIDR_RE.search(target) or _RANGE_RE.search(target):
            return False

        if _FORBIDDEN_PARAMS & {str(key).lower() for key in params}:
            return False

        for template in params.get("templates") or []:
            text = str(template).lower()
            if any(tag in text for tag in ("fuzz", "intrusive", "dos")):
                return False
        for tag in params.get("tags") or []:
            if str(tag).lower() in _UNSAFE_TAGS:
                return False
        return True

    # ---- 命令构造 ----

    def build_command(self, target: str, params: dict[str, Any]) -> list[str]:
        """构造 nuclei 调用命令；始终带非破坏与限速参数。"""
        command = [
            self._binary,
            "-u",
            target,
            "-jsonl",
            "-silent",
            "-no-interactsh",
            "-etags",
            _EXCLUDED_TAGS,
            "-rate-limit",
            str(params.get("rate_limit", self.manifest.rate_limit.requests_per_minute)),
            "-timeout",
            str(params.get("per_request_timeout", 10)),
        ]
        for template in params.get("templates") or []:
            command += ["-t", str(template)]
        return command

    # ---- 输出归一化（SDD-01 §5.5、SDD-02 §4.6） ----

    def parse_jsonl(self, raw: str) -> list[dict[str, Any]]:
        """解析 nuclei `-jsonl` 输出为归一化 Finding 字典列表。"""
        findings: list[dict[str, Any]] = []
        for line in (raw or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                findings.append(self._to_finding(item))
        return findings

    def _to_finding(self, item: dict[str, Any]) -> dict[str, Any]:
        info = item.get("info") or {}
        template_id = item.get("template-id") or info.get("name") or TOOL_ID
        tags = [str(tag).lower() for tag in (info.get("tags") or [])]
        category = "WEAK_CONFIG" if _WEAK_CONFIG_MARKERS & set(tags) else "VULN"
        severity = _SEVERITY_MAP.get(str(info.get("severity", "")).lower(), "INFO")
        url = item.get("matched-at") or item.get("host") or ""

        # 证据脱敏：只保留模板标识、命中位置与标签，不含响应正文
        evidence = f"模板：{template_id}；命中位置：{url}；标签：{','.join(tags) or '-'}"

        return {
            "type": category,
            "severity": severity,
            "title": info.get("name") or template_id,
            "url": url,
            "evidence": evidence,
            "impact": info.get("description")
            or "命中已知漏洞或弱配置模板，可能被用于进一步攻击。",
            "suggestion": info.get("remediation")
            or "参照该检测模板的修复建议，结合组件版本升级或配置加固处理。",
            "tool_name": TOOL_ID,
            "raw": item,
        }

    # ---- 执行（SDD-04 §2） ----

    def run(self, target: str, params: dict[str, Any]) -> ToolResult:
        if not self.non_destructive_check(target, params):
            return ToolResult(
                tool_id=TOOL_ID, status="REJECTED", error="non-destructive check failed"
            )

        command = self.build_command(target, params)
        try:
            raw = self._executor(command, self.manifest.timeout_seconds)
        except FileNotFoundError:
            return ToolResult(
                tool_id=TOOL_ID,
                status="FAILED",
                error=f"未找到可执行文件：{self._binary}，请先安装 {self.manifest.name}",
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_id=TOOL_ID,
                status="TIMEOUT",
                error=f"执行超时（{self.manifest.timeout_seconds}s）",
            )
        except OSError as exc:
            return ToolResult(tool_id=TOOL_ID, status="FAILED", error=f"执行失败：{exc}")
        except Exception as exc:  # 兜底：工具异常不得冒泡打断流水线（SDD-01 §5.1 降级）
            return ToolResult(tool_id=TOOL_ID, status="FAILED", error=str(exc))

        return ToolResult(tool_id=TOOL_ID, status="SUCCESS", findings=self.parse_jsonl(raw))
