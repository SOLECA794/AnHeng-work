"""C 组工具适配器真机验证（T15/T16/T17）—— 产出可评审的原始证据。

真机验证的证据必须**落在仓库里**，而不是只存在于对话或临时目录。本脚本对三个适配器
各跑一次**完整链路**（`ToolRegistry.execute` → 非破坏校验 → 执行 → 归一化 → 审计），
并把**原始工具输出**与**归一化结果**写入 `tests/fixtures/tool_output/`。

前置（Docker 可用）：
    # 授权靶场（已在本项目工具清单 §4.1 登记）
    docker run -d --name dvwa --restart unless-stopped -p 8080:80 vulnerables/web-dvwa

    # Nuclei 模板与缓存（命名卷；Windows 绑定挂载会极慢）
    docker volume create nuclei-tpl && docker volume create nuclei-cache
    #  模板首次导入：见 reports/C组真机测试.md「环境准备」

    # ZAP 守护进程
    docker run -d --name zap -p 8090:8090 ghcr.io/zaproxy/zaproxy:stable \\
      zap.sh -daemon -host 0.0.0.0 -port 8090 -config api.key=anheng-dev-key \\
      -config api.addrs.addr.name=.* -config api.addrs.addr.regex=true

运行：
    python scripts/verify_c_tools_real.py

产出（写入 tests/fixtures/tool_output/）：
    nuclei_dvwa_<日期>.jsonl         Nuclei 原始 JSONL 输出
    nuclei_dvwa_<日期>.stderr.log    Nuclei 的 INFO/WRN（诊断用）
    zap_dvwa_<日期>.http.jsonl       ZAP REST 的每次请求与响应（原始）
    wappalyzer_dvwa_<日期>.json      python-Wappalyzer 的原始识别结果
    results_<日期>.json              三个适配器的归一化结果与审计留痕（汇总）
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.tools.adapters.nuclei import NucleiAdapter  # noqa: E402
from src.tools.adapters.wappalyzer import WappalyzerAdapter  # noqa: E402
from src.tools.adapters.zap import ZapAdapter  # noqa: E402
from src.tools.container import ContainerSpec  # noqa: E402
from src.tools.contract import ToolRegistry  # noqa: E402
from src.tools.execution import make_subprocess_executor  # noqa: E402

TARGET = "http://127.0.0.1:8080/"
ZAP_BASE = "http://127.0.0.1:8090"
ZAP_KEY = "anheng-dev-key"
HOST_ALIAS = "host.docker.internal"
DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# 扫描范围与速率是**评估配置的一部分**，必须随结果记录（见 tests/dataset/README.md）。
NUCLEI_SCOPE = ["http/misconfiguration/", "http/exposures/", "http/technologies/"]
# 目标侧请求速率（验收标准 §4「目标负载受控」），与契约的工具调用速率无关。
# 30 次/秒：本地授权靶场、时长受限；合规速率（1 次/秒）跑不完此范围（见验证记录 §4）。
TARGET_RATE_PER_MINUTE = 1800

OUT_DIR = ROOT / "tests" / "fixtures" / "tool_output"


def _saving_executor(raw_path: pathlib.Path, stderr_log: pathlib.Path):
    """包一层执行器：命令输出照常返回，同时把原始 stdout 落盘。"""
    inner = make_subprocess_executor(stderr_log=stderr_log)

    def _run(command, timeout_seconds):
        raw = inner(command, timeout_seconds)
        raw_path.write_text(raw, encoding="utf-8")
        return raw

    return _run


def _saving_http(log_path: pathlib.Path):
    """包一层 ZAP REST 客户端：每次请求与响应都追加落盘。"""
    import httpx

    def _http(path, params):
        response = httpx.get(ZAP_BASE + path, params=params, timeout=60)
        record = {
            "request": {"path": path, "params": params},
            "response": response.json() if response.status_code == 200 else response.text,
        }
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record["response"]

    return _http


def _saving_analyzer(raw_path: pathlib.Path):
    """包一层指纹分析：原始识别结果落盘。"""
    from functools import lru_cache

    from Wappalyzer import WebPage, Wappalyzer

    @lru_cache(maxsize=1)
    def _rules():
        return Wappalyzer.latest()

    def _analyze(url):
        page = WebPage.new_from_url(url, verify=False, timeout=60)
        detected = _rules().analyze_with_versions_and_categories(page)
        raw_path.write_text(json.dumps(detected, ensure_ascii=False, indent=2), encoding="utf-8")
        return detected

    return _analyze


def _summarize(name: str, result, elapsed: float, extra: dict) -> dict:
    return {
        "tool": name,
        "status": result.status,
        "error": result.error,
        "elapsed_seconds": round(elapsed, 1),
        "finding_count": len(result.findings),
        "findings": result.findings,
        **extra,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []

    # ---- T15 Nuclei（容器模式）----
    raw = OUT_DIR / f"nuclei_dvwa_{DATE}.jsonl"
    stderr_log = OUT_DIR / f"nuclei_dvwa_{DATE}.stderr.log"
    stderr_log.unlink(missing_ok=True)
    nuclei = NucleiAdapter(
        container=ContainerSpec(
            image="projectdiscovery/nuclei:latest",
            mounts=("nuclei-tpl:/root/nuclei-templates", "nuclei-cache:/root/.cache/nuclei"),
        ),
        executor=_saving_executor(raw, stderr_log),
        stderr_log=stderr_log,
    )
    registry = ToolRegistry()
    registry.register(nuclei)
    print("[T15] Nuclei 扫描中（约 4 分钟）...", flush=True)
    started = time.time()
    result = registry.execute(
        "nuclei",
        TARGET,
        {
            "templates": NUCLEI_SCOPE,
            "target_rate_limit": TARGET_RATE_PER_MINUTE,
            "silent": False,
        },
    )
    summary.append(
        _summarize(
            "nuclei",
            result,
            time.time() - started,
            {
                "target": TARGET,
                "image": "projectdiscovery/nuclei:latest",
                "templates": NUCLEI_SCOPE,
                "target_rate_limit_per_minute": TARGET_RATE_PER_MINUTE,
                "raw_output": str(raw.relative_to(ROOT)),
                "stderr_log": str(stderr_log.relative_to(ROOT)),
                "audit": registry.audit(),
            },
        )
    )

    # ---- T16 ZAP（被动扫描）----
    zap_http = OUT_DIR / f"zap_dvwa_{DATE}.http.jsonl"
    zap_http.unlink(missing_ok=True)
    zap = ZapAdapter(http=_saving_http(zap_http), api_key=ZAP_KEY, target_alias=HOST_ALIAS)
    zap_registry = ToolRegistry()
    zap_registry.register(zap)
    print("[T16] ZAP 被动扫描中...", flush=True)
    started = time.time()
    result = zap_registry.execute("zap", TARGET, {})
    summary.append(
        _summarize(
            "zap",
            result,
            time.time() - started,
            {
                "target": TARGET,
                "zap_api": ZAP_BASE,
                "target_alias": HOST_ALIAS,
                "raw_output": str(zap_http.relative_to(ROOT)),
                "audit": zap_registry.audit(),
            },
        )
    )

    # ---- T17 Wappalyzer（进程内）----
    wapp_raw = OUT_DIR / f"wappalyzer_dvwa_{DATE}.json"
    wapp = WappalyzerAdapter(analyzer=_saving_analyzer(wapp_raw))
    wapp_registry = ToolRegistry()
    wapp_registry.register(wapp)
    print("[T17] Wappalyzer 识别中...", flush=True)
    started = time.time()
    result = wapp_registry.execute("wappalyzer", TARGET, {})
    summary.append(
        _summarize(
            "wappalyzer",
            result,
            time.time() - started,
            {
                "target": TARGET,
                "raw_output": str(wapp_raw.relative_to(ROOT)),
                "audit": wapp_registry.audit(),
            },
        )
    )

    summary_path = OUT_DIR / f"results_{DATE}.json"
    summary_path.write_text(
        json.dumps(
            {"generated_at": datetime.now(timezone.utc).isoformat(), "runs": summary},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\n原始证据与汇总已写入 {OUT_DIR.relative_to(ROOT)}/")
    for run in summary:
        print(f"  {run['tool']:12} status={run['status']:8} "
              f"{run['elapsed_seconds']:>6}s  命中 {run['finding_count']} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
