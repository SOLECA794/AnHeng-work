"""页面发现模块（T14 · 骨架，待实现）。

规格依据：SRS-02 F2、SDD-01 §5.4、SDD-02 §4.3 site_page。
"""

import hashlib
import time
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

_DEFAULT_PORTS = {"http": 80, "https": 443}
USER_AGENT = "learning-agent-discovery/0.1 (authorized-scan)"


@dataclass
class Scope:
    """授权巡检范围（对应 SDD-02 auth_scope）。"""

    domain: str
    scope_type: str = "WHOLE"  # WHOLE / PATH / SUBDOMAIN
    scope_value: str | None = None


@dataclass
class Page:
    """发现的页面（对应 SDD-02 site_page）。"""

    url: str
    normalized_url: str
    title: str | None
    content_hash: str
    depth: int
    status: str = "ACTIVE"


def normalize_url(url: str) -> str:
    """URL 规范化：小写 scheme/host、去默认端口、去片段、查询参数排序。

    用于跨轮次页面去重（SDD-02 site_page.normalized_url 唯一约束）。
    """
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower() or "http"
    host = (parsed.hostname or "").lower()
    netloc = host
    if parsed.port and parsed.port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunparse((scheme, netloc, path, "", query, ""))


def is_in_scope(url: str, scope: Scope) -> bool:
    """授权范围校验：域名白名单 + 路径前缀（SRS-02 F2.2、SDD-01 §5.4）。

    超范围返回 False，调用方须据此丢弃并（在服务层）留痕。
    """
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    domain = scope.domain.lower()

    if scope.scope_type == "SUBDOMAIN":
        if host != domain and not host.endswith("." + domain):
            return False
    elif host != domain:
        return False

    if scope.scope_type == "PATH":
        prefix = (scope.scope_value or "").rstrip("/")
        if prefix:
            path = parsed.path or "/"
            if path != prefix and not path.startswith(prefix + "/"):
                return False
    return True


def content_hash(text: str) -> str:
    """内容哈希：SHA-256 hex（64 位），供篡改对比与增量发现使用。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _LinkExtractor(HTMLParser):
    """提取 <title> 与 <a href>（后续可按需扩展 link/script/img 资源）。"""

    def __init__(self):
        super().__init__()
        self.title: str | None = None
        self.links: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and self.title is None:
            self.title = data.strip()


def discover(base_url, scope, *, max_depth=2, max_pages=200, timeout=10.0,
             delay_seconds=0.0, sleeper=None, client=None) -> list[Page]:
    """广度优先发现授权范围内的页面（SRS-02 F2.1、SDD-01 §5.4）。

    - 超范围 URL 一律丢弃（is_in_scope），不入队、不入库；
    - 以规范化 URL 去重（SRS-02 F2.3）；
    - max_depth 限制发现深度（首页为 0）；max_pages 限制单次页面上限；
    - delay_seconds 为请求间隔（限速，合规要求），首次请求不等待；
    - 单页请求失败/非 HTML 时跳过，不阻塞整体（SDD-01 §5.1 降级）。
    """
    sleep = sleeper if sleeper is not None else time.sleep
    own_client = client is None
    if own_client:
        client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    start = normalize_url(base_url)
    results: list[Page] = []
    seen = {start}
    queue = deque([(start, 0)])
    first = True
    try:
        while queue and len(results) < max_pages:
            url, depth = queue.popleft()
            if not first and delay_seconds > 0:
                sleep(delay_seconds)
            first = False

            try:
                resp = client.get(url)
            except httpx.HTTPError:
                continue
            if resp.status_code != 200:
                continue
            if "html" not in resp.headers.get("content-type", ""):
                continue

            parser = _LinkExtractor()
            parser.feed(resp.text)
            results.append(
                Page(
                    url=url,
                    normalized_url=url,
                    title=parser.title,
                    content_hash=content_hash(resp.text),
                    depth=depth,
                )
            )

            if depth >= max_depth:
                continue
            for href in parser.links:
                target = normalize_url(urljoin(url, href))
                if target in seen or not is_in_scope(target, scope):
                    continue
                seen.add(target)
                queue.append((target, depth + 1))
    finally:
        if own_client:
            client.close()
    return results
