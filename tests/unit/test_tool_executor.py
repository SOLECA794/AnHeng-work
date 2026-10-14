"""工具执行器单测（诊断能力）。

背景（真机调试中暴露）：适配器原先只捕获 stdout、把 stderr 直接丢弃。配合硬编码的
`-silent`，导致**扫描失败时完全不可诊断**——实测中「218 个模板 0 命中」的真因
就因此查不出来。更糟的是，我三次从「空的 stderr」错误推断成「没有发生异常」。

本模块让 stderr 落到日志文件：既保留诊断信息，又不污染 stdout（stdout 仍是纯 JSONL）。

运行：python tests/unit/test_tool_executor.py -v
"""

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.execution import make_subprocess_executor  # noqa: E402

# 真实子进程：分别向 stdout / stderr 写内容
PRINTER = [sys.executable, "-c", "import sys; print('OUT-LINE'); print('ERR-LINE', file=sys.stderr)"]


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
