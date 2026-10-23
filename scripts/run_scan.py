"""临时巡检入口（替代恒脑编排）。

用法：
    python scripts/run_scan.py http://127.0.0.1:8080

依次调用可用工具，输出 Markdown 报告。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.tools.contract import ToolRegistry
from src.tools.container import ContainerSpec
from src.tools.adapters.nuclei import NucleiAdapter
from src.tools.adapters.wappalyzer import WappalyzerAdapter

import os
TEMPLATE_DIR = str(ROOT / "nuclei-templates")
NUCLEI_CONTAINER = ContainerSpec(
    image="projectdiscovery/nuclei:latest",
    mounts=(f"{TEMPLATE_DIR}:/root/nuclei-templates:ro",),
)


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    adapters = [
        WappalyzerAdapter(),
        NucleiAdapter(container=NUCLEI_CONTAINER),
    ]
    for adapter in adapters:
        try:
            if adapter.manifest.id == "nuclei":
                adapter.manifest.timeout_seconds = 900
            reg.register(adapter)
        except Exception as e:
            print(f"[warn] {adapter.manifest.id} 注册失败: {e}", file=sys.stderr)
    return reg


def run_scan(target: str) -> str:
    reg = build_registry()
    results = {}
    all_findings = []

    for tool_id in ["wappalyzer", "nuclei"]:
        if tool_id not in [m.id for m in reg.manifests()]:
            continue
        t0 = time.time()
        print(f"[*] 运行 {tool_id} -> {target} ...", file=sys.stderr)
        try:
            params = {}
            if tool_id == "nuclei":
                params = {
                    "templates": ["http/misconfiguration/", "http/exposures/", "http/technologies/"],
                    "target_rate_limit": 1800,
                }
            res = reg.execute(tool_id, target, params)
            elapsed = time.time() - t0
            results[tool_id] = {
                "status": res.status,
                "elapsed_s": round(elapsed, 1),
                "findings_count": len(res.findings),
                "error": res.error,
            }
            all_findings.extend(res.findings)
        except Exception as e:
            results[tool_id] = {"status": "EXCEPTION", "error": str(e)}

    # 按严重程度统计
    level_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "unknown": 5}
    all_findings.sort(key=lambda f: level_order.get(str(f.get("severity", "unknown")).lower(), 9))

    counts = {}
    for f in all_findings:
        lv = str(f.get("severity", "info")).lower()
        counts[lv] = counts.get(lv, 0) + 1

    # 生成 Markdown
    lines = [
        f"# 安全巡检报告",
        f"",
        f"- **目标**: `{target}`",
        f"- **扫描时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **工具数**: {len(results)}",
        f"- **发现总数**: {len(all_findings)}",
        f"",
        f"## 工具执行结果",
        f"",
        f"| 工具 | 状态 | 耗时(s) | 发现数 | 错误 |",
        f"|------|------|---------|--------|------|",
    ]
    for tid, r in results.items():
        lines.append(f"| {tid} | {r['status']} | {r.get('elapsed_s','-')} | {r.get('findings_count',0)} | {r.get('error') or ''} |")

    lines += [
        f"",
        f"## 风险分级统计",
        f"",
    ]
    for lv in ["critical", "high", "medium", "low", "info"]:
        if lv in counts:
            lines.append(f"- **{lv}**: {counts[lv]}")

    lines += [f"", f"## 风险详情", f""]
    for i, f in enumerate(all_findings, 1):
        title = f.get("title") or f.get("name") or f.get("template-id") or "未命名"
        sev = f.get("severity", "info")
        url = f.get("matched-at") or f.get("url") or target
        desc = f.get("description") or f.get("info") or ""
        lines.append(f"### {i}. [{sev.upper()}] {title}")
        lines.append(f"- 位置: `{url}`")
        if desc:
            lines.append(f"- 描述: {desc[:200]}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
    report = run_scan(target)
    print(report)
    out = ROOT / "reports" / "scan-output.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"\n[*] 报告已保存: {out}", file=sys.stderr)
