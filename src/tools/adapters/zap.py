"""ZAP 被动扫描适配器（T16）。

依据：
- SDD-04 T11 工具注册表契约（ToolManifest / ToolAdapter / ToolResult）
- SRS-02 F3.2 弱配置检测（缺失安全响应头、错误信息泄露、目录列举等）
- 工具清单 §2.1：OWASP ZAP **被动扫描**、被动代理模式、「仅被动观察请求/响应」
- SDD-01 §5.3/§5.5、SDD-02 §4.6 finding 字段

非破坏实现要点：
- 只调用 ZAP 的 `accessUrl`（让 ZAP 被动观察一次响应）+ `alerts`（读取告警），
  **不调用 spider / ajaxSpider / ascan 等主动扫描接口**；
- `non_destructive_check` 显式拒绝主动扫描与攻击类参数。

category 口径按 SDD-02（`VULN` / `WEAK_CONFIG`），与 SDD-04 §1 词表的差异见
`docs/01-项目治理/04-C组对SDD-04工具注册表契约的核对反馈.md`。
"""

from __future__ import annotations

import re
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from src.tools.container import container_reachable_url
from src.tools.contract import RateLimit, ToolManifest, ToolResult

TOOL_ID = "zap"
DEFAULT_BASE_URL = "http://127.0.0.1:8090"

ACCESS_URL_PATH = "/JSON/core/action/accessUrl/"
ALERTS_PATH = "/JSON/core/view/alerts/"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
# 主动扫描/攻击类参数：出现即拒绝（工具清单 §2.1 仅限被动模式）
_FORBIDDEN_PARAMS = frozenset(
    {"active_scan", "ascan", "attack", "ajax_spider", "spider", "fuzz", "brute", "payload"}
)
_CIDR_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b")
_RANGE_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}-\d{1,3}(?:\.\d{1,3}){3}\b")

_RISK_MAP = {
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
    "informational": "INFO",
    "info": "INFO",
}
# 命中以下标记的告警归为弱配置，否则归为漏洞
_WEAK_CONFIG_MARKERS = (
    "header",
    "cookie",
    "information disclosure",
    "content-type",
    "cache",
    "cross-domain",
    "options method",
    "directory browsing",
    "server leaks",
    "timestamp",
    "application error",
)


class ZapAdapter:
    """ZAP 被动扫描适配器，实现 SDD-04 契约的 `manifest` / `non_destructive_check` / `run`。"""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        http: Callable[[str, dict], dict] | None = None,
        api_key: str = "",
        target_alias: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http = http or self._default_http
        # ZAP 会自己去取目标页面：ZAP 在容器内时，需把宿主机回环地址改写为容器可达别名
        self._target_alias = target_alias
        self.manifest = ToolManifest(
            id=TOOL_ID,
            name="OWASP ZAP（被动扫描）",
            description="经 ZAP 守护进程被动观察目标请求/响应，检测漏洞与弱配置；不启用主动扫描",
            version="0.1.0",
            categories=["VULN", "WEAK_CONFIG"],
            execution_mode="http",
            read_only=True,
            enabled=True,
            rate_limit=RateLimit(requests_per_minute=30, burst=1),
            timeout_seconds=120,
        )

    # ---- 默认 REST 客户端 ----

    def _default_http(self, path: str, params: dict) -> dict:
        try:
            response = httpx.get(
                self._base_url + path, params=params, timeout=self.manifest.timeout_seconds
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:  # 统一转成标准异常，便于上层判定
            raise TimeoutError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(str(exc)) from exc
        return response.json()

    def _with_key(self, params: dict) -> dict:
        if self._api_key:
            params = {**params, "apikey": self._api_key}
        return params

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

    # ---- 输出归一化（SDD-01 §5.5、SDD-02 §4.6） ----

    def parse_alerts(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        alerts = (payload or {}).get("alerts") or []
        findings: list[dict[str, Any]] = []

        for item in alerts:
            if not isinstance(item, dict):
                continue
            plugin_id = str(item.get("pluginId") or "").strip()
            name = str(item.get("alert") or "").strip()
            if not name and not plugin_id:
                continue  # 空条目/脏数据跳过

            url = item.get("url") or ""
            param = item.get("param") or ""
            lowered = name.lower()
            category = (
                "WEAK_CONFIG"
                if any(marker in lowered for marker in _WEAK_CONFIG_MARKERS)
                else "VULN"
            )

            # 证据脱敏：不引用 ZAP 的 evidence / attack 字段（可能含响应片段或注入载荷）
            evidence = f"ZAP 插件 {plugin_id or '-'}（{name}）；命中位置：{url}"
            if param:
                evidence += f"；参数：{param}"

            findings.append(
                {
                    "type": category,
                    "severity": _RISK_MAP.get(str(item.get("risk") or "").lower(), "INFO"),
                    "title": name or f"ZAP 插件 {plugin_id}",
                    "url": url,
                    "evidence": evidence,
                    "impact": item.get("description")
                    or "命中 ZAP 规则，可能存在安全配置缺陷或漏洞。",
                    "suggestion": item.get("solution")
                    or "参照 ZAP 给出的修复建议，加固配置或修复对应漏洞。",
                    "tool_name": TOOL_ID,
                    # raw 同样脱敏：剔除 evidence/attack 等可能含载荷的字段
                    "raw": {
                        "pluginId": plugin_id,
                        "risk": item.get("risk"),
                        "confidence": item.get("confidence"),
                        "cweid": item.get("cweid"),
                    },
                }
            )
        return findings

    # ---- 执行（SDD-04 §2） ----

    def run(self, target: str, params: dict[str, Any]) -> ToolResult:
        if not self.non_destructive_check(target, params):
            return ToolResult(
                tool_id=TOOL_ID, status="REJECTED", error="non-destructive check failed"
            )

        zap_target = (
            container_reachable_url(target, self._target_alias)
            if self._target_alias
            else target
        )
        try:
            # 让 ZAP 自身访问目标一次（被动观察），再读取该目标的告警
            self._http(ACCESS_URL_PATH, self._with_key({"url": zap_target}))
            payload = self._http(ALERTS_PATH, self._with_key({"baseurl": zap_target}))
        except TimeoutError:
            return ToolResult(
                tool_id=TOOL_ID,
                status="TIMEOUT",
                error=f"ZAP 调用超时（{self.manifest.timeout_seconds}s）",
            )
        except OSError as exc:
            return ToolResult(tool_id=TOOL_ID, status="FAILED", error=f"ZAP 不可达或调用失败：{exc}")
        except Exception as exc:  # 兜底：工具异常不得冒泡打断流水线（SDD-01 §5.1 降级）
            return ToolResult(tool_id=TOOL_ID, status="FAILED", error=str(exc))

        return ToolResult(tool_id=TOOL_ID, status="SUCCESS", findings=self.parse_alerts(payload))
