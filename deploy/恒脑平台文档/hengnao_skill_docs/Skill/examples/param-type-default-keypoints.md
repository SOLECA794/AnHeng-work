# 关键片段：参数 `type` 与 `default_value` 必须原生对齐

> 实测事故（2026-08-24，gc.das-ai.com）：  
> `config error: the value for the parameter must be of array type: items`  
> 根因：开始节点 `items` 声明 `type: "array"`，但 `default_value` 写成了字符串 `"[\"alpha\",\"beta\",\"gamma\"]"`。

## 硬规则

1. `type` 只能用平台类型：`string` / `integer` / `float` / `boolean` / `array` / `object` / `file`
2. 有 `default_value` 时，**JSON 值本身**必须匹配 `type`，禁止用字符串去“描述”该类型
3. 数组/对象尤其不能 JSON.stringify 后再塞进 `default_value`
4. 同样约束：根 `input_parameters`、`private_envs` / `public_envs`，以及任何带 `default_value` 的参数

## 错误 → 正确（array）

```json
{ "name": "items", "type": "array", "required": true,
  "default_value": "[\"alpha\",\"beta\",\"gamma\"]" }
```

```json
{ "name": "items", "type": "array", "required": true,
  "default_value": ["alpha", "beta", "gamma"] }
```

## 各类型 default_value

| `type` | `default_value` 必须是 | 禁止 |
|--------|------------------------|------|
| `string` | JSON 字符串 `"hello"` | 数字 / 布尔 |
| `integer` | JSON 整数 `3` | `"3"` / `3.5` / `true` |
| `float` | JSON 数字 `3.14` 或 `3` | `"3.14"` |
| `boolean` | `true` / `false` | `"true"` / `1` / `0` |
| `array` | JSON 数组 `[...]` | `"[...]"` 字符串 |
| `object` | JSON 对象 `{...}` | `"{...}"` 字符串 |
| `file` | 字符串 `file:名称.ext<uuid>` | 数组 / 对象 |

## 其它常见错

```json
{ "name": "top_k", "type": "integer", "default_value": "10" }
{ "name": "enabled", "type": "boolean", "default_value": "true" }
{ "name": "meta", "type": "object", "default_value": "{\"k\":1}" }
```

应改为：`10`、`true`、`{"k":1}`。

节点 `input_parameters` 的 `value.expr`（reference/rendering）仍是字符串表达式，不受本表约束；本规则针对 **`default_value` 的 JSON 字面量**。

## 生成自检

- [ ] 每个参数 `type` 属于上表
- [ ] 有 `default_value` 则与 `type` 原生匹配
- [ ] `array`/`object` 绝不用带引号的 JSON 文本
- [ ] `file` 默认值形如 `file:xxx<uuid>`
- [ ] 跑过 `scripts/validate_agent_json.py`
