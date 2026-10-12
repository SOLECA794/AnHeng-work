"""篡改检测器（T18）。

规格依据：
- SRS-02 F3.4 页面篡改检测：与基线内容哈希比对 → 差异分析 → 标记并给出差异证据；
- SRS-02 F5.2 基线对比：基线采集 → 每次巡检对比 → 差异记录；
- SDD-01 §5.6 分级模型：影响面因子（首页/登录页/核心路径）抬高严重度；
- SDD-02 §4.8 baseline（仅存 content_hash）、§4.6 finding（finding_type=TAMPER）。

设计说明：
- 判定核心为哈希比对（与 baseline 表结构一致，无需存储全文）；
- 当调用方额外提供基线/当前文本时，补充行级差异摘要作为证据（SRS-02 F3.4「给出差异证据」）；
- 首次巡检无基线的页面不判定为篡改（基线由本轮建立）。
"""

import difflib
from dataclasses import dataclass
from urllib.parse import urlparse

# 核心路径前缀：命中即按影响面抬高到 HIGH（SDD-01 §5.6）
CORE_PATH_PREFIXES = (
    "/index",
    "/login",
    "/signin",
    "/admin",
    "/manage",
    "/home",
    "/default",
    "/main",
)

IMPACT = "页面内容被非预期修改，可能存在挂马、暗链或未授权变更，影响站点公信力与合规。"
SUGGESTION = (
    "比对差异内容确认是否为授权变更；非授权变更应立即回滚，"
    "并排查上传/文件包含/弱口令等入侵入口、轮换后台凭据。"
)


@dataclass
class Baseline:
    """页面基线（对应 SDD-02 baseline）。"""

    url: str
    content_hash: str
    page_id: int | None = None


@dataclass
class TamperFinding:
    """篡改检测结果（归一化后对应 SDD-02 finding，finding_type=TAMPER）。"""

    url: str
    severity: str
    title: str
    evidence: str
    impact: str
    suggestion: str
    finding_type: str = "TAMPER"
    tool_name: str = "tamper-detector"
    page_id: int | None = None


def severity_for_url(url: str) -> str:
    """按页面重要性定级：首页/登录页/管理页等核心路径为 HIGH，其余 MEDIUM。"""
    path = (urlparse(url).path or "/").lower()
    if path == "/" or path.startswith(CORE_PATH_PREFIXES):
        return "HIGH"
    return "MEDIUM"


def diff_summary(baseline_text: str, current_text: str, max_lines: int = 5) -> str:
    """行级差异摘要（证据用）：无差异返回空串，超限截断并标注。"""
    changed: list[str] = []
    for line in difflib.unified_diff(
        baseline_text.splitlines(), current_text.splitlines(), lineterm="", n=0
    ):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line[:1] in "+-":
            changed.append(line)
        if len(changed) >= max_lines:
            break

    if not changed:
        return ""
    summary = "\n".join(changed)
    if len(changed) >= max_lines:
        summary += "\n…（差异已截断，完整差异见原始内容）"
    return summary


def detect_tamper(pages, baselines, *, baseline_texts=None, current_texts=None) -> list[TamperFinding]:
    """比对当前页面与基线，输出篡改 Finding。

    :param pages: 本轮发现的页面（services.discovery.Page 或具 url/normalized_url/content_hash 的对象）
    :param baselines: {url: Baseline} 基线索引
    :param baseline_texts / current_texts: 可选的 {url: 文本}，用于补充行级差异证据
    """
    baseline_texts = baseline_texts or {}
    current_texts = current_texts or {}
    findings: list[TamperFinding] = []

    for page in pages:
        baseline = baselines.get(page.normalized_url) or baselines.get(page.url)
        if baseline is None or baseline.content_hash == page.content_hash:
            continue

        severity = severity_for_url(page.url)
        evidence = [
            f"页面：{page.url}",
            f"基线哈希：{baseline.content_hash}",
            f"当前哈希：{page.content_hash}",
        ]
        if page.url in baseline_texts and page.url in current_texts:
            diff = diff_summary(baseline_texts[page.url], current_texts[page.url])
            if diff:
                evidence.append(f"差异摘要：\n{diff}")

        findings.append(
            TamperFinding(
                url=page.url,
                severity=severity,
                title=f"页面内容与基线不一致（疑似篡改）：{page.url}",
                evidence="\n".join(evidence),
                impact=IMPACT,
                suggestion=SUGGESTION,
                page_id=baseline.page_id,
            )
        )

    return findings
