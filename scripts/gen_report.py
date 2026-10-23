import sys, json, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.tools.adapters.nuclei import NucleiAdapter
from src.tools.adapters.wappalyzer import WappalyzerAdapter

raw = (ROOT / "reports" / "nuclei-raw.jsonl").read_text(encoding="utf-8")
findings = NucleiAdapter().parse_jsonl(raw)

# Wappalyzer
wp = WappalyzerAdapter().run("http://127.0.0.1:8080", {})
tech = [t.get("name", "") for t in wp.findings] if wp.findings else []

# 统计
counts = {}
for f in findings:
    lv = f.get("severity", "info")
    counts[lv] = counts.get(lv, 0) + 1

lines = [
    "# 安全巡检报告",
    "",
    f"- **目标**: http://127.0.0.1:8080 (DVWA)",
    f"- **扫描时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
    f"- **技术栈识别**: {', '.join(tech) if tech else '未识别'}",
    f"- **Nuclei 命中**: {len(findings)} 条",
    "",
    "## 风险分级统计",
    "",
]
for lv in ["critical", "high", "medium", "low", "info", "unknown"]:
    if lv in counts:
        lines.append(f"- **{lv}**: {counts[lv]}")

lines += ["", "## 风险详情", ""]
for i, f in enumerate(findings, 1):
    title = f.get("title", "未命名")
    sev = f.get("severity", "info")
    url = f.get("matched_at", f.get("url", ""))
    desc = f.get("description", "")
    lines.append(f"### {i}. [{sev.upper()}] {title}")
    lines.append(f"- 位置: `{url}`")
    if desc:
        lines.append(f"- 描述: {desc[:150]}")
    lines.append("")

report = "\n".join(lines)
(ROOT / "reports" / "scan-final.md").write_text(report, encoding="utf-8")
print(report)
print(f"\n[*] 报告已保存: reports/scan-final.md")
