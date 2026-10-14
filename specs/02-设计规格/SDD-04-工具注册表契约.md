# SDD-04 · T11 工具注册表契约

> 版本：v1.0.0（2026-10-12）
> 目的：作为 B → C 的稳定接口。C 的 Nuclei、ZAP、Wappalyzer 适配器只需实现本契约，不依赖 B 的内部实现。

## 1. 注册清单字段

每个工具必须提供 `ToolManifest`：

| 字段 | 类型 | 要求 |
|---|---|---|
| `id` | string | 小写唯一标识，如 `nuclei` |
| `name` / `description` | string | 展示名称和能力说明 |
| `version` | string | 适配器版本 |
| `categories` | string[] | `VULN`、`WEAK_CONFIG`、`FINGERPRINT`、`TAMPER`、`EXTERNAL_LINK`、`SENSITIVE` 之一或多项 |
| `execution_mode` | string | `local`、`http` 或 `container` |
| `read_only` | boolean | 必须为 `true`，否则禁止注册 |
| `enabled` | boolean | 是否允许执行 |
| `rate_limit` | object | `requests_per_minute`（≥1）、`burst`（≥1） |
| `timeout_seconds` | integer | 1–300 秒 |

## 2. 适配器接口

```python
class ToolAdapter(Protocol):
    manifest: ToolManifest
    def non_destructive_check(self, target, params) -> bool: ...
    def run(self, target, params) -> ToolResult: ...
```

`run` 返回 `ToolResult`：`tool_id`、`status`、`findings`、脱敏后的 `evidence`，以及可选 `error`。`status` 使用 `SUCCESS`、`REJECTED`、`FAILED`、`TIMEOUT`。

## 3. 执行前置校验

注册表是唯一事实源。执行前按顺序检查：已注册 → 已启用 → `read_only=true` → `non_destructive_check` 通过 → 限速/超时。任何失败均返回 `REJECTED` 或 `FAILED`，并写入审计记录；不得调用目标。

## 4. C 组接入约定

C 在 `src/tools/adapters/` 实现适配器，导出 `manifest`、`non_destructive_check`、`run`，并在启动装配时调用 `registry.register(adapter)`。适配器不得修改 `ToolManifest` 字段语义；新增字段须先更新本契约版本。

## 5. 限速与超时运行时语义

- `rate_limit.requests_per_minute` 使用每工具独立的令牌桶补充速率（每分钟允许的请求数）；`burst` 是初始及最大令牌数。
- 执行前消耗一个令牌；无令牌时不调用适配器，返回 `ToolResult.status=REJECTED`，错误为 `rate limit exceeded`，审计状态为 `RATE_LIMITED`。
- `timeout_seconds` 是单次 `run` 的调用方等待上限。超时返回 `ToolResult.status=TIMEOUT` 并记录审计；取消底层任务为尽力而为，适配器应自行支持取消或子进程超时。
- 适配器抛出异常时返回 `FAILED`，异常文本只用于内部审计和调试，不得把凭据或未脱敏响应写入 `evidence`。


## 6. 2026-10-14 运行时加固

- 每个注册表实例内，同一工具最多有一次实际执行；超时后后台执行未结束时，新调用返回 REJECTED（tool still running）。这避免重复创建挂起线程。
- Python 线程无法强制终止。TIMEOUT 只表示调用方等待超时；底层 HTTP、子进程或容器必须自行实现超时和资源清理。当前实现不是进程隔离，挂死线程也可能影响进程退出。
- 限速为每注册表实例的调用次数限速，不是扫描工具内部 HTTP 请求速率；多进程部署需要共享限流存储。
- 执行时重新检查 read_only；未注册、校验异常均拒绝并记录。工具异常不返回原始异常文本，避免携带凭证；返回对象必须为同一 tool_id 的 ToolResult。
- 注册在启动阶段完成；运行期间不支持并发注册或动态修改限速配置，需要重建注册表。
- 验收命令：python -m unittest discover -s tests/unit -p test_registry_controls.py -v
