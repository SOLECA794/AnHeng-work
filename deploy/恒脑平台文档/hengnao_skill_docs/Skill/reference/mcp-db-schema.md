# MCP 在瀚高库中的表结构与工具清单来源

> 实测环境：GC 瀚高 `<host>:<port>` / 库 `<dbname>`（2026-08-25）  
> 只读账号拉 catalog；**不要把密码写入 Skill 或 git**。

## 结论（Q-M02 / Q-M03）

**没有**类似 `m_security_plugin_ability.input_parameters` 的 **MCP 工具入参表**。

按 **`server_id`（节点里的 `mcps[].id`）** 从库能拿到什么：

| 能拿到 | 来源 | 限制 |
|--------|------|------|
| MCP 连接信息 | `m_mcp_server*.config` | 无 tools |
| 模板描述 / 环境变量 | `m_mcp_server_template.mcp_info` | 仅部分模板有 |
| **工具名清单** | `mcp_info.tools`（字符串数组） | **仅少数模板有**；**无** `input_parameters` |
| 完整入参 schema（如 `execute_sql.query`） | ❌ 库中没有 | 靠平台 UI / 运行时 `list_tools` 等**动态**能力 |

**Skill 策略（Q-M03）**：

- **只写方法**：如何用 catalog 的 `server_id` / `tool_names` 接线（见 [mcp-wiring-keypoints.md](../examples/mcp-wiring-keypoints.md)）
- **不维护** npm 包 → tools schema 硬编码表（不在 Skill 里合入 `MCP_KNOWN_TOOLS_BY_PACKAGE`）
- 完整入参后续靠 **动态拉取**，不靠 Skill 内静态映射

插件入参仍在 **`m_security_plugin_ability.input_parameters`**（与 MCP 无关）。

智能体脚本 `m_agent_application_local.config` 为 **加密**（`config_deal_flag=1`），无法批量反解析已配置的 MCP options。

## 相关表

| 表 | 用途 | 与工具 schema 关系 |
|----|------|-------------------|
| `m_mcp_server_template` | 系统 MCP 模板 | `config` 连接信息；`mcp_info` 模板描述、环境变量、可选 `tools` 名列表；`packages` 预加载 env |
| `m_mcp_server` | 租户已发布 MCP | `config`、`packages` |
| `m_mcp_server_local` | 工作区草稿 MCP | 同上 |
| `m_agent_mcp_server` | 某智能体绑定的 MCP 副本 | `config` 多为连接信息，**无** tools[] |
| `m_agent_application_local` | 智能体编排脚本 | `config` **加密**；`node_attrs` 仅画布坐标 |
| `m_security_plugin_ability` | **插件**能力入参 | ✅ `input_parameters` JSON 数组 |

## `config` 典型形态（连接，不含 tools）

```json
{
  "mcpServers": {
    "mcp-server-mysql": {
      "command": "npx",
      "args": ["-y", "mcp-server-mysql"],
      "type": "stdio",
      "env": { "MYSQL_HOST": "...", "MYSQL_DB": "..." }
    }
  }
}
```

## `mcp_info` 典型形态（模板元数据）

```json
{
  "id": "...",
  "name": "mcp-server-atip",
  "description": "...",
  "transport": "streamable-http",
  "tools": [
    "query_ip_intelligence",
    "query_domain_intelligence",
    "query_hash_intelligence",
    "query_vul_intelligence"
  ],
  "packages": [{
    "environment_variables": [
      { "name": "ATIP_MCP_SERVER_HOST", "description": "...", "default": "0.0.0.0" }
    ]
  }]
}
```

> `tools` 只有**名字**，没有 `query` 这类入参定义。MySQL 等模板的 `mcp_info` 可能为空。

## catalog 动态字段（`fetch_platform_catalog.py`）

每个 `mcps[]` 条目除原有 `server_id/name/origin/config` 外，可能包含：

| 字段 | 来源 |
|------|------|
| `package_name` | 从 `config.mcpServers.*.args` 推断（如 `mcp-server-mysql`） |
| `environment_variables` | 从 `mcp_info.packages[].environment_variables` |
| `tool_names` | 从 `mcp_info.tools`（工具名字符串数组；**无入参**） |
| `packages` | 表字段 `packages`（密钥已 redact） |

根级 `mcp_tool_names_by_server_id`：`{ "<server_id>": ["tool_a", "tool_b"] }`

## 生成脚本时怎么用（方法）

1. 有 catalog：按 `server_id` 取 `tool_names` → 模型节点可只列 `tools[].name`；工具节点若尚无入参 schema，设计说明标明待动态补全 / UI 确认  
2. 插件仍用 `abilities_by_tool_id[tool_id].input_parameters`（库表直读）  
3. **禁止**在 Skill 包内新增硬编码 MCP 包映射表
