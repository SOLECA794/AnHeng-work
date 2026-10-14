"""工具执行器。

**为什么要单独有它**：适配器原先只捕获 stdout、把 stderr 直接丢弃，配合硬编码的 `-silent`，
导致扫描异常**完全不可诊断**——实测中「218 个模板 0 命中」查了很久才发现是
「契约 300 秒超时 → `TimeoutExpired` 抛出 → 写日志的代码根本没执行到 → 零证据」。

本模块让 stderr **边跑边落盘**（而不是等进程结束才写）：

- stdout 仍是纯工具输出（供解析）；
- stderr 由子进程直接写入日志文件句柄，因此可以用 `tail -f` 实时观察长任务；
- **超时/被杀时，已产出的 stderr 依然留在日志里**——这正是最需要诊断的场景。

实测背景：合规限速下（1 次/2 秒）218 个模板需约 740 秒，远超契约 300 秒上限，
因此「超时也保留诊断」不是锦上添花，而是排查长扫描问题的前提。
"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Callable, Sequence


def make_subprocess_executor(
    stderr_log: pathlib.Path | None = None,
) -> Callable[[Sequence[str], int], str]:
    """构造子进程执行器：返回 stdout，并把 stderr 实时追加写入日志文件。

    :param stderr_log: stderr 落盘路径；为 None 时仍捕获 stderr 但不落盘。
                       日志按调用追加，并先写入命令行，便于回溯是哪个工具、什么参数。
    """

    def _execute(command: Sequence[str], timeout_seconds: int) -> str:
        argv = [str(part) for part in command]

        if stderr_log is None:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            return completed.stdout

        stderr_log.parent.mkdir(parents=True, exist_ok=True)
        with stderr_log.open("a", encoding="utf-8") as handle:
            # 命令行先落盘并 flush：tail -f 时能立刻确认正在跑哪个命令
            handle.write(f"$ {' '.join(argv)}\n")
            handle.flush()
            # stderr 交给文件句柄，由子进程直接写入 → 实时可见，且超时也保留
            completed = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=handle,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        return completed.stdout

    return _execute
