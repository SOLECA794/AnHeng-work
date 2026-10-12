#!/usr/bin/env python
"""页面发现爬虫 CLI（T14）。

规格依据：SRS-02 F2、SDD-01 §5.4、SDD-02 §4.3 site_page。

用法示例：
    python scripts/crawl_site.py --url http://localhost:8080 --authorized \\
        --scope-type WHOLE --max-depth 2 --delay 1 --output reports/pages.json

合规（SRS-02、工具清单 §4）：
    - 仅可对已登记授权的资产执行，未显式声明 --authorized 一律拒绝（对应错误码 40310）；
    - 只读 GET、限速（--delay）、范围外 URL 在发现阶段即丢弃；
    - 落库由能力服务 site_page 仓储承接（T08 骨架就绪后接入），本脚本负责发现与产出。
"""

import argparse
import json
import pathlib
import sys
from dataclasses import asdict
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# Windows 控制台默认 GBK，中文输出会乱码；显式切 UTF-8
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from services.discovery import Scope, discover, is_in_scope, normalize_url  # noqa: E402


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="授权站点页面发现（非破坏，只读 GET）",
    )
    parser.add_argument("--url", required=True, help="巡检入口 URL（须在授权范围内）")
    parser.add_argument("--scope-type", default="WHOLE", choices=["WHOLE", "PATH", "SUBDOMAIN"])
    parser.add_argument("--scope-value", default=None, help="PATH 类型时的路径前缀，如 /dvwa")
    parser.add_argument("--max-depth", type=int, default=2, help="发现深度（首页为 0）")
    parser.add_argument("--max-pages", type=int, default=200, help="单次发现页面上限")
    parser.add_argument("--delay", type=float, default=1.0, help="请求间隔秒（限速）")
    parser.add_argument("--timeout", type=float, default=10.0, help="单请求超时秒")
    parser.add_argument("--output", default=None, help="结果 JSON 落盘路径（可选）")
    parser.add_argument(
        "--authorized",
        action="store_true",
        help="确认目标为已登记授权资产；未声明则拒绝执行",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if not args.authorized:
        print(
            "[拒绝] 未声明 --authorized：仅可对已登记授权资产执行（对应错误码 40310）。",
            file=sys.stderr,
        )
        return 2

    base = normalize_url(args.url)
    domain = urlparse(base).hostname or ""
    scope = Scope(domain=domain, scope_type=args.scope_type, scope_value=args.scope_value)

    if not is_in_scope(base, scope):
        print(f"[拒绝] 入口 URL 超出声明范围：{base}", file=sys.stderr)
        return 2

    print(f"发现目标：{base}（范围 {scope.scope_type}={scope.scope_value or domain}）")
    pages = discover(
        base,
        scope,
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        timeout=args.timeout,
        delay_seconds=args.delay,
    )

    for page in pages:
        print(f"  [{page.depth}] {page.normalized_url}  <{page.title or '-'}>  {page.content_hash[:12]}")

    print(f"共发现 {len(pages)} 个页面")

    if args.output:
        out = pathlib.Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps([asdict(p) for p in pages], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已写出：{out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
