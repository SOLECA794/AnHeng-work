"""T18/T19 对 F 组 T40 样例集 v1.0.0 的对齐验证。

数据来源：tests/dataset/v1.0.0.json（样例标签）+ tests/fixtures/sites/（靶标页面）
+ tests/dataset/baselines/c-index.html（C 靶标篡改基线）。

口径：`positive` 是该 URL **对指定 category** 的预期标签，不是整站安全性结论；
因此按 category 过滤检测结果后再判定（dataset/README.md 明确此口径）。

扫描配置（按 dataset/README「记录扫描配置」的要求随结果一并记录）：
    靶标 B 中的合成可疑域名黑名单——均为 .invalid 保留域，不可解析，仅作测试标记。

运行：python tests/integration/test_dataset_alignment.py -v
"""

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from services.discovery import Page, content_hash  # noqa: E402
from tools.external_link import scan_page  # noqa: E402
from tools.tamper import Baseline, detect_tamper  # noqa: E402

DATASET = ROOT / "tests/dataset/v1.0.0.json"
SITES = ROOT / "tests/fixtures/sites"
BASELINE_FILE = ROOT / "tests/dataset/baselines/c-index.html"

BLACKLIST = frozenset(
    {
        "collector.example.invalid",
        "c2.example.invalid",
        "ads.example.invalid",
        "spam.example.invalid",
    }
)
SITE_HOST = "127.0.0.1"
# C 组（T18/T19）负责的 category；VULN/WEAK_CONFIG/SENSITIVE 属 T15-T17 / T20
OWNED_CATEGORIES = frozenset({"MALICIOUS_LINK", "MALICIOUS_SCRIPT", "TAMPER"})


def load_cases() -> list[dict]:
    return json.loads(DATASET.read_text(encoding="utf-8"))["cases"]


def site_file(site: str, path: str) -> pathlib.Path:
    rel = "index.html" if path in ("", "/") else path.lstrip("/")
    return SITES / site.lower() / rel


def page_url(site: str, path: str) -> str:
    ports = {"A": 18081, "B": 18082, "C": 18083, "D": 18084, "E": 18085}
    return f"http://{SITE_HOST}:{ports[site]}{path}"


class DatasetAlignmentTest(unittest.TestCase):
    def setUp(self):
        self.cases = [c for c in load_cases() if c["category"] in OWNED_CATEGORIES]

    def test_owned_cases_cover_the_dataset(self):
        # 防止样例集更新后本测试静默少测：C 组应覆盖 7 条（B-01~B-04、C-01/C-02、E-02）
        self.assertEqual(len(self.cases), 7)
        self.assertEqual(
            {c["id"] for c in self.cases},
            {"B-01", "B-02", "B-03", "B-04", "C-01", "C-02", "E-02"},
        )

    def test_detectors_match_dataset_labels(self):
        for case in self.cases:
            with self.subTest(case=case["id"], category=case["category"]):
                detected = self._detect(case)
                self.assertEqual(
                    detected,
                    case["positive"],
                    f"{case['id']}（{case['category']}）预期 positive={case['positive']}，"
                    f"实际检出={detected}；证据口径：{case['evidence']}",
                )

    def _detect(self, case: dict) -> bool:
        html = site_file(case["site"], case["path"]).read_text(encoding="utf-8")
        url = page_url(case["site"], case["path"])

        if case["category"] == "TAMPER":
            baseline_text = BASELINE_FILE.read_text(encoding="utf-8")
            page = Page(
                url=url,
                normalized_url=url,
                title="",
                content_hash=content_hash(html),
                depth=0,
            )
            baselines = {url: Baseline(url=url, content_hash=content_hash(baseline_text))}
            return bool(detect_tamper([page], baselines))

        findings = scan_page(html, url, site_host=SITE_HOST, blacklist=BLACKLIST)
        return any(f.finding_type == case["category"] for f in findings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
