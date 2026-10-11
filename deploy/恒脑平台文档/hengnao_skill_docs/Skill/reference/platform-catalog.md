# 运行时平台目录（插件 / MCP / Skill / 知识库）

Skill 包**不内置**任何租户的 UUID 清单。生成可落地脚本时，用当前恒脑库 + `user_id` 或 `tenant_id` **动态拉取**当前环境可访问的资源，再把真实 ID 写进 JSON。

不要把数据库密码写进本文件、SKILL.md 或 git。

## 何时拉取

用户调用本技能时，若提供了：

1. 可访问的恒脑数据库连接（推荐 5 个已解析的标准系统环境变量，或 `HENGNAO_DB_DSN`）
2. 当前用户 ID（`m_sys_user.id`）或租户 ID

则**先拉取 catalog，再编排**。目标是在当前环境资源下生成最大化可运行的智能体，而不是只搭空骨架。

推荐优先使用 `user_id`：

- `user_id` 模式：可准确包含 `visible_range=3`（仅自己）资源。
- `tenant_id` 模式：适合批量/后台场景，不包含“仅自己”资源，避免误拿其他人的私有条目。

未提供数据库时：仍可生成框架，`tools[].id` / `skills[]` / MCP 用 `PLACEHOLDER_*`，设计说明列出待填项。**禁止编造 UUID。**

## 数据库信息取值顺序

1. **系统环境变量优先**：优先使用已解析的 `HENGNAO_DB_DSN`，或 5 个拆分变量。不要为了使用用户输入而忽略有效的环境变量。
2. **用户 input 兜底**：仅当某个数据库字段在环境变量中缺失、为空或仍是未解析模板时，才从用户**显式提供**的数据库配置中补齐该字段。只提取数据库配置语义，不要从普通业务文本、示例、日志或其他无关字段猜测连接信息。
3. **缺失时受限执行**：补齐后仍不完整，输出：

   > 未提供恒脑数据库连接信息，无法拉取运行时目录；本次仅能生成占位符骨架，部分业务受限。

   然后继续执行不依赖数据库的设计、JSON 生成和无 catalog 校验，不要阻塞整个任务。

可识别的用户 input 形态：

- 单字段：`dsn` / `url` / `connection_string` / `connection_uri`，值必须以 `postgres://`、`postgresql://` 或 `hg://` 开头。
- 对象：`db` / `database` / `db_config` / `database_config` 下的 `host`、`port`、`user` / `username`、`password`、`dbname` / `name`。
- 平铺字段：`db_host` / `host`、`db_port` / `port`、`db_user` / `user`、`db_password` / `password`、`db_name` / `dbname`。
- 中文标签：`地址`（可包含 `host:port`）、`端口`、`用户名`、`密码`、`数据库` / `库名`。

提取与使用约束：

- 解析出的 `host:port` 必须拆分为 host 与 port；host 只允许主机名或 IP。
- 环境变量已有的字段优先；仅把 input 中对应缺失字段的值作为进程内临时连接参数。
- 用户 input 中的密码只用于本次数据库连接，不写入生成 JSON、catalog、日志或交付说明。
- input 中若仍有 `{{...}}` / `$.kv_envs.*`，视为未解析，不得送入数据库驱动。
- 不能拼凑看似合理的默认密码、主机、库名或租户 ID。

## 命令

默认连接优先使用系统环境变量。5 个拆分变量必须全部是**已解析的普通字符串**：

| 环境变量 | 要求 | 示例形态 |
|----------|------|----------|
| `HENGNAO_DB_HOST` | 仅主机名或 IP，不拼端口/用户/密码/数据库名 | `<host>` |
| `HENGNAO_DB_PORT` | 纯数字端口 | `<port>` |
| `HENGNAO_DB_USER` | 仅数据库用户名 | `<user>` |
| `HENGNAO_DB_PASSWORD` | 仅数据库密码，运行时注入 | `<secret>` |
| `HENGNAO_DB_NAME` | 仅数据库库名 | `<dbname>` |

`$.kv_envs.*` / `{{...}}` 是恒脑编排层的参数引用语法，**不是进程启动时的 OS 环境变量取值**。写入这些 OS 变量前必须先由外部配置系统解析成字面量；否则脚本会拒绝连接并提示未解析。

连接前可做无泄漏预检（只输出布尔结果，不输出配置值）：

```bash
python3 - <<'PY'
import os, re
keys = ["HENGNAO_DB_HOST", "HENGNAO_DB_PORT", "HENGNAO_DB_USER",
        "HENGNAO_DB_PASSWORD", "HENGNAO_DB_NAME"]
print("all_set =", all(os.environ.get(k) for k in keys))
print("host_shape_ok =", bool(re.fullmatch(r"[A-Za-z0-9._-]+", os.environ.get("HENGNAO_DB_HOST", ""))))
print("port_shape_ok =", bool(re.fullmatch(r"[1-9][0-9]{0,4}", os.environ.get("HENGNAO_DB_PORT", ""))))
print("all_resolved =", not any("{{" in (os.environ.get(k) or "") or (os.environ.get(k) or "").startswith("$.kv_envs.") for k in keys))
PY
```

常见错误：

- `HENGNAO_DB_HOST` 仍为 `{{$.kv_envs...}}`：配置平台未完成解析；改为运行时字面量，或先在编排层解析后再注入 OS 进程。
- `HENGNAO_DB_HOST` 混入 `:<port>`、`@` 或整段 DSN：HOST 只保留主机名/IP，端口用 `HENGNAO_DB_PORT`。
- 连接错误显示 `localhost:5432`：连接参数缺失或未被目标进程继承；先跑上面的无泄漏预检。

从用户 input 补齐后，同样必须通过上述形态和解析检查；不完整时进入受限模式，不得尝试连接。

推荐命令：

```bash
python3 Skill/scripts/fetch_platform_catalog.py \
  --user-id "$HENGNAO_USER_ID" \
  -o catalog.json
```

仅确认用户归属租户、不需要资源目录时，使用轻量只读查询：

```bash
python3 Skill/scripts/query_user_tenant.py \
  --user-id "$HENGNAO_USER_ID" \
  --tenant-id "$HENGNAO_TENANT_ID" \
  --pretty
```

`--tenant-id` 可选；提供时输出 `tenant_id_matches`，用于校验编排入参中的 `orgID` 是否与库中归属一致。

等价环境变量：`HENGNAO_USER_ID` / `HENGNAO_TENANT_ID`，加上上表 5 个拆分变量，或一条 `HENGNAO_DB_DSN`。脚本不内置主机、端口、用户或库名默认值；任一字段缺失时，必须从用户显式 input 补齐后再传入子进程参数，仍不完整则进入受限模式。密码仍必须来自 `HENGNAO_DB_PASSWORD`、`--password` 或本次 input 提取值，且不得持久化。

其中 `HENGNAO_DB_PASSWORD` **不会**被硬编码到技能包里；仍需你在运行时通过环境变量或 `--password` 提供。

离线技能包会自带 `Skill/vendor/python/`，其中包含纯 Python PostgreSQL 驱动 `pg8000` 及依赖；`scripts/fetch_platform_catalog.py` 与 `scripts/query_user_tenant.py` 启动时会自动优先加载，因此不需要每次沙箱重新联网安装数据库驱动。`fetch_platform_catalog.py` 为 `pg8000` 提供了基础 URL DSN 兼容层（支持 `sslmode=disable/prefer`）；`query_user_tenant.py` 只使用 5 个拆分环境变量，避免额外 DSN 分支。

可选：

- `--limit-agents 40`：参考智能体摘要条数（市场与工作区各最多 N 条）；`0` 跳过
- `--callable-agent-limit 300`：拉取可调用子智能体目录上限（market）
- `--agent-id <id>`：查一条智能体**元数据**（名称/简介）。工作区 `config` 与市场脚本正文通常**加密**，不能当完整 JSON 模板展开

生成后校验：

```bash
python3 Skill/scripts/validate_agent_json.py agent.json --catalog catalog.json
```

## 脚本字段 ← 库字段

| 脚本位置 | 目录字段 | 库表 |
|----------|----------|------|
| 根 `uid` | `user.id` | `m_sys_user.id` |
| 根 `org` | `user.tenant_id` | `m_sys_user.tenant_id` |
| `tools[].id` | `plugins[].abilities[].tool_id` | `m_security_plugin_ability.tool_id`（**不是** `toolkit_id`） |
| `tools[].options[].name` | 对应能力的 `input_parameters`（已去掉 header/密钥） | `input_parameters` |
| `coding_agent_config.skills[]` | `skills[].skill_id` | `m_security_skill.skill_id` |
| 节点 `mcps[].id` | `mcps[].server_id` | `m_mcp_server` / `_local` / `_template` |
| `mcps[].tools[].options[]` | MCP 工具入参：**无独立 DB 表**；catalog 动态 `tool_names`；完整入参靠平台动态能力（Skill 不维护硬编码映射） | 见 [mcp-db-schema.md](mcp-db-schema.md) |
| `coding_agent_config.mcps[]` | `mcps[].server_id`（**字符串数组**） | 同上 |
| `act_agent.agents[].id` | `callable_agents[].agent_id` | `m_agent_application_market.agent_id` |
| `act_agent.agents[].options[]` | `callable_agents[].input_parameter_hints` + DB 入参定义 | 见 [mcp-wiring-keypoints.md](../examples/mcp-wiring-keypoints.md) |
| 知识写入/检索 options 里的库 id | `knowledge_bases[].id` | `m_knowledge_base.id` |
| `model.model` / `coding_agent_config.model` | `models[].name` | 租户用量 + 默认 `HengNao-v4` / `HengNao-r1` / `GLM-5.1` |

系统内置 API 能力在 `m_security_plugin`（`type=system`）中，**不会**复制进 `*_local`。生成时必须把 **系统目录 + 租户可见行** 合在一起用。

## 可见范围

`m_dict.group_code = agent_publish_visible_range`：

| 值 | 含义 | 本脚本是否纳入 |
|----|------|----------------|
| 1 | 完全公开 | 是（本租户） |
| 2 | 同租户 | 是 |
| 5 | 受限公开 | 是 |
| 3 | 仅自己 | 仅 `create_id = 当前用户` |
| NULL | 常见于系统插件 | 按公开处理 |

另外始终包含：

- 系统 API 插件：`m_security_plugin.type = system`
- 系统 Skill：`m_security_skill.tenant_id = 0`
- 启用中的 MCP 模板：`m_mcp_server_template.status = 1`
- 本租户工作区副本：`*_local`（按 `toolkit_id` / `server_id` 去重，已发布优先）

知识库无 `tenant_id=0` 的系统行；只拉**当前租户**且可见的库。

## 产出结构（摘要）

```json
{
  "user": { "id": "...", "name": "...", "tenant_id": "..." },
  "id_mapping": {},
  "models": [{ "name": "HengNao-v4" }],
  "plugins": [{
    "toolkit_id": "...",
    "name": "文档解析工具集",
    "origin": "system",
    "abilities": [{
      "tool_id": "...",
      "name": "文档解析任务创建",
      "input_parameters": [{ "name": "file", "type": "file", "required": true }]
    }]
  }],
  "capability_index": [{ "capability": "文档解析", "matches": [] }],
  "mcps": [{ "server_id": "...", "name": "...", "origin": "template" }],
  "skills": [{ "skill_id": "...", "name": "...", "origin": "system" }],
  "callable_agents": [{
    "agent_id": "...",
    "name": "...",
    "input_parameter_hints": ["input", "alarmData"],
    "invocation_examples": ["..."],
    "resources": { "plugins": [], "mcps": [], "skills": [], "knowledge_bases": [] }
  }],
  "knowledge_bases": [{ "id": "...", "name": "...", "type_hint": "doc" }],
  "indexes": { "tool_ids": [], "skill_ids": [], "server_ids": [], "agent_ids": [], "knowledge_base_ids": [] }
}
```

`origin`：`system` / `tenant_published` / `workspace_local` / `template`。

密钥类参数（`hn-token`、`authorization`、header）已剥离，不要把 catalog 里的红acted 值写回脚本。平台导入后由租户凭证注入。

## 生成时怎么用 catalog

1. 读 `capability_index`：按业务选能力（文档解析、知识写入、原文召回、代码执行等），**原样复制** `tool_id` 与 body 参数名。
2. 知识库：从 `knowledge_bases` 按名称选库，把 **字符串形式的 id** 填进写入/检索插件 options（bigint 不要当 JS number，以免丢精度）。
3. 自规划：从 `skills` 按名称/描述多选，填 `coding_agent_config.skills`；MCP 填 **`coding_agent_config.mcps[]` 字符串**（= `server_id`）。
4. **工具/MCP/子智能体入参（Q-R01）**：插件 `tools[].options`、工具节点 MCP `mcps[].tools[].options`、子智能体 `agents[].options` 的 **name/type/required/description 必须从 catalog/DB 复制**，再为每项配 `value`（引用/输入）。禁止猜参数名。形态见 [mcp-wiring-keypoints.md](../examples/mcp-wiring-keypoints.md)。
5. 模型节点挂 MCP：节点 `mcps[].id` + `tools[].name` 列表即可，通常不写 options。
6. 智能体节点：`act_agent.agents[].id` 用 `callable_agents[].agent_id`；`options` 按 hints + DB 定义组装。
7. 根 `uid` / `org` 填 catalog 的 `scope.user_id` / `scope.tenant_id`（tenant 模式无 user_id 时 `uid` 留空或占位）。
8. 模型：优先租户已在用的名字；自规划常用 `GLM-5.1` 或 catalog 里存在的同等模型。
9. **能力撞名**（同名 system / 租户各一份）：
   - 用户描述明确指向自有能力（如「**我的**文档解析」「租户下的 XX」）→ 优先 `origin=tenant_published`（或 `workspace_local`）中名称/描述最贴的一项
   - 用户**未**指明 → **默认 system 优先**：`system` > `tenant_published` > `workspace_local`
   - 静默选定并在设计说明写明绑定的 `tool_id` / 名称 / `origin`；仅当描述歧义大、多个候选同样贴合时再问用户
10. catalog 里没有的能力才用 `PLACEHOLDER_*`，并在设计说明列出。
11. **禁止**使用未出现在 `indexes.*` 里的 UUID。

## 最大化可用（原则）

在合法可见资源内，主动接线，而不是只生成空 `tools: []`：

- 文档/合规类：系统「文档解析」+「知识写入」+「状态查询」+「原文内容召回」/ FAQ 检索 + 用户知识库 id
- 研判/多 Skill：系统 + 租户 Skill 凡名称匹配 Memory / 总结 / 翻译 / 邮件 / 审计 的都挂上；再补相关 MCP
- 图表/报告：图表生成、文档生成系统能力
- 不要把一个模型节点挂上整个 catalog；按拓扑拆插件节点，模型只做判断

参考智能体列表只帮助选场景名称，**拓扑仍按** `examples/*-keypoints.md` 与导入硬规则生成（库内脚本正文不可解密）。
