"""工具执行器。

**为什么要单独有它**：适配器原先只捕获 stdout、把 stderr 直接丢弃，配合硬编码的 `-silent`，
导致扫描异常**完全不可诊断**——实测中「218 个模板 0 命中」的真因就是因此查不出来，
还让我三次从「空的 stderr」错误推断成「没有发生异常」。

本模块把 stderr 落到日志文件：stdout 仍是纯工具输出（供解析），stderr 供事后诊断。
"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Callable, Sequence


def make_subprocess_executor(
    stderr_log: pathlib.Path | None = None,
) -> Callable[[Sequence[str], int], str]:
    """构造子进程执行器：返回 stdout，并按需把 stderr 追加写入日志文件。

    :param stderr_log: stderr 落盘路径；为 None 时仍捕获 stderr 但不落盘。
                       日志按调用追加，并记录命令行，便于回溯是哪个工具、什么参数。
    """

    def _execute(command: Sequence[str], timeout_seconds: int) -> str:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        if stderr_log is not None and completed.stderr:
            stderr_log.parent.mkdir(parents=True, exist_ok=True)
            with stderr_log.open("a", encoding="utf-8") as handle:
                handle.write(f"$ {' '.join(str(part) for part in command)}\n")
                handle.write(completed.stderr)
                if not completed.stderr.endswith("\n"):
                    handle.write("\n")
        return completed.stdout

    return _execute
