"""Wappalyzer 指纹适配器（T17）—— 进程内调用 python-Wappalyzer。

依据：
- SDD-04 T11 工具注册表契约（categories 含 `FINGERPRINT`）
- SRS-02 F3.1 常见漏洞检测：指纹识别（Wappalyzer）→ 组件/版本比对漏洞库
- SDD-01 §5.1 检测流水线「指纹识别」阶段、§5.4
- 工具清单 §2.1：Wappalyzer **只读分析**、「仅解析响应，无写操作」

实现变更（2026-10-14）：由「调用 wappalyzer CLI」改为**进程内调用 python-Wappalyzer**
（Wappalyzer 规则的 Python 实现，自带 1270+ 项技术规则）。原因：
1. npm 上的 `wappalyzer` CLI 已废弃，无法作为稳定依赖；
2. 进程内调用可测、无外部二进制、无容器依赖；
3. 该库自带抓取（`WebPage.new_from_url`），仅只读 GET。

因此本适配器**没有 subprocess / 容器模式**——`execution_mode` 恒为 `local`。

⚠️ 两处待决（见 `docs/01-项目治理/04-C组对SDD-04工具注册表契约的核对反馈.md`）：
1. `FINGERPRINT` 不在 SDD-02 的 `finding_type` 枚举内。指纹不是风险项，本适配器统一以
   `severity=INFO` 输出，**是否落库为 Finding 需口径确认**，未确认前不应进入告警链路；
2. 工具清单 §2.1 记为「Wappalyzer」，此处为同名规则的 Python 实现，清单宜同步说明。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Callable
from urllib.parse import urlparse

from src.tools.contract import RateLimit, ToolManifest, ToolResult

TOOL_ID = "wappalyzer"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
# 只读分析：拒绝任何主动发送数据或攻击类参数
_FORBIDDEN_PARAMS = frozenset({"post_data", "payload", "fuzz", "brute", "upload", "exploit"})
_CIDR_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b")
_RANGE_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}-\d{1,3}(?:\.\d{1,3}){3}\b")

# python-Wappalyzer 底层用 requests，其 Timeout 并非内建 TimeoutError
_TIMEOUT_EXCEPTIONS: tuple[type[BaseException], ...] = (TimeoutError,)
try:  # pragma: no cover - 取决于运行环境是否装了 requests
    from requests.exceptions import Timeout as _RequestsTimeout

    _TIMEOUT_EXCEPTIONS = (TimeoutError, _RequestsTimeout)
except ImportError:  # pragma: no cover
    pass


@lru_cache(maxsize=1)
def _rules():
    """加载 Wappalyzer 规则库（进程内缓存一次，避免每次扫描重复解析）。"""
    from Wappalyzer import Wappalyzer

    return Wappalyzer.latest()


class WappalyzerAdapter:
    """Wappalyzer 指纹适配器，实现 SDD-04 契约的 `manifest` / `non_destructive_check` / `run`。"""

    def __init__(self, *, analyzer: Callable[[str], dict] | None = None) -> None:
        # analyzer 仅用于测试注入；生产走 python-Wappalyzer
        self._analyzer = analyzer
        self.manifest = ToolManifest(
            id=TOOL_ID,
            name="Wappalyzer",
            description="技术栈指纹识别（组件/版本）；只读解析响应，无写操作",
            version="0.2.0",
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

    # ---- 指纹识别 ----

    def _analyze(self, url: str) -> dict:
        if self._analyzer is not None:
            return self._analyzer(url)

        from Wappalyzer import WebPage

        # verify=False：授权靶场常使用自签证书，避免因证书校验导致漏检
        page = WebPage.new_from_url(
            url, verify=False, timeout=self.manifest.timeout_seconds
        )
        return _rules().analyze_with_versions_and_categories(page)

    # ---- 输出归一化（SDD-01 §5.5） ----

    def parse_detected(self, detected: dict[str, Any], url: str) -> list[dict[str, Any]]:
        """把 python-Wappalyzer 的识别结果转为归一化 Finding（`severity=INFO`，非风险项）。"""
        findings: list[dict[str, Any]] = []
        for name, info in (detected or {}).items():
            info = info if isinstance(info, dict) else {}
            versions = [v for v in (info.get("versions") or []) if v]
            categories = [c for c in (info.get("categories") or []) if c]
            version = versions[0] if versions else ""

            findings.append(
                {
                    "type": "FINGERPRINT",
                    "severity": "INFO",
                    "title": f"{name} {version}".strip(),
                    "url": url,
                    "evidence": (
                        f"技术栈：{name}（版本 {version or '未知'}；"
                        f"类别 {', '.join(categories) or '未知'}）"
                    ),
                    "impact": "指纹信息用于关联组件已知漏洞（CVE），本身不构成风险。",
                    "suggestion": "结合组件与版本比对已知漏洞库；如版本存在已知漏洞，升级到安全版本。",
                    "tool_name": TOOL_ID,
                    "raw": {"name": name, "versions": versions, "categories": categories},
                }
            )
        return findings

    # ---- 执行（SDD-04 §2） ----

    def run(self, target: str, params: dict[str, Any]) -> ToolResult:
        if not self.non_destructive_check(target, params):
            return ToolResult(
                tool_id=TOOL_ID, status="REJECTED", error="non-destructive check failed"
            )

        try:
            detected = self._analyze(target)
        except ImportError as exc:
            return ToolResult(
                tool_id=TOOL_ID,
                status="FAILED",
                error=f"未安装 python-Wappalyzer（Wappalyzer 规则的 Python 实现）：{exc}",
            )
        except _TIMEOUT_EXCEPTIONS:
            return ToolResult(
                tool_id=TOOL_ID,
                status="TIMEOUT",
                error=f"抓取目标超时（{self.manifest.timeout_seconds}s）",
            )
        except Exception as exc:  # 兜底：工具异常不得冒泡打断流水线（SDD-01 §5.1 降级）
            return ToolResult(tool_id=TOOL_ID, status="FAILED", error=f"指纹识别失败：{exc}")

        return ToolResult(
            tool_id=TOOL_ID, status="SUCCESS", findings=self.parse_detected(detected, target)
        )
