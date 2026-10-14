"""容器执行支撑单测（T15/T16/T17 走 Docker 路线的基础）。

规格依据：SDD-01 §2/§3「开源工具以容器或独立进程运行」、工具清单 §2.1、
验收标准 §4「最小权限：工具容器非 root」。

背景（真机实测得出）：
- 容器内访问宿主机上的授权靶场，`127.0.0.1:8080` 会 Connection refused，
  必须改写为 `host.docker.internal`（Docker Desktop for Windows）；
- 官方 Nuclei 镜像默认以 root 运行，与「工具容器非 root」要求冲突，
  故容器命令需支持注入 `--user` 等选项。

运行：python tests/unit/test_container.py -v
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.container import (  # noqa: E402
    DEFAULT_HOST_ALIAS,
    ContainerSpec,
    container_reachable_url,
    docker_run_command,
)


class ContainerReachableUrlTest(unittest.TestCase):
    def test_rewrites_loopback_with_port(self):
        self.assertEqual(
            container_reachable_url("http://127.0.0.1:8080/login.php"),
            f"http://{DEFAULT_HOST_ALIAS}:8080/login.php",
        )

    def test_rewrites_localhost(self):
        self.assertEqual(
            container_reachable_url("http://localhost/dvwa/"),
            f"http://{DEFAULT_HOST_ALIAS}/dvwa/",
        )

    def test_preserves_scheme_path_and_query(self):
        self.assertEqual(
            container_reachable_url("https://127.0.0.1:8443/a/b?c=1&d=2"),
            f"https://{DEFAULT_HOST_ALIAS}:8443/a/b?c=1&d=2",
        )

    def test_leaves_public_hosts_untouched(self):
        for url in ("http://example.com/", "https://a.example.com:443/x"):
            with self.subTest(url=url):
                self.assertEqual(container_reachable_url(url), url)

    def test_supports_custom_alias(self):
        self.assertEqual(
            container_reachable_url("http://127.0.0.1:8080/", alias="gateway.internal"),
            "http://gateway.internal:8080/",
        )


class DockerRunCommandTest(unittest.TestCase):
    def test_builds_minimal_command(self):
        command = docker_run_command(ContainerSpec(image="projectdiscovery/nuclei"), ["-u", "x"])
        self.assertEqual(
            command,
            [
                "docker",
                "run",
                "--rm",
                "--add-host",
                f"{DEFAULT_HOST_ALIAS}:host-gateway",
                "projectdiscovery/nuclei",
                "-u",
                "x",
            ],
        )

    def test_includes_mounts_before_image(self):
        spec = ContainerSpec(
            image="projectdiscovery/nuclei",
            mounts=("C:\\tpl:/root/nuclei-templates",),
        )
        command = docker_run_command(spec, ["-version"])
        self.assertIn("-v", command)
        self.assertLess(command.index("-v"), command.index(spec.image))
        self.assertIn("C:\\tpl:/root/nuclei-templates", command)

    def test_includes_extra_options_for_least_privilege(self):
        # 最小权限：以非 root 运行需注入 --user 等选项
        spec = ContainerSpec(
            image="projectdiscovery/nuclei", options=("--user", "1000:1000")
        )
        command = docker_run_command(spec, ["-version"])
        self.assertLess(command.index("--user"), command.index(spec.image))
        self.assertIn("1000:1000", command)

    def test_can_disable_auto_remove(self):
        command = docker_run_command(ContainerSpec(image="x", remove=False), [])
        self.assertNotIn("--rm", command)

    def test_includes_container_name_when_given(self):
        # 命名是「超时后能把它杀掉」的前提
        spec = ContainerSpec(image="x", name="anheng-nuclei-abc123")
        command = docker_run_command(spec, [])
        self.assertIn("--name", command)
        self.assertEqual(command[command.index("--name") + 1], "anheng-nuclei-abc123")
        self.assertLess(command.index("--name"), command.index(spec.image))

    def test_omits_name_when_not_given(self):
        self.assertNotIn("--name", docker_run_command(ContainerSpec(image="x"), []))

    def test_maps_host_gateway_so_the_rewritten_alias_resolves(self):
        """真机实测：Docker Desktop 的内部 DNS 会**间歇性**解析不了 `host.docker.internal`
        （同一命令时而成功、时而 "no address found for host"），届时整个扫描会被跳过、0 命中。
        显式 `--add-host` 映射宿主网关，让改写后的地址始终可解析。"""
        command = docker_run_command(ContainerSpec(image="x"), [])
        self.assertIn("--add-host", command)
        mapping = command[command.index("--add-host") + 1]
        self.assertEqual(mapping, f"{DEFAULT_HOST_ALIAS}:host-gateway")
        self.assertLess(command.index("--add-host"), command.index("x"))

    def test_can_disable_host_gateway_mapping(self):
        command = docker_run_command(ContainerSpec(image="x", host_alias=None), [])
        self.assertNotIn("--add-host", command)

    def test_appends_tool_args_after_image(self):
        spec = ContainerSpec(image="projectdiscovery/nuclei")
        command = docker_run_command(spec, ["-u", "http://host.docker.internal:8080/"])
        self.assertGreater(command.index("-u"), command.index(spec.image))


if __name__ == "__main__":
    unittest.main(verbosity=2)
