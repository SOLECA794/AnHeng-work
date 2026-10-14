"""容器执行支撑。

依据：SDD-01 §2/§3「开源工具以容器或独立进程运行」、工具清单 §2.1、
验收标准 §4「最小权限：工具容器非 root」。

真机实测背景（2026-10-13，Docker Desktop for Windows，已实测确认）：
- 容器内访问宿主机上的授权靶场时 `http://127.0.0.1:8080/` 返回 `Connection refused`，
  必须改写为 `http://host.docker.internal:8080/`；
- 官方 Nuclei 镜像默认以 root 运行，与「工具容器非 root」要求冲突，
  故经 `ContainerSpec.options` 注入 `--user` 等选项。

注意：改写**只作用于发给工具的那一份目标地址**。授权边界校验（`non_destructive_check`）
始终针对原始目标，避免改写成为绕过授权校验的口子。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlparse, urlunparse

DEFAULT_HOST_ALIAS = "host.docker.internal"

# 指向宿主机的回环主机名：容器内不可达，需改写
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


@dataclass(frozen=True)
class ContainerSpec:
    """容器运行参数。"""

    image: str
    mounts: tuple[str, ...] = ()   # -v 参数，如 ("C:\\tpl:/root/nuclei-templates",)
    options: tuple[str, ...] = ()  # 其他 docker run 选项，如 ("--user", "1000:1000")
    remove: bool = True            # 是否加 --rm
    name: str | None = None        # --name；超时后据此终止容器（否则「已放弃」的扫描仍在打目标）
    # 把 host.docker.internal 显式映射到宿主网关。Docker Desktop 的内部 DNS 会**间歇性**
    # 解析失败（真机实测：同一命令时而成功、时而 "no address found for host"，导致扫描被
    # 整体跳过、0 命中）。显式映射让改写后的地址始终可解析。设为 None 可关闭。
    host_alias: str | None = DEFAULT_HOST_ALIAS


def container_reachable_url(url: str, alias: str = DEFAULT_HOST_ALIAS) -> str:
    """把指向宿主机的回环地址改写为容器可达的别名；其他地址原样返回。"""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
        return url
    netloc = f"{alias}:{parsed.port}" if parsed.port else alias
    return urlunparse(parsed._replace(netloc=netloc))


def docker_run_command(spec: ContainerSpec, args: Sequence[str]) -> list[str]:
    """构造 `docker run` 命令：`docker run [--rm] [--name n] [-v ...] [options] <image> <args...>`。"""
    command = ["docker", "run"]
    if spec.remove:
        command.append("--rm")
    if spec.name:
        command += ["--name", spec.name]
    if spec.host_alias:
        command += ["--add-host", f"{spec.host_alias}:host-gateway"]
    for mount in spec.mounts:
        command += ["-v", mount]
    command += list(spec.options)
    command.append(spec.image)
    command += [str(arg) for arg in args]
    return command
