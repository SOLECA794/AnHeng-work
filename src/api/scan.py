"""巡检接口（T27 缓兵之计：本地脚本替代恒脑编排）。

POST /api/scan  触发一次巡检（Wappalyzer + Nuclei）
GET  /api/scan/{task_id}  查询巡检结果
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from threading import Thread
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.core.response import ApiResponse, success
from src.tools.adapters.wappalyzer import WappalyzerAdapter

router = APIRouter(prefix="/scan", tags=["scan"])

# 内存任务表（演示用，生产应落库）
_tasks: dict[str, dict[str, Any]] = {}


class ScanRequest(BaseModel):
    target_url: str
    templates: list[str] = ["http/misconfiguration/", "http/exposures/"]
    run_nuclei: bool = True


def _run_nuclei_task(task_id: str, target: str, templates: list[str]) -> None:
    """后台跑 Nuclei（手动 docker 命令，绕过 Windows 挂载慢的问题）。"""
    import subprocess

    _tasks[task_id]["status"] = "running"
    try:
        project_root = Path(__file__).resolve().parent.parent.parent
        tpl_dir = project_root / "nuclei-templates"
        raw_out = project_root / "reports" / f"nuclei-{task_id}.jsonl"

        # 容器内 127.0.0.1 指向容器自己，改写为 host.docker.internal
        container_target = target.replace("127.0.0.1", "host.docker.internal").replace("localhost", "host.docker.internal")

        cmd = [
            "docker", "run", "--rm",
            "-v", "nuclei-tpl:/root/nuclei-templates:ro",
            "--add-host", "host.docker.internal:host-gateway",
            "projectdiscovery/nuclei:latest",
            "-u", container_target,
            "-silent", "-nc", "-jsonl", "-no-interactsh",
            "-etags", "fuzz,intrusive,dos",
            "-rate-limit", "30",
            "-timeout", "10",
        ]
        for t in templates:
            cmd += ["-t", t]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        raw_out.write_text(result.stdout, encoding="utf-8")

        # 用适配器解析
        from src.tools.adapters.nuclei import NucleiAdapter
        findings = NucleiAdapter().parse_jsonl(result.stdout)

        _tasks[task_id]["status"] = "done"
        _tasks[task_id]["findings"] = findings
        _tasks[task_id]["raw_output"] = str(raw_out)
        if result.stderr:
            _tasks[task_id]["nuclei_stderr"] = result.stderr[-500:]  # 最后500字符
    except subprocess.TimeoutExpired:
        _tasks[task_id]["status"] = "timeout"
        _tasks[task_id]["error"] = "Nuclei 扫描超时（600s）"
    except FileNotFoundError:
        _tasks[task_id]["status"] = "failed"
        _tasks[task_id]["error"] = "docker 命令未找到，请确认 Docker Desktop 已启动"
    except Exception as e:
        _tasks[task_id]["status"] = "failed"
        _tasks[task_id]["error"] = str(e)


@router.post("", response_model=ApiResponse)
def start_scan(req: ScanRequest) -> ApiResponse:
    task_id = uuid.uuid4().hex[:12]
    _tasks[task_id] = {
        "task_id": task_id,
        "target": req.target_url,
        "status": "pending",
        "started_at": time.time(),
    }

    # Wappalyzer 同步跑（快）
    try:
        wp = WappalyzerAdapter().run(req.target_url, {})
        _tasks[task_id]["tech"] = [t.get("name", "") for t in wp.findings] if wp.findings else []
    except Exception as e:
        _tasks[task_id]["tech_error"] = str(e)

    # Nuclei 异步跑
    if req.run_nuclei:
        t = Thread(target=_run_nuclei_task, args=(task_id, req.target_url, req.templates), daemon=True)
        t.start()
    else:
        _tasks[task_id]["status"] = "done"

    return success({"task_id": task_id, "status": _tasks[task_id]["status"]})


@router.get("/{task_id}", response_model=ApiResponse)
def get_scan_result(task_id: str) -> ApiResponse:
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="task not found")
    return success(_tasks[task_id])
