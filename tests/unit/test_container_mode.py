"""三个适配器的容器模式单测（T15/T16/T17 走 Docker 路线）。

要点：
- `manifest.execution_mode` 随运行方式切换为 `container`（SDD-04 §1 允许 local/http/container）；
- 命令经 `docker run` 包裹，镜像与挂载由调用方通过 `ContainerSpec` 指定；
- **目标改写只作用于发给工具的那一份**——`non_destructive_check` 仍校验原始目标，
  避免「授权校验被改写后的地址绕过」；
- ZAP 是 REST 型工具：它自己会去取目标页面，因此改写的对象是传给 ZAP 的 target。

运行：python tests/unit/test_container_mode.py -v
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.adapters.nuclei import NucleiAdapter  # noqa: E402
from src.tools.adapters.wappalyzer import WappalyzerAdapter  # noqa: E402
from src.tools.adapters.zap import ZapAdapter  # noqa: E402
from src.tools.container import DEFAULT_HOST_ALIAS, ContainerSpec  # noqa: E402

TARGET = "http://127.0.0.1:8080/"
REWRITTEN = f"http://{DEFAULT_HOST_ALIAS}:8080/"
NUCLEI_SPEC = ContainerSpec(
    image="projectdiscovery/nuclei:latest",
    mounts=("C:\\tpl:/root/nuclei-templates",),
)


def _executor_capturing(sink):
    def _run(command, timeout_seconds):
        sink.extend(command)
        return ""

    return _run


class NucleiContainerModeTest(unittest.TestCase):
    def test_manifest_switches_to_container_execution_mode(self):
        self.assertEqual(
            NucleiAdapter(container=NUCLEI_SPEC).manifest.execution_mode, "container"
        )

    def test_local_mode_stays_default(self):
        self.assertEqual(NucleiAdapter().manifest.execution_mode, "local")

    def test_command_is_wrapped_in_docker_run(self):
        command = NucleiAdapter(container=NUCLEI_SPEC).build_command(TARGET, {})
        self.assertEqual(command[:3], ["docker", "run", "--rm"])
        self.assertIn("projectdiscovery/nuclei:latest", command)
        self.assertIn("-jsonl", command)

    def test_target_is_rewritten_for_the_container(self):
        command = NucleiAdapter(container=NUCLEI_SPEC).build_command(TARGET, {})
        self.assertIn(REWRITTEN, command)
        self.assertNotIn(TARGET, command)

    def test_authorization_check_still_uses_the_original_target(self):
        # 改写不得成为绕过授权校验的口子
        adapter = NucleiAdapter(container=NUCLEI_SPEC)
        self.assertTrue(adapter.non_destructive_check(TARGET, {}))
        self.assertFalse(adapter.non_destructive_check("file:///etc/passwd", {}))
        self.assertFalse(adapter.non_destructive_check("http://192.168.0.0/24", {}))

    def test_run_executes_the_docker_command(self):
        captured: list[str] = []
        adapter = NucleiAdapter(
            container=NUCLEI_SPEC, executor=_executor_capturing(captured)
        )
        result = adapter.run(TARGET, {})
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(captured[:3], ["docker", "run", "--rm"])
        self.assertIn(REWRITTEN, captured)

    def test_rejected_target_never_reaches_docker(self):
        captured: list[str] = []
        adapter = NucleiAdapter(
            container=NUCLEI_SPEC, executor=_executor_capturing(captured)
        )
        self.assertEqual(adapter.run("file:///etc/passwd", {}).status, "REJECTED")
        self.assertEqual(captured, [])


class WappalyzerContainerModeTest(unittest.TestCase):
    def test_manifest_switches_to_container_execution_mode(self):
        adapter = WappalyzerAdapter(container=ContainerSpec(image="wappalyzer:latest"))
        self.assertEqual(adapter.manifest.execution_mode, "container")

    def test_command_is_wrapped_and_target_rewritten(self):
        adapter = WappalyzerAdapter(container=ContainerSpec(image="wappalyzer:latest"))
        command = adapter.build_command(TARGET, {})
        self.assertEqual(command[:3], ["docker", "run", "--rm"])
        self.assertIn(REWRITTEN, command)
        self.assertNotIn(TARGET, command)

    def test_local_mode_unchanged(self):
        self.assertEqual(WappalyzerAdapter().build_command(TARGET, [])[0], "wappalyzer")


class ZapContainerModeTest(unittest.TestCase):
    class _FakeHttp:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []

        def __call__(self, path, params=None):
            self.calls.append((path, dict(params or {})))
            return {"alerts": []}

    def test_target_sent_to_zap_is_rewritten(self):
        # ZAP 自己会去取目标页面，容器内的 ZAP 必须拿到容器可达的地址
        fake = self._FakeHttp()
        ZapAdapter(http=fake, target_alias=DEFAULT_HOST_ALIAS).run(TARGET, {})
        access = next(params for path, params in fake.calls if path.endswith("/accessUrl/"))
        alerts = next(params for path, params in fake.calls if path.endswith("/alerts/"))
        self.assertEqual(access["url"], REWRITTEN)
        self.assertEqual(alerts["baseurl"], REWRITTEN)

    def test_without_alias_target_is_untouched(self):
        fake = self._FakeHttp()
        ZapAdapter(http=fake).run(TARGET, {})
        access = next(params for path, params in fake.calls if path.endswith("/accessUrl/"))
        self.assertEqual(access["url"], TARGET)

    def test_authorization_check_uses_original_target(self):
        adapter = ZapAdapter(http=self._FakeHttp(), target_alias=DEFAULT_HOST_ALIAS)
        self.assertTrue(adapter.non_destructive_check(TARGET, {}))
        self.assertFalse(adapter.non_destructive_check(TARGET, {"active_scan": True}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
