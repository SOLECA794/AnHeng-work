
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
import uuid
from dataclasses import replace
from typing import Any, Callable
from urllib.parse import urlparse

from src.tools.container import ContainerSpec, container_reachable_url, docker_run_command
from src.tools.contract import RateLimit, ToolManifest, ToolResult
from src.tools.execution import make_subprocess_executor

TOOL_ID = "nuclei"
DEFAULT_BINARY = "nuclei"

# 发往**目标站**的默认请求速率（每分钟）。这是目标侧负载控制（验收标准 §4），
# 与 SDD-04 §5 的 manifest.rate_limit（工具**调用**速率，保护服务）是两个独立旋钮。
# 取值保守：1 次/秒。需要更快时由调用方按站点通过 params["target_rate_limit"] 显式提高。
DEFAULT_TARGET_RATE_PER_MINUTE = 60

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


def rate_limit_args(requests_per_minute: int) -> list[str]:
    """把**发往目标站**的请求速率（每分钟）换算为 nuclei 的限速参数。

    两个易混点，都在注释里钉住：

    1. **单位**：nuclei 的 `-rate-limit` 是**每秒**（默认 150），而我们要表达的是每分钟。
       直接透传会把速率放大 60 倍（实测把 DVWA 打到无响应）。这里统一用
       `-rate-limit 1 -rate-limit-duration <秒>s` 精确表达每分钟速率。
    2. **语义**：本参数是**目标侧负载控制**（验收标准 §4「目标负载受控」），
       与 SDD-04 §5 的 `manifest.rate_limit` **不是同一个东西**——后者是
       **工具调用**速率（`ToolRegistry` 的令牌桶，保护服务自身）。
       两者必须来自不同的旋钮，否则调高工具调用配额会顺带把目标打爆。
    """
    if requests_per_minute <= 0:
        raise ValueError("requests_per_minute 必须为正数")
    if requests_per_minute % 60 == 0:
        # 能被 60 整除时用整数倍秒速率：精确且可读（如 1800/分钟 → 30 次/秒）
        return [
            "-rate-limit",
            str(requests_per_minute // 60),
            "-rate-limit-duration",
            "1s",
        ]
    return [
        "-rate-limit",
        "1",
        "-rate-limit-duration",
        f"{60 / requests_per_minute:.12g}s",
    ]


def _docker_kill(container_name: str) -> None:
    """尽力终止指定容器；不等待、不抛错（清理属尽力而为）。"""
    subprocess.run(
        ["docker", "kill", container_name],
        capture_output=True,
        text=True,
        check=False,
    )


class NucleiAdapter:
    """Nuclei 工具适配器，实现 SDD-04 契约的 `manifest` / `non_destructive_check` / `run`。"""

    def __init__(
        self,
        *,
        executor: Callable[[list[str], int], str] | None = None,
        binary: str = DEFAULT_BINARY,
        container: ContainerSpec | None = None,
        stderr_log=None,
        container_terminator: Callable[[str], None] | None = None,
    ) -> None:
        self._binary = binary
        self._executor = executor or make_subprocess_executor(stderr_log=stderr_log)
        self._container = container
        self._terminate = container_terminator or _docker_kill
        self.manifest = ToolManifest(
            id=TOOL_ID,
            name="Nuclei",
            description="模板化漏洞与弱配置检测；仅发送检测请求，不利用、不注入（非破坏）",
            version="0.1.0",
            categories=["VULN", "WEAK_CONFIG"],
            execution_mode="container" if container else "local",
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

    def build_command(
        self, target: str, params: dict[str, Any], *, spec: ContainerSpec | None = None
    ) -> list[str]:
        """构造 nuclei 调用命令；始终带非破坏与限速参数。

        容器模式下：目标改写为容器可达地址，整条命令经 `docker run` 包裹。
        `spec` 用于按次覆盖容器参数（`run()` 借此注入唯一容器名以便超时后终止）。
        """
        container_spec = spec if spec is not None else self._container
        effective_target = container_reachable_url(target) if container_spec else target
        args = ["-u", effective_target, "-jsonl"]
        if params.get("silent", True):
            # 诊断时可传 silent=False：nuclei 的 INFO/WRN 走 stderr，stdout 仍是纯 JSONL
            args.append("-silent")
        args += [
            "-no-interactsh",
            "-etags",
            _EXCLUDED_TAGS,
            *rate_limit_args(
                int(params.get("target_rate_limit", DEFAULT_TARGET_RATE_PER_MINUTE))
            ),
            "-timeout",
            str(params.get("per_request_timeout", 10)),
        ]
        for template in params.get("templates") or []:
            args += ["-t", str(template)]

        if container_spec:
            return docker_run_command(container_spec, args)
        return [self._binary, *args]

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

        # 容器模式：给本次运行一个唯一容器名，超时/异常时据此终止它
        container_name = None
        container_spec = self._container
        if container_spec is not None:
            container_name = f"anheng-{TOOL_ID}-{uuid.uuid4().hex[:12]}"
            container_spec = replace(container_spec, name=container_name)

        command = self.build_command(target, params, spec=container_spec)
        try:
            raw = self._executor(command, self.manifest.timeout_seconds)
            return ToolResult(
                tool_id=TOOL_ID, status="SUCCESS", findings=self.parse_jsonl(raw)
            )
        except FileNotFoundError:
            failure = ToolResult(
                tool_id=TOOL_ID,
                status="FAILED",
                error=f"未找到可执行文件：{self._binary}，请先安装 {self.manifest.name}",
            )
        except subprocess.TimeoutExpired:
            failure = ToolResult(
                tool_id=TOOL_ID,
                status="TIMEOUT",
                error=f"执行超时（{self.manifest.timeout_seconds}s）",
            )
        except OSError as exc:
            failure = ToolResult(tool_id=TOOL_ID, status="FAILED", error=f"执行失败：{exc}")
        except Exception as exc:  # 兜底：工具异常不得冒泡打断流水线（SDD-01 §5.1 降级）
            failure = ToolResult(tool_id=TOOL_ID, status="FAILED", error=str(exc))

        # 未跑成功即确保容器被终止：否则客户端放弃后，容器里的扫描仍在持续请求目标
        # （违反验收标准 §4「目标负载受控」）。成功路径不必处理——`--rm` 会自行清理。
        self._terminate_quietly(container_name)
        return failure

    def _terminate_quietly(self, container_name: str | None) -> None:
        if not container_name:
            return
        try:
            self._terminate(container_name)
        except Exception:  # 尽力而为：清理失败不得影响结果返回
            pass
