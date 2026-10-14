"""工具执行器单测（诊断能力）。

背景（真机调试中暴露）：适配器原先只捕获 stdout、把 stderr 直接丢弃。配合硬编码的
`-silent`，导致**扫描失败时完全不可诊断**——实测中「218 个模板 0 命中」的真因
就因此查不出来。更糟的是，我三次从「空的 stderr」错误推断成「没有发生异常」。

本模块让 stderr 落到日志文件：既保留诊断信息，又不污染 stdout（stdout 仍是纯 JSONL）。

运行：python tests/unit/test_tool_executor.py -v
"""

import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.execution import make_subprocess_executor  # noqa: E402

# 真实子进程：分别向 stdout / stderr 写内容
PRINTER = [sys.executable, "-c", "import sys; print('OUT-LINE'); print('ERR-LINE', file=sys.stderr)"]

# 分两段写 stderr，中间停顿——用于验证「进程未结束时日志已可见」
STREAMING_PRINTER = [
    sys.executable,
    "-u",
    "-c",
    "import sys, time\n"
    "print('FIRST-CHUNK', file=sys.stderr, flush=True)\n"
    "time.sleep(4)\n"
    "print('SECOND-CHUNK', file=sys.stderr, flush=True)\n",
]

# 写完一行后长时间挂起——用于验证超时后仍保留已产出的 stderr
HANGING_PRINTER = [
    sys.executable,
    "-u",
    "-c",
    "import sys, time\n"
    "print('BEFORE-HANG', file=sys.stderr, flush=True)\n"
    "time.sleep(30)\n",
]


class SubprocessExecutorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_stdout(self):
        executor = make_subprocess_executor()
        self.assertIn("OUT-LINE", executor(PRINTER, 30))

    def test_writes_stderr_to_log_file(self):
        log = self.tmpdir / "nuclei.log"
        executor = make_subprocess_executor(stderr_log=log)
        executor(PRINTER, 30)
        self.assertTrue(log.exists(), "配置了日志路径就应写出 stderr")
        self.assertIn("ERR-LINE", log.read_text(encoding="utf-8"))

    def test_log_records_the_command_for_traceability(self):
        log = self.tmpdir / "nuclei.log"
        make_subprocess_executor(stderr_log=log)(PRINTER, 30)
        content = log.read_text(encoding="utf-8")
        self.assertIn("-c", content)  # 命令行被记录，便于回溯是哪个工具、什么参数

    def test_appends_across_runs(self):
        log = self.tmpdir / "nuclei.log"
        executor = make_subprocess_executor(stderr_log=log)
        executor(PRINTER, 30)
        executor(PRINTER, 30)
        # 按整行匹配：命令行头里也含源码文本，不能直接 count 子串
        lines = [
            line
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip() == "ERR-LINE"
        ]
        self.assertEqual(len(lines), 2)

    def test_creates_parent_directories(self):
        log = self.tmpdir / "nested" / "deeper" / "nuclei.log"
        make_subprocess_executor(stderr_log=log)(PRINTER, 30)
        self.assertTrue(log.exists())

    def test_writes_nothing_when_no_log_configured(self):
        executor = make_subprocess_executor()
        executor(PRINTER, 30)
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_still_returns_stdout_when_command_fails(self):
        failing = [sys.executable, "-c", "import sys; print('PARTIAL'); sys.exit(3)"]
        self.assertIn("PARTIAL", make_subprocess_executor()(failing, 30))

    # ---- 实时性：stderr 必须边跑边落盘，而不是等进程结束 ----

    def test_stderr_is_visible_while_the_command_is_still_running(self):
        log = self.tmpdir / "nuclei.log"
        executor = make_subprocess_executor(stderr_log=log)
        worker = threading.Thread(
            target=executor, args=(STREAMING_PRINTER, 30), daemon=True
        )
        worker.start()
        try:
            deadline = time.time() + 15
            while time.time() < deadline:
                if log.exists() and "FIRST-CHUNK" in log.read_text(encoding="utf-8"):
                    break
                time.sleep(0.1)
            content = log.read_text(encoding="utf-8") if log.exists() else ""
            # 按整行精确匹配：日志头记录的命令行里也含这些字面量（-c 源码），子串匹配会误判
            lines = [line.strip() for line in content.splitlines()]
            self.assertIn(
                "FIRST-CHUNK", lines, "进程未结束时日志就应已可见（用于 tail -f 观察长任务）"
            )
            self.assertIn("$ ", content, "命令行头也应在开跑前写入，便于确认在跑哪个命令")
            self.assertNotIn(
                "SECOND-CHUNK", lines, "进程仍在运行时不应已有后半段输出（说明是流式而非缓冲）"
            )
        finally:
            worker.join(timeout=20)

    def test_stderr_is_preserved_when_the_command_times_out(self):
        """超时是最需要诊断的场景：老实现在超时时连日志文件都不会创建，导致零证据。"""
        log = self.tmpdir / "nuclei.log"
        with self.assertRaises(subprocess.TimeoutExpired):
            make_subprocess_executor(stderr_log=log)(HANGING_PRINTER, 2)
        self.assertTrue(log.exists(), "超时也必须留下日志文件")
        lines = [
            line.strip()
            for line in log.read_text(encoding="utf-8").splitlines()
        ]
        self.assertIn("BEFORE-HANG", lines, "超时前已产出的 stderr 必须保留")


if __name__ == "__main__":
    unittest.main(verbosity=2)
