# 模型节点提示词标准模板

> 已确认：不采用 PDF 幻灯片中的多框架变体，统一使用平台实战示例结构。

## 系统提示词结构（`system_prompt_template`）

按以下五段编写（Markdown 标题可用 `##`）：

```markdown
## 角色定位
（领域专家是谁、职责边界）

## 任务描述
1. 第一步……
2. 第二步……
（可编号列出可执行步骤）

## 输出约束条件
1. 输出格式（JSON 字段、类型、枚举）
2. 禁止项（如禁止 ```json 代码块包裹）
3. 空值/缺省规则

## 参考示例
（至少 1 个正例；复杂场景加 1 个缺省/追问反例）

## 任务开始
请根据用户的输入进行……
```

## 用户提示词（`user_prompt_template`）

注入当次数据，使用 Go 模板：

```markdown
用户回复的内容：{{$.node3.HistoryAnswer}}
```

或：

```markdown
待分析文档片段：{{$.chunk}}
```

## 与平台字段对齐

| 提示词段落 | 平台配置 |
|------------|----------|
| 输出 JSON Schema | `model.guided_json_expr`（`type: literal`） |
| 输出固定枚举 | `model.guided_choice_expr`（`type: literal`，expr 为 JSON 数组） |
| 输出正则格式 | `model.guided_regex_expr`（`type: literal`） |
| 用户侧数据 | `user_prompt_template` + `input_parameters` 引用 |
| 结束语渲染 | 顶层 `output_expression`（`type: rendering`） |

完整 guided 规则见 [guided-output.md](guided-output.md)。

## 用户提示词结构化（推荐）

用户提示词除 Go 模板注入外，宜用**固定分块标题**，便于模型对齐推理：

```markdown
【用户需求】
{{$.kv_user.input}}

【候选数据】
{{range $.node2.results}}...{{end}}

【硬性要求】
1. …
2. …

【输出格式】
（与 guided_json Schema 字段一致；有 guided 时可简写，避免与 Schema 冲突）
```

避免把规则、数据、格式混成一段散文。

## 优化现有脚本时的提示词重构

审查或修复用户提供的脚本时，若模型节点存在以下情况，**主动重构**：

| 现象 | 处理 |
|------|------|
| 系统提示词无 `##` 分段、规则堆在一起 | 拆成五段式（角色→任务→约束→示例→任务开始） |
| 用户提示词无分块、`\n` 转义混乱 | 改为 `【用户需求】` 等结构化块 + 正常换行 |
| 口头「仅输出 JSON」但无 guided | 补 `guided_json_expr`，Schema 与 jsonpath 对齐 |
| 意图分类输出不稳定 | 改 `guided_choice_expr` + 枚举与提示词一致 |
| 结构化节点 temperature > 0 | 建议改为 `0` |

**硬规则**：下游 `output_parameters` 使用 `jsonpath` 从 `result_key` 解析时，**必须**配置对应的 guided（通常为 `guided_json_expr`）。

## 参考样例

| 场景 | 位置 |
|------|------|
| 班级信息收集（交互 + 追问） | [examples/class-info-keypoints.md](../examples/class-info-keypoints.md) |
| 合规片段初筛 / 深入分析 | [examples/compliance-check-keypoints.md](../examples/compliance-check-keypoints.md) |
| 自规划研判 | [examples/coding-agent-keypoints.md](../examples/coding-agent-keypoints.md) |
| 引导输出 Choice/Json/Regex | [examples/guided-output-keypoints.md](../examples/guided-output-keypoints.md) |

## 示例压缩原则（强制）

生成提示词时：

1. 只引用示例中的**关键字段、关键边、关键代码逻辑**；
2. 不直接粘贴超长完整 JSON（尤其排班、合规大脚本）；
3. 优先写“结构摘要 + 1~3 个最小片段”；
4. 需要对照结构时，打开对应 `examples/*-keypoints.md`，不要展开超长完整 JSON。

## 模型选型与篇幅

| 模型 | 提示词策略 |
|------|------------|
| DeepSeek（推理） | 讲清楚目标，少举例 |
| 恒脑（通用） | 本模板五段 + Few-shot |
| 高约束 JSON | 恒脑 + `guided_json_expr`；`required` 非空 |
| 意图枚举 | 恒脑 + `guided_choice_expr` |
| 固定格式文本 | 恒脑 + `guided_regex_expr`（禁 `^` `$` `*`；`{m,n}` 中 n≤50） |

Schema 工具：https://www.lddgo.net/string/generate-json-schema
