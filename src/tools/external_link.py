"""外链 / 挂马检测器（T19）。

规格依据：
- SRS-02 F3.3 恶意外链 / 挂马检测：外链提取 → 黑名单/信誉库比对 + 特征规则（恶意脚本、跳转、iframe、异常外联）；
- SDD-01 §5.3 自研检测器（external_link）、§5.5 结果归一化（Finding 标准结构）；
- SDD-02 §4.6 finding（finding_type=MALICIOUS_LINK）。

检测规则（rule_id → category → 判定 → 级别）：
- BLACKLISTED_LINK      MALICIOUS_LINK    外链（a/link）域名命中黑名单        HIGH
- BLACKLISTED_SCRIPT    MALICIOUS_SCRIPT  外部脚本 src 命中黑名单             HIGH
- HIDDEN_IFRAME         MALICIOUS_SCRIPT  隐藏 iframe（0/1 尺寸或 CSS 隐藏）   HIGH
- EXTERNAL_IFRAME       MALICIOUS_SCRIPT  可见 iframe 指向外部域名            MEDIUM
- META_REFRESH_EXTERNAL MALICIOUS_SCRIPT  meta refresh 跳转到外部             HIGH
- JS_REDIRECT_EXTERNAL  MALICIOUS_SCRIPT  脚本跳转到外部地址                  HIGH
- OBFUSCATED_SCRIPT     MALICIOUS_SCRIPT  eval + 解码函数构成的混淆脚本       MEDIUM

category 归类口径：页面**指向外部**的链接归 MALICIOUS_LINK；被注入/执行的**脚本、框架、跳转**归 MALICIOUS_SCRIPT。
对齐 F 组 T40 样例集 v1.0.0（B-01/B-02 = MALICIOUS_LINK，B-03/B-04 = MALICIOUS_SCRIPT）。

合规：仅解析页面文本，不发起对目标的额外请求、不修改目标内容（工具清单 §2.2 非破坏声明）。
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

# 非可导航协议：不计入外链
_SKIP_SCHEMES = ("javascript", "mailto", "tel", "data")
# 脚本内跳转赋值：location.href = "..." / window.location = '...' / document.location.href = ...
_JS_REDIRECT_RE = re.compile(
    r"""(?:(?:window|document)\s*\.\s*)?location(?:\s*\.\s*href)?\s*=\s*['"]([^'"]+)['"]"""
)
_META_URL_RE = re.compile(r"""url\s*=\s*['"]?([^'";\s]+)""", re.IGNORECASE)
_OBFUSCATION_MARKERS = ("fromcharcode", "unescape(", "atob(")
_EVIDENCE_MAX = 160

# rule_id → (category, severity, title, impact, suggestion)
_RULES: dict[str, tuple[str, str, str, str, str]] = {
    "BLACKLISTED_LINK": (
        "MALICIOUS_LINK",
        "HIGH",
        "外链命中黑名单域名",
        "页面指向已知恶意/失陷域名，可能用于导流、挂马或数据外发。",
        "移除指向该域名的链接；核查页面是否被注入，排查上传/包含类入口。",
    ),
    "BLACKLISTED_SCRIPT": (
        "MALICIOUS_SCRIPT",
        "HIGH",
        "外部脚本命中黑名单域名",
        "页面加载来自已知恶意域名的脚本，脚本可完全控制页面行为（窃取凭据、注入内容、跳转）。",
        "立即移除该脚本引用；核查页面是否被注入，排查上传/包含类入口并轮换后台凭据。",
    ),
    "HIDDEN_IFRAME": (
        "MALICIOUS_SCRIPT",
        "HIGH",
        "页面存在隐藏 iframe",
        "隐藏 iframe 是挂马的典型手法，可能在用户无感知下加载恶意内容或窃取会话。",
        "定位并移除该 iframe；对比基线确认注入时间点，排查模板/编辑器/上传入口。",
    ),
    "EXTERNAL_IFRAME": (
        "MALICIOUS_SCRIPT",
        "MEDIUM",
        "iframe 指向外部域名",
        "外部 iframe 会把第三方内容引入本站，存在内容失控与外链风险。",
        "确认是否为业务所需；非必要则移除，必要则纳入外链白名单并定期复核。",
    ),
    "META_REFRESH_EXTERNAL": (
        "MALICIOUS_SCRIPT",
        "HIGH",
        "meta refresh 跳转到外部站点",
        "访问者会被自动跳转到外部站点，属典型挂马/劫持特征。",
        "移除指向外部的自动跳转；核对页面完整性并排查注入入口。",
    ),
    "JS_REDIRECT_EXTERNAL": (
        "MALICIOUS_SCRIPT",
        "HIGH",
        "脚本跳转到外部地址",
        "脚本将访问者重定向至外部站点，属典型挂马/劫持特征。",
        "移除脚本中的外链跳转；核对页面完整性并排查注入入口。",
    ),
    "OBFUSCATED_SCRIPT": (
        "MALICIOUS_SCRIPT",
        "MEDIUM",
        "页面存在混淆脚本",
        "混淆脚本常用于隐藏恶意行为，来源不可信时风险较高。",
        "人工复核该脚本来源与用途；非业务脚本应移除并排查注入入口。",
    ),
}


@dataclass
class ExternalRef:
    """页面中的引用（链接/脚本/框架/样式）。"""

    kind: str  # a / script / iframe / link
    url: str
    host: str


@dataclass
class MaliciousFinding:
    """外链/挂马检测结果（归一化后对应 SDD-02 finding）。

    finding_type 由规则决定：外链类为 MALICIOUS_LINK，脚本/框架/跳转类为 MALICIOUS_SCRIPT。
    """

    rule_id: str
    finding_type: str
    url: str
    severity: str
    title: str
    evidence: str
    impact: str
    suggestion: str
    tool_name: str = "external-link-detector"


def _resolve(raw: str, base_url: str) -> str | None:
    """把页面内的相对/绝对引用解析为绝对 URL；非可导航协议返回 None。"""
    raw = (raw or "").strip()
    if not raw or raw.startswith("#"):
        return None
    if urlparse(raw).scheme.lower() in _SKIP_SCHEMES:
        return None
    return urljoin(base_url, raw)


def _is_hidden(attrs: dict) -> bool:
    """iframe 是否隐藏：hidden 属性 / 0-1 尺寸 / CSS 隐藏。"""
    if "hidden" in attrs:
        return True
    for dim in ("width", "height"):
        if attrs.get(dim, "").strip() in {"0", "1"}:
            return True
    style = attrs.get("style", "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


class _Scanner(HTMLParser):
    """单遍解析页面：收集引用、内联脚本、meta refresh、隐藏 iframe。"""

    def __init__(self):
        super().__init__()
        self.raw_refs: list[tuple[str, str]] = []
        self.scripts: list[str] = []
        self.meta_refresh: list[str] = []
        self.hidden_frames: list[str] = []
        self._in_script = False
        self._script_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script":
            self._in_script = True
            self._script_buf = []
            if attrs.get("src"):
                self.raw_refs.append(("script", attrs["src"]))
        elif tag == "iframe":
            if attrs.get("src"):
                self.raw_refs.append(("iframe", attrs["src"]))
                if _is_hidden(attrs):
                    self.hidden_frames.append(attrs["src"])
        elif tag == "a":
            if attrs.get("href"):
                self.raw_refs.append(("a", attrs["href"]))
        elif tag == "link":
            if attrs.get("href"):
                self.raw_refs.append(("link", attrs["href"]))
        elif tag == "meta":
            if attrs.get("http-equiv", "").lower() == "refresh" and attrs.get("content"):
                self.meta_refresh.append(attrs["content"])

    def handle_endtag(self, tag):
        if tag == "script" and self._in_script:
            self._in_script = False
            self.scripts.append("".join(self._script_buf))

    def handle_data(self, data):
        if self._in_script:
            self._script_buf.append(data)


def extract_refs(html: str, base_url: str) -> list[ExternalRef]:
    """提取页面中的 a/script/iframe/link 引用（绝对 URL + host）。"""
    scanner = _Scanner()
    scanner.feed(html)
    refs: list[ExternalRef] = []
    for kind, raw in scanner.raw_refs:
        target = _resolve(raw, base_url)
        if target is None:
            continue
        refs.append(ExternalRef(kind=kind, url=target, host=(urlparse(target).hostname or "").lower()))
    return refs


def _finding(rule_id: str, target_url: str, page_url: str, evidence: str) -> MaliciousFinding:
    """构造 Finding：url 指向命中的目标资源，evidence 记录宿主页面便于定位。"""
    category, severity, title, impact, suggestion = _RULES[rule_id]
    full_evidence = f"所在页面：{page_url}\n{evidence}"
    if len(full_evidence) > _EVIDENCE_MAX:
        full_evidence = full_evidence[:_EVIDENCE_MAX] + "…"
    return MaliciousFinding(
        rule_id=rule_id,
        finding_type=category,
        url=target_url,
        severity=severity,
        title=f"{title}：{target_url}",
        evidence=full_evidence,
        impact=impact,
        suggestion=suggestion,
    )


def scan_page(html: str, url: str, *, site_host: str, blacklist=()) -> list[MaliciousFinding]:
    """扫描单个页面，输出外链/挂马 Finding。

    :param site_host: 本站域名；与之不同的引用视为外链
    :param blacklist: 外链黑名单域名集合（信誉库比对，SRS-02 F3.3）
    """
    site_host = (site_host or "").lower()
    blacklist = {h.lower() for h in blacklist}

    scanner = _Scanner()
    scanner.feed(html)
    findings: list[MaliciousFinding] = []

    def is_external(target: str) -> bool:
        return (urlparse(target).hostname or "").lower() != site_host

    # 1) 黑名单比对（仅外链参与）：脚本 src 归 MALICIOUS_SCRIPT，链接/样式归 MALICIOUS_LINK
    for kind, raw in scanner.raw_refs:
        target = _resolve(raw, url)
        if target is None or not is_external(target):
            continue
        host = (urlparse(target).hostname or "").lower()
        if host not in blacklist:
            continue
        if kind == "script":
            findings.append(
                _finding(
                    "BLACKLISTED_SCRIPT", target, url, f"外部脚本域名 {host} 命中黑名单：{target}"
                )
            )
        else:
            findings.append(
                _finding("BLACKLISTED_LINK", target, url, f"外链域名 {host} 命中黑名单：{target}")
            )

    # 2) 隐藏 iframe 优先于外部 iframe 报告，避免同一 iframe 重复计数
    hidden_targets = set()
    for raw in scanner.hidden_frames:
        target = _resolve(raw, url)
        if target is None:
            continue
        hidden_targets.add(target)
        findings.append(
            _finding("HIDDEN_IFRAME", target, url, f"隐藏 iframe（尺寸 0/1 或 CSS 隐藏）：{target}")
        )

    for kind, raw in scanner.raw_refs:
        if kind != "iframe":
            continue
        target = _resolve(raw, url)
        if target is None or target in hidden_targets or not is_external(target):
            continue
        host = (urlparse(target).hostname or "").lower()
        findings.append(
            _finding("EXTERNAL_IFRAME", target, url, f"iframe 指向外部域名 {host}：{target}")
        )

    # 3) meta refresh 外跳
    for content in scanner.meta_refresh:
        matched = _META_URL_RE.search(content)
        target = _resolve(matched.group(1), url) if matched else None
        if target and is_external(target):
            findings.append(
                _finding("META_REFRESH_EXTERNAL", target, url, f"meta refresh 跳转外部地址：{target}")
            )

    # 4) 脚本外跳
    for script in scanner.scripts:
        for matched in _JS_REDIRECT_RE.finditer(script):
            target = _resolve(matched.group(1), url)
            if target and is_external(target):
                findings.append(
                    _finding("JS_REDIRECT_EXTERNAL", target, url, f"脚本跳转到外部地址：{target}")
                )

    # 5) 混淆脚本
    for script in scanner.scripts:
        compact = script.replace(" ", "").lower()
        if "eval(" in compact and any(m in compact for m in _OBFUSCATION_MARKERS):
            # 内联脚本没有独立资源地址，目标即为所在页面
            findings.append(
                _finding("OBFUSCATED_SCRIPT", url, url, f"混淆脚本片段：{script.strip()[:80]}")
            )

    return findings
