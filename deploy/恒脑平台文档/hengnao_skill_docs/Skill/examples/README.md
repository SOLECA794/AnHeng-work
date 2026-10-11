# 样例关键片段索引

本目录只保留**关键片段**与结构摘要，不内置超长完整 JSON。  
生成时优先复制这里的拓扑与字段，再按 [import-compatibility.md](../reference/import-compatibility.md) 补全节点标准字段。

完整官方演示 JSON 在仓库 `../Prompts/新示例集合/`；本目录提炼其精髓，**禁止**照抄示例中的 UUID / uid / org。

## 样例 1：全节点类型（无边）

- **用途**：验证 6 类节点 `type`
- **types**：`llm_mixup`, `act_tool`, `act_agent`, `act_interaction`, `act_interpreter`, `act_coding_agent`
- **edges**：`[]`
- **注意**：仅作 type 对照；真实导入必须带全套标准字段，不要抄「只有 id/name/type」的残缺节点

## 样例 2：单自规划 — 密文研判 Agentic

- **文件**：[coding-agent-keypoints.md](coding-agent-keypoints.md)
- **名称**：Agentic基于本地经验的智能研判多智能体协同
- **架构**：单 `act_coding_agent`（Codex 沙箱）
- **输入**：`input`, `rolefile`(file), `analyzefile`(file)
- **要点**：`coding_agent_config.prompt` 十段（Memory、解码、研判、HTML、邮件）；Skill 有 catalog 用真实 `skill_id`

## 样例 3：复杂流水线 — 金融文档合规检查

- **文件**：[compliance-check-keypoints.md](compliance-check-keypoints.md)
- **名称**：ZGYH金融文档合规检查智能体v1
- **架构**：交互总线 → 并行 fan-out → 文档解析 → 代码分片 → 模型迭代初筛 → 条件深入分析 → 三路并行知识检索 → 报告生成
- **边**：大量 `parallel: true` 条件边 + 轮询 `isCompleted` + Go 模板条件
- **迭代**：`node0` / `node4` / `node5` / `node12` 等
- **知识库**：写入 + 状态轮询 + 混合检索

## 样例 4：交互 — 班级信息收集

- **文件**：[class-info-keypoints.md](class-info-keypoints.md)
- **名称**：Demo_交互节点_班级信息收集
- **要点**：`node2` 交互问答（`questions` + `interactions`）、`Completed==0` 条件边循环、HistoryAnswer 累加
- **参考**：[reference/interaction-node.md](../reference/interaction-node.md)

## 样例 5：排班智能体（关键片段）

- **文件**：[security-roster-keypoints.md](security-roster-keypoints.md)
- **名称**：Demo_排班智能体
- **架构**：文件解析 → 条件轮询取任务结果 → 多轮交互补充需求 → 排班循环 → 自检与后处理 → 生成多个 Excel 文件
- **特点**：
  - 条件边循环：`code == 1`、`Completed == "0"`、`rota_code == "0/1"`
  - 并行条件边：`node11` 分流至 `node12/node14/node13/node16`
  - 三套文件输出：原始排班、后处理、后处理1
  - 代码节点大量使用 `openpyxl` + `FileBucket`

## 样例 6：知识库问答（FAQ + 原文联合检索）

- **文件**：[kb-qna-keypoints.md](kb-qna-keypoints.md)
- **用途**：查询“我的知识库并回答问题”
- **要点**：开始节点后先接空交互总线；FAQ 检索与原文召回并行；总线保留普通边汇总到模型总结；知识库优先用 `labels`

## 样例 6b：全局文件作为记忆存储媒介

- **文件**：[global-file-memory-keypoints.md](global-file-memory-keypoints.md)
- **名称**：Demo_全局文件作为记忆存储媒介的方案
- **架构**：模型意图判断 → 代码节点读写环境文件（保留7天）→ 插件读取回显
- **关键点**：
  - `private_envs` 文件变量作为长期记忆载体
  - `intent==1` 清空历史记忆（保留标题）
  - Markdown 追加写入 + 时间窗口清理

## 样例 7：模型引导输出（Choice / Json / Regex）

- **文件**：[guided-output-keypoints.md](guided-output-keypoints.md)
- **来源**：`Prompts/模型节点引导生成.json`
- **要点**：`guided_choice_expr` / `guided_json_expr` / `guided_regex_expr`；jsonpath 下游必配 guided；场景路由初筛数组 Schema

## 样例 8：官方新示例 — 编排能力精髓

- **文件**：[official-demos-orchestration-keypoints.md](official-demos-orchestration-keypoints.md)
- **来源**：`Prompts/新示例集合/` 中 并发 / 条件 / 多轮 / 规范输出 / 人机交互 / 智能体协作
- **要点**：
  - 并行：总线 + `parallel` fan-out + 普通边汇总
  - 条件：`intent == "..."`（无 `$`）+ guided 意图
  - 多轮：`use_memory: true` + `session_round`
  - 规范输出：guided Schema ↔ 多字段 jsonpath ↔ 根 `output_expression`
  - 人机：`interactions` 播报 → `type_input` 补参 → `type_select` 授权
  - 协作：多 `act_agent` 串行，`rendering` 接力上下文

## 样例 9：官方新示例 — 插件能力精髓

- **文件**：[official-demos-plugins-keypoints.md](official-demos-plugins-keypoints.md)
- **来源**：`Prompts/新示例集合/` 中 知识检索 / 代码执行 / 图片 / 文档 / 图表 / 表格
- **要点**：
  - 知识库：意图分流原文 vs FAQ；`labels` + `associate`
  - 代码执行：`private_envs` + 自定义 `result_key` + `tmp_result == 'error'` 回环
  - 文件门禁：`{name}_attribute.Extension`
  - 图表：工具挂在 `llm_mixup.tools[]`；强制 x/labels/y 二维结构
  - 表格：guided array → 表格插件 → 根 file 输出必声明

## 样例 10：代码节点调用开放接口

- **文件**：[code-node-open-api-keypoints.md](code-node-open-api-keypoints.md)
- **参考**：[reference/open-apis.md](../reference/open-apis.md)
- **来源**：智能体服务 / 知识检索与创作 / 大模型问答 三份开放接口 PDF + 平台代码节点 SDK 说明
- **要点**：
  - Host：沙箱环境变量 **`OPENAPI_HOST`**（`os.environ["OPENAPI_HOST"]`）
  - Agent/RAG：`appKey` + HMAC `sign`；LLM：`Authorization: Bearer <LLMKey>` 或 `OpenAI()`
  - `FileBucket.get/save` 与 agent/document upload 分工
  - 密钥进 `private_envs`；优先非流式；`flag==2` 勿盲重试

## 样例 11：代码节点入参 `args.input`（防 AttributeError）

- **文件**：[code-node-args-input-keypoints.md](code-node-args-input-keypoints.md)
- **事故**：`args.image` → `'Args' object has no attribute 'image'`（2026-08-21）
- **要点**：节点 `input_parameters` 一律 `args.input.<name>`；文件同理；含图片 Vision Base64 正确骨架

## 样例 12：输出参数完整 `value`（输入 / 引用 / 渲染 / jsonpath）

- **文件**：[output-parameters-value-keypoints.md](output-parameters-value-keypoints.md)
- **事故**：`output_parameters` 只有 name/type，下游 `{{$.nodeX.field}}` 为空（2026-08-21，接 Vision 代码节点）
- **要点**：每个输出参数必须配 `value`；从 `result_key` 拆字段用 jsonpath + `text.reference`；对照规范输出官方示例

## 样例 13：`required` 与代码迭代参数规则

- **文件**：[iteration-required-keypoints.md](iteration-required-keypoints.md)
- **来源**：实测「错误示例 / 正确示例」回归（2026-08-24）
- **要点**：
  - `required` 仅根 `input_parameters` 可配；中间节点 `input_parameters` 禁止 `required`
  - 代码节点 `iteration_values.dest_name` 不在同节点 `input_parameters` 重复声明（同名会覆盖逐轮绑定，一次处理整表）
  - 代码内直接用 `args.input.<dest_name>`；下游防御多层/标量包装；结束模板禁用 `json` 函数

## 样例 14：参数 type 与 default_value 原生对齐

- **文件**：[param-type-default-keypoints.md](param-type-default-keypoints.md)
- **事故**：`type: array` 的 `default_value` 写成字符串 → `the value for the parameter must be of array type: items`（2026-08-24）
- **要点**：`default_value` 必须是对应 JSON 原生类型；array/object 禁止 stringify；integer/boolean/file 同步约束

## 样例 15：迭代节点 jsonpath（`$.字段[*]`）

- **文件**：[iteration-jsonpath-keypoints.md](iteration-jsonpath-keypoints.md)
- **事故**：`$.[*].processed_items[*]` → `processed_items` 为空；改为 `$.processed_items[*]` 才有值（2026-08-24）
- **要点**：迭代后 `result_key` 是字符串数组；抽字段写 `$.字段` / `$.字段[*]`，禁止 `$.[*].字段`

## 样例 16：条件分支后 rendering 用 `dig` 兜底

- **文件**：[rendering-dig-fallback-keypoints.md](rendering-dig-fallback-keypoints.md)
- **来源**：场景推荐智能体实测（2026-08-25，`Prompts/错误示例1.md`）
- **事故**：多路并行 + 条件 n 选 1 后，根 `output_expression` 写 `{{$.node11.result}}` → 未走路径时报错
- **要点**：对可能未执行节点字段用 `{{dig "result" "" $.node11}}`；空字符串也是合法输出；可设默认文案

## 样例 17：MCP 三种挂法 + 入参从 DB 赋值

- **文件**：[mcp-wiring-keypoints.md](mcp-wiring-keypoints.md)
- **来源**：用户实测脚本（2026-08-25）
- **要点**：
  - `act_tool`：`mcps[].id` + `tools[].name` + 完整 `options`/`value`
  - `llm_mixup`：`mcps[].id` + `tools[].name` 列表
  - `act_coding_agent`：`coding_agent_config.mcps[]` 字符串数组
  - 插件/子智能体 `options` 须从 catalog/DB 复制

## 场景 → 样例对照

| 用户需求 | 参考样例 | 专题 doc |
|----------|----------|----------|
| 端到端 Agent / 多 Skill | 样例 2 | 01, 02 |
| 合规 / 长文档检查 | 样例 3 | 02, 04, 05 |
| 条件路由 / 并行总线 | 样例 3、**样例 8** | 04 |
| 文件 Excel/Word | 样例 3 node8、样例 5 | 05 |
| 交互问答收集 / 授权确认 | 样例 4、**样例 8** | 05, interaction-node |
| 模型 JSON/枚举/正则约束 | 样例 7、**样例 8 规范输出** | guided-output, prompt-template |
| 安保排班与循环优化 | 样例 5 | 04, 05 |
| 全局文件长期记忆 | 样例 6b | 05 |
| FAQ+原文并行问答 | 样例 6 | 02, 04 |
| 知识库类型分流 / OCR / 文档解析 | **样例 9** | 02, 04, 05 |
| LLM 写码执行回环 | **样例 9** | 03, 04 |
| 图表 / 表格生成与下载 | **样例 9** | 02, 05 |
| 多子智能体协作 | **样例 8** | 03 |
| 多轮对话记忆 | **样例 8** | 01, 03 |
| 代码节点调开放 API / 其它智能体 / RAG HTTP | **样例 10** | open-apis, 05 |
| 代码节点入参 / 图片 Vision | **样例 11** | 03, open-apis |
| 输出参数 value / jsonpath 拆字段 | **样例 12** | 03, json-patterns |
| required 与代码迭代参数规则 | **样例 13** | 03, import-compatibility |
| 参数 type / default_value 对齐 | **样例 14** | 03, json-patterns |
| 迭代节点 jsonpath 抽字段 | **样例 15** | 03, json-patterns |
| 条件分支 rendering / dig 兜底 | **样例 16** | 04, 06-faq-best-practices |
| MCP / 插件 / 子智能体接线 | **样例 17** | 02, platform-catalog |

## 使用方式

生成新脚本时：

1. 选最接近的样例作拓扑参考
2. 只复制关键片段，按 [import-compatibility.md](../reference/import-compatibility.md) 补全节点字段
3. 有 catalog 时绑定真实 `tool_id` / `skill_id` / `agent_id`；否则 `PLACEHOLDER_*`，禁止编造 UUID
4. `python3 scripts/validate_agent_json.py <file>.json [--catalog catalog.json]` 全部 PASS 再交付
