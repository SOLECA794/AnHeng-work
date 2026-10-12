"""Wappalyzer 指纹适配器（T17）。

依据：
- SDD-04 T11 工具注册表契约（categories 含 `FINGERPRINT`）
- SRS-02 F3.1 常见漏洞检测：指纹识别（Wappalyzer）→ 组件/版本比对漏洞库
- SDD-01 §5.1 检测流水线「指纹识别」阶段、§5.4
- 工具清单 §2.1：Wappalyzer **只读分析**、「仅解析响应，无写操作」

⚠️ 两处待决（详见 `docs/01-项目治理/04-C组对SDD-04工具注册表契约的核对反馈.md`）：
  1. `FINGERPRINT` 不在 SDD-02 的 `finding_type` 枚举内。指纹本身不是风险项，本适配器
     统一以 `severity=INFO` 输出，**是否落库为 Finding 需口径确认**；未确认前不宜进入告警链路。
  2. npm 上的 `wappalyzer` CLI 已废弃，命令行调用方式（本实现按 `wappalyzer <url>` 构造）
     需在工具装好后实测校正；`build_command` 为唯一改动点。
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Callable
from urllib.parse import urlparse

from src.tools.contract import RateLimit, ToolManifest, ToolResult

TOOL_ID = "wappalyzer"
DEFAULT_BINARY = "wappalyzer"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
# 只读分析：拒绝任何主动发送数据或攻击类参数
_FORBIDDEN_PARAMS = frozenset({"post_data", "payload", "fuzz", "brute", "upload", "exploit"})
_CIDR_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b")
_RANGE_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}-\d{1,3}(?:\.\d{1,3}){3}\b")


def _subprocess_executor(command: list[str], timeout_seconds: int) -> str:
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


class WappalyzerAdapter:
    """Wappalyzer 指纹适配器，实现 SDD-04 契约的 `manifest` / `non_destructive_check` / `run`。"""

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
            name="Wappalyzer",
            description="技术栈指纹识别（组件/版本）；只读解析响应，无写操作",
            version="0.1.0",
            categories=["FINGERPRINT"],
            execution_mode="local",
            read_only=True,
            enabled=True,
            rate_limit=RateLimit(requests_per_minute=30, burst=1),
            timeout_seconds=60,
        )

    # ---- 非破坏校验（SDD-04 §3、工具清单 §2.1） ----

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
        return True

    # ---- 命令构造（CLI 方式待实测校正） ----

    def build_command(self, target: str, params: dict[str, Any]) -> list[str]:
        return [self._binary, target]

    # ---- 输出归一化（SDD-01 §5.5） ----

    def parse_output(self, raw: str) -> list[dict[str, Any]]:
        """解析 Wappalyzer CLI 的 JSON 输出为指纹记录（`severity=INFO`，非风险项）。"""
        raw = (raw or "").strip()
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []

        urls = payload.get("urls")
        if not isinstance(urls, dict):
            return []

        findings: list[dict[str, Any]] = []
        for url, entry in urls.items():
            if not isinstance(entry, dict):
                continue
            for application in entry.get("applications") or []:
                if isinstance(application, dict) and application.get("name"):
                    findings.append(self._to_finding(url, application))
        return findings

    def _to_finding(self, url: str, application: dict[str, Any]) -> dict[str, Any]:
        name = str(application.get("name")).strip()
        version = str(application.get("version") or "").strip()
        confidence = application.get("confidence")
        categories = [
            category.get("name")
            for category in (application.get("categories") or [])
            if isinstance(category, dict) and category.get("name")
        ]

        evidence = (
            f"技术栈：{name}（版本 {version or '未知'}；"
            f"置信度 {confidence if confidence is not None else '未知'}；"
            f"类别 {', '.join(categories) or '未知'}）"
        )

        return {
            "type": "FINGERPRINT",
            "severity": "INFO",
            "title": f"{name} {version}".strip(),
            "url": url,
            "evidence": evidence,
            "impact": "指纹信息用于关联组件已知漏洞（CVE），本身不构成风险。",
            "suggestion": "结合组件与版本比对已知漏洞库；如版本存在已知漏洞，升级到安全版本。",
            "tool_name": TOOL_ID,
            "raw": {
                "name": name,
                "version": version,
                "confidence": confidence,
                "categories": categories,
            },
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

        return ToolResult(tool_id=TOOL_ID, status="SUCCESS", findings=self.parse_output(raw))
