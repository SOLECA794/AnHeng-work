# 节点类型与 JSON 字段速查

> ⚠️ 本节是字段**语义速查**。平台导入还要求每个节点带**全套标准字段**、`model` 为对象、描述长度 8–1024。
> 生成后用 `scripts/validate_agent_json.py` 校验，完整规则见 [import-compatibility.md](import-compatibility.md)。

## type 映射

| 业务名 | `type` | 核心 JSON 字段 |
|--------|--------|----------------|
| 模型 | `llm_mixup` | `system_prompt_template`, `user_prompt_template`, `model`, `guided_json_expr` |
| 代码 | `act_interpreter` | `interpreter.code`, `interpreter.lang` |
| 插件 | `act_tool` | `tools[].id`, `tools[].options` |
| 智能体 | `act_agent` | 引用已发布智能体 |
| 交互 | `act_interaction` | `questions[]`, `interactions[]` — 见 [interaction-node.md](interaction-node.md) |
| 自规划 | `act_coding_agent` | `coding_agent_config`, `coding_agent_id` |

## 通用节点字段（必须全套，缺一可能导入失败）

每个节点至少包含：`id, name, description, type, input_parameters, output_parameters, result_key, tools, mcps, agents, timeout, max_retries, max_working, max_reflections, use_memory, log_memory, iteration, iteration_values, on_error, no_action, no_repeat`。

可导入模板见 [import-compatibility.md](import-compatibility.md) 规则 1。`act_tool` **不要**带 `model` / `user_prompt_template`；其余节点 `model` 必须是对象。

## `required` 参数边界

- 根（开始节点）`input_parameters` 可配置 `required`。
- `nodes[*].input_parameters`（中间节点）不应配置 `required`。
- 插件 `tools[].options[].required` 按插件定义使用。

## 参数 type / default_value

合法 `type`：`string` / `integer` / `float` / `boolean` / `array` / `object` / `file`。  
`default_value` 必须是对应 JSON 原生值（array 用 `["a"]` 而不是 `"[\"a\"]"`）。见 [param-type-default-keypoints.md](../examples/param-type-default-keypoints.md)。

```json
{
  "id": "node0",
  "name": "中文业务名",
  "description": "一句话说明本节点职责，至少八个字符",
  "type": "llm_mixup",
  "input_parameters": [],
  "output_parameters": [],
  "result_key": "result_key",
  "model": { "model": "HengNao-v4", "temperature": 0, "top_p": 1, "max_tokens": 1024, "top_k": -1, "reasoning": 1 },
  "tools": [],
  "mcps": [],
  "agents": [],
  "timeout": 300,
  "max_retries": 3,
  "max_working": 30,
  "max_reflections": 5,
  "use_memory": false,
  "log_memory": false,
  "iteration": false,
  "iteration_values": [],
  "on_error": 0,
  "no_action": false,
  "no_repeat": false
}
```

## value 赋值 type

平台 UI 称「输入 / 引用 / 渲染」；JSON 用 `value.type`。**每个入参/出参只要声明了，就必须带完整 `value`**（不能只有 name/type）。

| type | UI 说法 | 用途 |
|------|---------|------|
| `literal` | 输入 | 固定值，`expr` 为字符串 |
| `reference` | 引用 | `$.kv_user.*` / `$.kv_envs.*` / `$.nodeN.*` / `$.result_key` |
| `rendering` | 渲染 | Go 模板，用于 `output_expression`、子智能体 options 等 |
| `jsonpath` | （解析） | 从 `result_key` 提取具名字段，**必须**有 `text: { type: reference, expr: "$.result_key" }` |

详见 [output-parameters-value-keypoints.md](../examples/output-parameters-value-keypoints.md)。

## 引用路径

| 路径 | 含义 |
|------|------|
| `$.kv_user.X` | 用户输入 |
| `$.kv_envs.X` | 环境变量 |
| `$.nodeN.X` | 节点 N 的 output_parameters 名 |
| `$.nodeN.result_key` | 节点原始 JSON 输出 |
| `$.X` | 并行场景下最后节点的 X |

## 模型 guided 输出

配置在 `model` 内，均为 `{ "type": "literal", "expr": "..." }`：

| 字段 | 用途 |
|------|------|
| `guided_json_expr` | JSON Schema 字符串；object/array 根均可 |
| `guided_choice_expr` | JSON **数组**字符串（选项列表） |
| `guided_regex_expr` | 正则字符串 |

```json
"guided_json_expr": {
  "type": "literal",
  "expr": "{ \"type\": \"object\", \"required\": [\"field\"], \"properties\": { \"field\": { \"type\": \"string\" } } }"
}
```

**硬规则**：`llm_mixup` 若 `output_parameters` 用 `jsonpath` 解析 `result_key`，必须配置 guided（通常 `guided_json_expr`），且 Schema 字段与 jsonpath `expr` 一致。详见 [guided-output.md](guided-output.md)。

## 自规划节点

运行时为 **Codex** 沙箱（已替换原 OpenCode）。脚本 `type` 仍是 `act_coding_agent`，通过 Skill / MCP 自主规划执行。

```json
"coding_agent_config": {
  "name": "任务名",
  "model": "GLM-5.1",
  "prompt": "系统提示词（含 Skill 调用规则）",
  "skills": ["PLACEHOLDER_SKILL_<名称>"]
}
```

注意：`coding_agent_config.model` 是字符串；节点级 `model` 仍必须是对象。有 catalog 时 `skills` 填真实 `skill_id`。**不必预填 `coding_agent_id`**（平台保存脚本时追加）。详见 [coding-agent-keypoints.md](../examples/coding-agent-keypoints.md) 与 [platform-catalog.md](platform-catalog.md)。

## 边

```json
{
  "src_node": "node3",
  "dst_node": "node4",
  "parallel": true,
  "condition": "可选"
}
```

详见 [docs/04-edges.md](../docs/04-edges.md)。
