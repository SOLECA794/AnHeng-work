# 关键片段：MCP / 插件 / 子智能体接线（三种节点形态）

> 来源：用户实测脚本「2312312313」（2026-08-25）  
> **硬规则**：工具节点选的插件/MCP、智能体节点选的子智能体，其 **入参名、type、required、description 均须从数据库/catalog 拉取**，再在脚本里为每个参数配 `value`（引用/输入/渲染）。禁止凭猜测编造参数名。

## 三种 MCP 挂法对照

| 节点 type | JSON 位置 | 形态 | 入参 options |
|-----------|-----------|------|--------------|
| `act_tool` | 节点 `mcps[]` | `{ "id": "<server_id>", "tools": [{ "name": "...", "options": [...] }] }` | **必须**按 DB 定义填全 `options[].value` |
| `llm_mixup` | 节点 `mcps[]` | 同上，但 `tools[]` 通常只列 `name` 供模型选用 | 一般**不配** options（模型自主调用） |
| `act_coding_agent` | `coding_agent_config.mcps[]` | **字符串数组**：`["<server_id>"]` | 无 options；Codex 运行时挂载 MCP |

节点 `mcps[].id` 与 catalog 的 `mcps[].server_id` **是同一 UUID**（脚本里字段名是 `id`，catalog 里是 `server_id`）。

---

## 1) 工具节点 · 只挂 MCP（`tools: []`，`mcps` 有值）

工具节点既可挂平台插件（`tools[].id` + `options`），也可**只挂 MCP** 并指定要调用的 MCP 工具及入参：

```json
{
  "id": "node0",
  "type": "act_tool",
  "tools": [],
  "mcps": [
    {
      "id": "740bcfda-6804-ae72-0900-1b82ba122b16",
      "tools": [
        {
          "name": "execute_sql",
          "options": [
            {
              "name": "query",
              "type": "string",
              "required": true,
              "description": "The SQL statement to execute. Single statements only.",
              "value": {
                "type": "reference",
                "expr": "$.kv_user.input"
              }
            }
          ]
        }
      ]
    }
  ]
}
```

要点：

- `mcps[].id` = catalog `server_id`
- `tools[].name` = 该 MCP 服务暴露的工具名（来自库表定义，勿猜）
- `options[]` 字段与 DB 一致；每个必填项必须有 `value`

---

## 2) 模型节点 · 挂载 MCP 供模型选用

模型节点挂 MCP 时，通常只声明**可用工具名**，由模型在推理中选用：

```json
{
  "id": "node1",
  "type": "llm_mixup",
  "mcps": [
    {
      "id": "740bcfda-6804-ae72-0900-1b82ba122b16",
      "tools": [
        { "name": "execute_sql" },
        { "name": "get_schema_info" },
        { "name": "get_table_sample" }
      ]
    }
  ]
}
```

要点：与工具节点不同，此处 **不必** 为每个 MCP 工具写 `options`（除非平台/UI 强制要求）。

---

## 3) 自规划节点 · `coding_agent_config.mcps`

自规划在配置块内用 **字符串数组** 挂 MCP，与节点级 `mcps[]` 不同：

```json
{
  "id": "node2",
  "type": "act_coding_agent",
  "coding_agent_config": {
    "name": "自规划任务名",
    "model": "GLM-5.3",
    "prompt": "……",
    "skills": [],
    "mcps": ["740bcfda-6804-ae72-0900-1b82ba122b16"],
    "env_parameters": [],
    "use_memory": false
  }
}
```

要点：

- `coding_agent_config.mcps[]` 元素是 **server_id 字符串**，不是 `{id:...}` 对象
- `coding_agent_id` **不必预填**（平台保存时追加）
- Skill 有 catalog 时 ID 必须来自 `indexes.server_ids`

---

## 插件节点（`tools[]`）与智能体节点（`agents[]`）

### 插件 `act_tool`

```json
"tools": [{
  "id": "<tool_id from catalog abilities>",
  "options": [
    {
      "name": "file",
      "type": "file",
      "required": true,
      "value": { "type": "reference", "expr": "$.kv_user.doc" }
    }
  ]
}]
```

`options[].name` / `type` / `required` 来自 `plugins[].abilities[].input_parameters`（`fetch_platform_catalog.py`）。

### 子智能体 `act_agent`

```json
"agents": [{
  "id": "<callable_agents[].agent_id>",
  "options": [
    {
      "name": "input",
      "type": "string",
      "value": { "type": "reference", "expr": "$.kv_user.input" }
    }
  ]
}]
```

`options` 按 `callable_agents[].input_parameter_hints`、`invocation_examples` 及关联资源表组装；见 [platform-catalog.md](../reference/platform-catalog.md)。

---

## 生成工作流（有 DB + catalog）

1. `fetch_platform_catalog.py` → 得到 `plugins` / `mcps`（含动态 **`tool_names`**、`package_name`）/ `callable_agents`
2. 按用户描述选 MCP `server_id` 与工具名（撞名规则见 platform-catalog）
3. **工具节点 MCP**：有 `tool_names` 则填 `tools[].name`；**入参 options** 以 catalog/平台动态能力为准（Skill **不维护** npm→schema 硬编码表）
4. **插件**：从 `abilities_by_tool_id[tool_id].input_parameters` 复制
5. 无 catalog 时：`mcps[].id` / `tools[].id` / `agents[].id` 用 `PLACEHOLDER_*`，设计说明列出待填项

库表与动态拉取说明见 [reference/mcp-db-schema.md](../reference/mcp-db-schema.md)。

## 自检

- [ ] MCP 的 `id` / 自规划 `mcps[]` 字符串均来自 catalog，未编造 UUID
- [ ] 工具节点 MCP/插件的 `options` 名与 DB 一致，且必填项有 `value`
- [ ] 模型节点 MCP 只列需要的 `tools[].name`
- [ ] 自规划用 `coding_agent_config.mcps` 字符串数组，勿与节点 `mcps[]` 对象形态混用
- [ ] 子智能体 `agents[].options` 与 catalog 提示一致
