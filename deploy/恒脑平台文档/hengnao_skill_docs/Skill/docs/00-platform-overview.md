# 恒脑智能体平台概览

> 来源：平台节点说明；导入规则见 [import-compatibility.md](../reference/import-compatibility.md)

## 编排模型

恒脑智能体开发平台通过**拖拉拽**编排智能体。除开始/结束节点外，包含 **6 类业务节点**：

| # | 节点 | 脚本 `type` | 职责 |
|---|------|-------------|------|
| — | 开始节点 | （顶层 `input_parameters`） | 环境变量、用户输入参数 |
| — | 结束节点 | （顶层 `output_parameters`） | 输出变量/文件、界面回答内容 |
| 1 | 模型节点 | `llm_mixup` | 系统/用户提示词推理；`guided_json` / `guided_choice` / `guided_regex` 约束输出 |
| 2 | 代码节点 | `act_interpreter` | Python 或 JS 工程化任务；可调平台开放接口（Agent/RAG/LLM），见 [open-apis.md](../reference/open-apis.md) |
| 3 | 智能体节点 | `act_agent` | 引用已发布智能体作为子流程 |
| 4 | 插件节点 | `act_tool` | 调用平台已配置接口 |
| 5 | 交互节点 | `act_interaction` | 对外消息反馈、用户问答交互 |
| 6 | 自规划节点 | `act_coding_agent` | Codex 沙箱 + Skill/MCP，自主规划执行 |

## 边（连接）

节点间通过**边**连接，支持并行与串行。边条件支持 **Expr** 与 **Go 模板**：

**解析顺序：先 Go 模板渲染 → 再 Expr 执行。**

纯 Expr 条件（如 `len(node2.results) > 0`）**不要**写 `$.` 前缀；`$` 仅用于 Go 模板 `{{}}` 内，或节点传参的 `value.expr`（reference）。

详见 [04-edges.md](04-edges.md)。

## 脚本形态

平台智能体导出为 **JSON 脚本**，顶层常见字段：

- `cot_version`: `"v2"`
- `eng_version`: 如 `"v2.3.8"`（当前最新引擎版本）
- `entry`: 入口节点 id
- `nodes` / `edges`
- `input_parameters` / `output_parameters` / `output_expression`
- `private_envs` / `public_envs`

完整样例见 [examples/README.md](../examples/README.md)。生成脚本必须满足 [import-compatibility.md](../reference/import-compatibility.md)。
