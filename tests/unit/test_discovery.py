"""页面发现模块单测（T14）。

规格依据：
- SRS-02 F2.1 页面与资源发现（范围/深度约束、去重、内容摘要与哈希）
- SRS-02 F2.2 发现范围控制（域名白名单 + 路径前缀）
- SRS-02 F2.3 发现去重与增量（URL 规范化 + 内容哈希）
- SDD-01 §5.4 资产与页面发现
- SDD-02 §4.3 site_page（normalized_url / content_hash / depth / status）

说明：夹具为进程内真实 HTTP 服务（非 mock），不需要外部网络与靶场。
运行：python tests/unit/test_discovery.py -v
"""

import hashlib
import http.server
import pathlib
import sys
import threading
import unittest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from services.discovery import (  # noqa: E402
    Page,
    Scope,
    content_hash,
    discover,
    is_in_scope,
    normalize_url,
)

# 夹具站点：首页含外链、重复链接；深度链 / → /a → /b → /c
SITE = {
    "/": (
        "<html><head><title>Home</title></head><body>"
        "<a href='/a.html'>A</a>"
        "<a href='/a.html'>A duplicate</a>"
        "<a href='http://example.invalid/ext'>external</a>"
        "<a href='/b.html'>B</a>"
        "</body></html>"
    ).encode("utf-8"),
    "/a.html": b"<html><head><title>A</title></head><body><a href='/b.html'>B</a></body></html>",
    "/b.html": b"<html><head><title>B</title></head><body><a href='/c.html'>C</a></body></html>",
    "/c.html": b"<html><head><title>C</title></head><body></body></html>",
}


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (stdlib 命名)
        body = SITE.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默访问日志
        pass


class DiscoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}/"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    # ---- URL 规范化（SRS-02 F2.3） ----

    def test_normalize_url_lowercases_host_and_drops_default_port(self):
        self.assertEqual(
            normalize_url("http://Example.com:80/a"),
            "http://example.com/a",
        )

    def test_normalize_url_sorts_query_and_drops_fragment(self):
        self.assertEqual(
            normalize_url("http://example.com/a?b=2&a=1#frag"),
            "http://example.com/a?a=1&b=2",
        )

    def test_normalize_url_defaults_empty_path_to_root(self):
        self.assertEqual(normalize_url("https://example.com"), "https://example.com/")
        self.assertEqual(normalize_url("https://example.com:443"), "https://example.com/")

    # ---- 授权范围校验（SRS-02 F2.2） ----

    def test_is_in_scope_rejects_other_domain(self):
        scope = Scope(domain="127.0.0.1", scope_type="WHOLE")
        self.assertTrue(is_in_scope("http://127.0.0.1:8080/x", scope))
        self.assertFalse(is_in_scope("http://evil.test/x", scope))

    def test_is_in_scope_enforces_path_prefix(self):
        scope = Scope(domain="127.0.0.1", scope_type="PATH", scope_value="/dvwa")
        self.assertTrue(is_in_scope("http://127.0.0.1:8080/dvwa/login.php", scope))
        self.assertTrue(is_in_scope("http://127.0.0.1:8080/dvwa", scope))
        self.assertFalse(is_in_scope("http://127.0.0.1:8080/dvwaX", scope))
        self.assertFalse(is_in_scope("http://127.0.0.1:8080/other", scope))

    def test_is_in_scope_allows_subdomains_of_authorized_domain(self):
        scope = Scope(domain="example.com", scope_type="SUBDOMAIN")
        self.assertTrue(is_in_scope("http://example.com/", scope))
        self.assertTrue(is_in_scope("http://a.example.com/", scope))
        self.assertFalse(is_in_scope("http://a.example.com.evil.net/", scope))

    # ---- 内容哈希（SRS-02 F2.1 / F2.3） ----

    def test_content_hash_is_sha256_hex_of_text(self):
        text = "页面内容"
        self.assertEqual(content_hash(text), hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(len(content_hash(text)), 64)

    # ---- 发现主流程（SRS-02 F2.1） ----

    def test_discover_respects_depth_scope_and_dedup(self):
        pages = discover(self.base_url, Scope("127.0.0.1", "WHOLE"), max_depth=1)
        paths = {normalize_url(p.url).split(str(self.server.server_address[1]))[-1] for p in pages}
        self.assertEqual(paths, {"/", "/a.html", "/b.html"})  # /c.html 在深度 2，不收录
        self.assertNotIn("http://example.invalid/ext", {p.url for p in pages})  # 超范围外链被丢弃
        self.assertEqual(len(pages), len({p.normalized_url for p in pages}))  # 重复链接被去重

    def test_discover_records_title_hash_and_depth(self):
        pages = discover(self.base_url, Scope("127.0.0.1", "WHOLE"), max_depth=1)
        root = next(p for p in pages if p.url == self.base_url)
        self.assertEqual(root.title, "Home")
        self.assertEqual(root.content_hash, hashlib.sha256(SITE["/"]).hexdigest())
        self.assertEqual(root.depth, 0)
        self.assertEqual(root.status, "ACTIVE")
        self.assertIsInstance(root, Page)

    def test_discover_respects_max_pages(self):
        pages = discover(self.base_url, Scope("127.0.0.1", "WHOLE"), max_depth=5, max_pages=1)
        self.assertEqual(len(pages), 1)

    def test_discover_spaces_requests_by_delay(self):
        slept = []
        pages = discover(
            self.base_url,
            Scope("127.0.0.1", "WHOLE"),
            max_depth=1,
            delay_seconds=0.5,
            sleeper=slept.append,
        )
        # 首次请求不等待，其后每次请求前等待一次
        self.assertEqual(slept, [0.5] * (len(pages) - 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
