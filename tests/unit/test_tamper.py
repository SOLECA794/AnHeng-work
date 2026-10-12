"""篡改检测器单测（T18）。

规格依据：
- SRS-02 F3.4 页面篡改检测（基线哈希比对 → 差异分析；标记篡改页面并给出差异证据）
- SRS-02 F5.2 基线对比（基线采集 → 每次对比 → 差异记录）
- SDD-01 §5.6 分级模型（影响面因子：首页/登录页/核心路径）
- SDD-02 §4.6 finding（finding_type=TAMPER）、§4.8 baseline

运行：python tests/unit/test_tamper.py -v
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from services.discovery import Page  # noqa: E402
from tools.tamper import (  # noqa: E402
    Baseline,
    diff_summary,
    detect_tamper,
    severity_for_url,
)

HOME = "http://site.test/"
INDEX = "http://site.test/index.html"
LOGIN = "http://site.test/login.php"
ABOUT = "http://site.test/about.html"


def _page(url: str, digest: str) -> Page:
    return Page(url=url, normalized_url=url, title="t", content_hash=digest, depth=0)


def _baseline(url: str, digest: str) -> Baseline:
    return Baseline(url=url, content_hash=digest)


class TamperTest(unittest.TestCase):
    # ---- 比对核心（SRS-02 F3.4） ----

    def test_no_finding_when_content_unchanged(self):
        baselines = {INDEX: _baseline(INDEX, "aaa")}
        self.assertEqual(detect_tamper([_page(INDEX, "aaa")], baselines), [])

    def test_finding_when_content_changed(self):
        baselines = {INDEX: _baseline(INDEX, "aaa")}
        findings = detect_tamper([_page(INDEX, "bbb")], baselines)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.finding_type, "TAMPER")
        self.assertEqual(finding.url, INDEX)
        self.assertIn("aaa", finding.evidence)
        self.assertIn("bbb", finding.evidence)

    def test_page_without_baseline_is_skipped(self):
        # 首次巡检无基线：不判定为篡改（基线由本次建立）
        self.assertEqual(detect_tamper([_page(ABOUT, "bbb")], {}), [])

    def test_finding_is_actionable_with_title_impact_and_suggestion(self):
        findings = detect_tamper([_page(INDEX, "bbb")], {INDEX: _baseline(INDEX, "aaa")})
        finding = findings[0]
        self.assertTrue(finding.title)
        self.assertTrue(finding.impact)
        self.assertTrue(finding.suggestion)
        self.assertEqual(finding.tool_name, "tamper-detector")

    def test_unrelated_page_change_does_not_affect_others(self):
        baselines = {INDEX: _baseline(INDEX, "aaa"), ABOUT: _baseline(ABOUT, "ccc")}
        findings = detect_tamper([_page(INDEX, "aaa"), _page(ABOUT, "ddd")], baselines)
        self.assertEqual([f.url for f in findings], [ABOUT])

    # ---- 分级（SDD-01 §5.6 影响面因子） ----

    def test_severity_high_for_homepage_and_login_pages(self):
        self.assertEqual(severity_for_url(HOME), "HIGH")
        self.assertEqual(severity_for_url(INDEX), "HIGH")
        self.assertEqual(severity_for_url(LOGIN), "HIGH")

    def test_severity_medium_for_ordinary_pages(self):
        self.assertEqual(severity_for_url(ABOUT), "MEDIUM")

    def test_finding_severity_follows_page_importance(self):
        baselines = {INDEX: _baseline(INDEX, "aaa"), ABOUT: _baseline(ABOUT, "ccc")}
        findings = detect_tamper([_page(INDEX, "b1"), _page(ABOUT, "b2")], baselines)
        by_url = {f.url: f.severity for f in findings}
        self.assertEqual(by_url[INDEX], "HIGH")
        self.assertEqual(by_url[ABOUT], "MEDIUM")

    # ---- 差异证据（SRS-02 F3.4「给出差异证据」） ----

    def test_diff_summary_reports_added_and_removed_lines(self):
        diff = diff_summary("alpha\nbeta\ngamma", "alpha\nBETA\ngamma")
        self.assertIn("-beta", diff)
        self.assertIn("+BETA", diff)

    def test_diff_summary_is_empty_when_identical(self):
        self.assertEqual(diff_summary("alpha\nbeta", "alpha\nbeta"), "")

    def test_diff_summary_truncates_to_max_lines(self):
        baseline = "\n".join(f"line-{i}" for i in range(20))
        current = "\n".join(f"changed-{i}" for i in range(20))
        diff = diff_summary(baseline, current, max_lines=3)
        changed = [l for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        self.assertEqual(len(changed), 3)

    def test_evidence_includes_line_diff_when_texts_available(self):
        baselines = {INDEX: _baseline(INDEX, "aaa")}
        findings = detect_tamper(
            [_page(INDEX, "bbb")],
            baselines,
            baseline_texts={INDEX: "<h1>Welcome</h1>"},
            current_texts={INDEX: "<h1>Hacked by X</h1>"},
        )
        self.assertIn("-<h1>Welcome</h1>", findings[0].evidence)
        self.assertIn("+<h1>Hacked by X</h1>", findings[0].evidence)


if __name__ == "__main__":
    unittest.main(verbosity=2)
