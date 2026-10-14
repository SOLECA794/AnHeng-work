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
- `timeout_seconds` 是单次 `run` 的硬等待上限。超时返回 `ToolResult.status=TIMEOUT` 并记录审计；取消底层任务为尽力而为，适配器应自行支持取消或子进程超时。
- 适配器抛出异常时返回 `FAILED`，异常文本只用于内部审计和调试，不得把凭据或未脱敏响应写入 `evidence`。
