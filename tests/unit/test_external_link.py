"""外链/挂马检测器单测（T19）。

规格依据：
- SRS-02 F3.3 恶意外链 / 挂马检测（外链提取 → 黑名单比对 + 特征规则；可疑外链/挂马特征被识别并给出证据）
- SDD-01 §5.3 自研检测器（external_link）、§5.5 归一化
- SDD-02 §4.6 finding（finding_type=MALICIOUS_LINK）

运行：python tests/unit/test_external_link.py -v
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tools.external_link import ExternalRef, extract_refs, scan_page  # noqa: E402

BASE = "http://site.test/"
SITE_HOST = "site.test"

HTML_REFS = """
<html>
  <head><link rel="stylesheet" href="/style.css"></head>
  <body>
    <a href="/about.html">about</a>
    <a href="https://evil.test/bad.js">bad</a>
    <script src="https://cdn.example.org/lib.js"></script>
    <iframe src="https://ads.tracker.test/frame"></iframe>
  </body>
</html>
"""

CLEAN_HTML = """
<html><head><title>ok</title></head><body>
  <a href="/about.html">About</a>
  <script src="/app.js"></script>
</body></html>
"""


def _rules(findings) -> set:
    return {f.rule_id for f in findings}


def _rule(findings, rule_id):
    return next(f for f in findings if f.rule_id == rule_id)


class ExtractRefsTest(unittest.TestCase):
    def test_collects_a_link_script_and_iframe_refs(self):
        refs = extract_refs(HTML_REFS, BASE)
        self.assertEqual({r.kind for r in refs}, {"link", "a", "script", "iframe"})
        urls = {r.url for r in refs}
        self.assertIn("http://site.test/style.css", urls)
        self.assertIn("https://evil.test/bad.js", urls)
        self.assertIn("https://cdn.example.org/lib.js", urls)
        self.assertIn("https://ads.tracker.test/frame", urls)
        self.assertIsInstance(refs[0], ExternalRef)

    def test_records_host_for_each_ref(self):
        hosts = {r.host for r in extract_refs(HTML_REFS, BASE)}
        self.assertEqual(
            hosts, {"site.test", "evil.test", "cdn.example.org", "ads.tracker.test"}
        )

    def test_skips_non_navigable_hrefs(self):
        html = """
        <html><body>
          <a href="#top">frag</a>
          <a href="mailto:a@b.test">mail</a>
          <a href="javascript:void(0)">js</a>
          <a href="tel:+8610000">tel</a>
        </body></html>
        """
        self.assertEqual(extract_refs(html, BASE), [])


class ScanPageTest(unittest.TestCase):
    # ---- 基线：干净页面不产生误报（SRS-02 F3.3 验收） ----

    def test_clean_page_produces_no_finding(self):
        self.assertEqual(scan_page(CLEAN_HTML, BASE, site_host=SITE_HOST), [])

    # ---- 黑名单外链（SRS-02 F3.3「黑名单/信誉库比对」） ----

    def test_blacklisted_external_link_is_flagged(self):
        findings = scan_page(HTML_REFS, BASE, site_host=SITE_HOST, blacklist={"evil.test"})
        finding = _rule(findings, "BLACKLISTED_LINK")
        self.assertEqual(finding.severity, "HIGH")
        self.assertIn("evil.test", finding.evidence)

    def test_same_host_link_is_not_a_blacklist_hit(self):
        findings = scan_page(CLEAN_HTML, BASE, site_host=SITE_HOST, blacklist={"site.test"})
        self.assertNotIn("BLACKLISTED_LINK", _rules(findings))

    # ---- 归一化字段指向（SDD-02 finding.url = 目标 URL；SDD-03 §5.2 样例） ----

    def test_finding_url_points_to_the_offending_resource(self):
        findings = scan_page(HTML_REFS, BASE, site_host=SITE_HOST, blacklist={"evil.test"})
        self.assertEqual(_rule(findings, "BLACKLISTED_LINK").url, "https://evil.test/bad.js")

    def test_finding_url_points_to_offending_iframe(self):
        html = '<html><body><iframe src="https://evil.test/f" width="0" height="0"></iframe></body></html>'
        findings = scan_page(html, BASE, site_host=SITE_HOST)
        self.assertEqual(_rule(findings, "HIDDEN_IFRAME").url, "https://evil.test/f")

    def test_finding_title_names_the_offending_target(self):
        findings = scan_page(HTML_REFS, BASE, site_host=SITE_HOST, blacklist={"evil.test"})
        self.assertIn("evil.test", _rule(findings, "BLACKLISTED_LINK").title)

    def test_finding_evidence_locates_the_hosting_page(self):
        findings = scan_page(HTML_REFS, BASE, site_host=SITE_HOST, blacklist={"evil.test"})
        self.assertIn(BASE, _rule(findings, "BLACKLISTED_LINK").evidence)

    # ---- 挂马特征：隐藏 iframe ----

    def test_hidden_iframe_by_zero_size_is_flagged(self):
        html = '<html><body><iframe src="/x" width="0" height="0"></iframe></body></html>'
        findings = scan_page(html, BASE, site_host=SITE_HOST)
        self.assertEqual(_rule(findings, "HIDDEN_IFRAME").severity, "HIGH")

    def test_hidden_iframe_by_style_is_flagged(self):
        html = '<html><body><iframe src="/x" style="display:none"></iframe></body></html>'
        findings = scan_page(html, BASE, site_host=SITE_HOST)
        self.assertIn("HIDDEN_IFRAME", _rules(findings))

    def test_visible_iframe_is_not_flagged_as_hidden(self):
        html = '<html><body><iframe src="/x" width="600" height="400"></iframe></body></html>'
        self.assertNotIn("HIDDEN_IFRAME", _rules(scan_page(html, BASE, site_host=SITE_HOST)))

    def test_external_iframe_is_flagged(self):
        html = '<html><body><iframe src="https://evil.test/f" width="600" height="400"></iframe></body></html>'
        findings = scan_page(html, BASE, site_host=SITE_HOST)
        finding = _rule(findings, "EXTERNAL_IFRAME")
        self.assertEqual(finding.severity, "MEDIUM")
        self.assertIn("evil.test", finding.evidence)

    def test_same_host_iframe_is_not_flagged_as_external(self):
        html = '<html><body><iframe src="/local" width="600" height="400"></iframe></body></html>'
        self.assertNotIn("EXTERNAL_IFRAME", _rules(scan_page(html, BASE, site_host=SITE_HOST)))

    # ---- 挂马特征：跳转 ----

    def test_meta_refresh_to_external_is_flagged(self):
        html = '<html><head><meta http-equiv="refresh" content="0;url=https://evil.test/"></head></html>'
        findings = scan_page(html, BASE, site_host=SITE_HOST)
        self.assertEqual(_rule(findings, "META_REFRESH_EXTERNAL").severity, "HIGH")

    def test_meta_refresh_internal_is_not_flagged(self):
        html = '<html><head><meta http-equiv="refresh" content="5;url=/home"></head></html>'
        self.assertNotIn("META_REFRESH_EXTERNAL", _rules(scan_page(html, BASE, site_host=SITE_HOST)))

    def test_js_redirect_to_external_is_flagged(self):
        html = '<html><body><script>window.location.href = "https://evil.test/x";</script></body></html>'
        findings = scan_page(html, BASE, site_host=SITE_HOST)
        self.assertIn("evil.test", _rule(findings, "JS_REDIRECT_EXTERNAL").evidence)

    def test_js_redirect_internal_is_not_flagged(self):
        html = '<html><body><script>location.href = "/home";</script></body></html>'
        self.assertNotIn("JS_REDIRECT_EXTERNAL", _rules(scan_page(html, BASE, site_host=SITE_HOST)))

    # ---- 挂马特征：混淆脚本 ----

    def test_obfuscated_script_is_flagged(self):
        html = "<html><body><script>eval(String.fromCharCode(97,98,99))</script></body></html>"
        findings = scan_page(html, BASE, site_host=SITE_HOST)
        self.assertEqual(_rule(findings, "OBFUSCATED_SCRIPT").severity, "MEDIUM")

    def test_ordinary_script_is_not_flagged_as_obfuscated(self):
        html = '<html><body><script>console.log("hello")</script></body></html>'
        self.assertNotIn("OBFUSCATED_SCRIPT", _rules(scan_page(html, BASE, site_host=SITE_HOST)))

    # ---- 归一化字段（SDD-01 §5.5 / SDD-02 finding） ----

    def test_findings_carry_rule_metadata_and_suggestion(self):
        findings = scan_page(HTML_REFS, BASE, site_host=SITE_HOST, blacklist={"evil.test"})
        self.assertTrue(findings)
        for finding in findings:
            self.assertIn(finding.finding_type, {"MALICIOUS_LINK", "MALICIOUS_SCRIPT"})
            self.assertEqual(finding.tool_name, "external-link-detector")
            self.assertTrue(finding.title)
            self.assertTrue(finding.suggestion)

    # ---- category 归类（对齐 F 组样例 v1.0.0：B-01/B-02=MALICIOUS_LINK，B-03/B-04=MALICIOUS_SCRIPT） ----

    def test_external_link_is_categorized_as_malicious_link(self):
        findings = scan_page(HTML_REFS, BASE, site_host=SITE_HOST, blacklist={"evil.test"})
        self.assertEqual(_rule(findings, "BLACKLISTED_LINK").finding_type, "MALICIOUS_LINK")

    def test_external_script_src_is_categorized_as_malicious_script(self):
        findings = scan_page(HTML_REFS, BASE, site_host=SITE_HOST, blacklist={"cdn.example.org"})
        self.assertEqual(_rule(findings, "BLACKLISTED_SCRIPT").finding_type, "MALICIOUS_SCRIPT")
        self.assertEqual(_rule(findings, "BLACKLISTED_SCRIPT").severity, "HIGH")

    def test_hidden_iframe_is_categorized_as_malicious_script(self):
        html = '<html><body><iframe src="/x" width="1" height="1"></iframe></body></html>'
        findings = scan_page(html, BASE, site_host=SITE_HOST)
        self.assertEqual(_rule(findings, "HIDDEN_IFRAME").finding_type, "MALICIOUS_SCRIPT")

    def test_external_iframe_is_categorized_as_malicious_script(self):
        html = '<html><body><iframe src="https://evil.test/f" width="600" height="400"></iframe></body></html>'
        findings = scan_page(html, BASE, site_host=SITE_HOST)
        self.assertEqual(_rule(findings, "EXTERNAL_IFRAME").finding_type, "MALICIOUS_SCRIPT")

    def test_obfuscated_script_is_categorized_as_malicious_script(self):
        html = "<html><body><script>eval(atob('eA=='))</script></body></html>"
        findings = scan_page(html, BASE, site_host=SITE_HOST)
        self.assertEqual(_rule(findings, "OBFUSCATED_SCRIPT").finding_type, "MALICIOUS_SCRIPT")

    def test_redirect_markers_are_categorized_as_malicious_script(self):
        meta = '<html><head><meta http-equiv="refresh" content="0;url=https://evil.test/"></head></html>'
        js = '<html><body><script>location.href = "https://evil.test/x";</script></body></html>'
        self.assertEqual(
            _rule(scan_page(meta, BASE, site_host=SITE_HOST), "META_REFRESH_EXTERNAL").finding_type,
            "MALICIOUS_SCRIPT",
        )
        self.assertEqual(
            _rule(scan_page(js, BASE, site_host=SITE_HOST), "JS_REDIRECT_EXTERNAL").finding_type,
            "MALICIOUS_SCRIPT",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
