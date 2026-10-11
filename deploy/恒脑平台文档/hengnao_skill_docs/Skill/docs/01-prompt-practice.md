# 01 提示词实践

> 来源：恒脑智能体服务构建典型场景分析（提示词实践）-智能体合伙人.pdf  
> 作者：黄冲 · 安恒信息中央研究院 · 2025.3.18

## 提示词在智能体中的位置

提示词工程（Prompt Engineering）是**人机对齐**的手段，在智能体范式中承担：

```
用户输入需求
    → 感知/输入（短期记忆）
    → 规划（提示词 + LLM）
    → 调度插件/工具 + 检索知识库（长期记忆/RAG）
    → 反思调整
    → 整合输出
```

**系统提示词** vs **用户提示词**：

| 类型 | 典型内容 |
|------|----------|
| 系统提示词 | 角色、规则、输出 Schema、工具使用说明、Few-shot |
| 用户提示词 | 本次任务的具体数据（可用 Go 模板注入 `{{$.nodeX}}`） |

## 标准提示词模板（已确认）

不采用 PDF 多框架变体，统一五段式。详见 [reference/prompt-template.md](../reference/prompt-template.md)：

1. **角色定位**
2. **任务描述**（编号步骤）
3. **输出约束条件**
4. **参考示例**（正例 + 必要时反例）
5. **任务开始**

用户提示词用 Go 模板注入当次数据：`{{$.kv_user.input}}`、`{{$.node3.HistoryAnswer}}` 等。

## 案例：安保活动排班

排班表包含：姓名、职级、小组（指挥组 / 监测组 / 研判组 / 处置组）。

实践要点：

| 步骤 | 说明 |
|------|------|
| 讲清楚目标 | 排班约束、人数、技能匹配 |
| 选好模板 | 角色 + 任务 + 输出表格结构 |
| 任务拆解 | 按组/按班次拆分子任务 |
| 选对模型 | 复杂约束用推理模型；格式化输出用通用模型 + Guided Json |
| 持续迭代 | 根据平台调试结果收紧约束 |

## 实践目标：人模合一

- 提示词应让模型「像领域专家一样思考」，而非堆砌关键词
- 插件/知识库能力必须在提示词中**写清场景与调用时机**（见 [02-plugins-knowledge-base.md](02-plugins-knowledge-base.md)）

## 平台差异（FAQ 摘录）

| 模型 | 特点 | 提示词策略 |
|------|------|------------|
| DeepSeek（推理） | 擅长复杂推理，对提示词敏感 | 讲清楚目标，**少举例** |
| 恒脑模型（通用） | 指令遵循好 | **角色 + 任务 + 约束 + Few-shot** |
| 高约束结构化输出 | — | 恒脑模型 + Guided Choice/Regex/Json |

**Guided 输出要点**（详见 [reference/guided-output.md](../reference/guided-output.md)）：

- 下游 `jsonpath` 解析 `result_key` → **必须**配 `guided_json_expr`（或 choice/regex）
- `guided_json`：`required` 非空，写清 `properties` 类型
- `guided_regex`：不用 `^` `$` `*`；`{m,n}` 中 n ≤ 50
- 优化旧脚本：提示词改五段式 + 用户提示词分块；口头 JSON 约束不够，必须 Schema

Json Schema 生成工具：https://www.lddgo.net/string/generate-json-schema

## 参考样例

- 班级信息收集：`examples/class-info-keypoints.md`（交互 + 追问式提示词关键片段）
- 合规检查：`examples/compliance-check-keypoints.md`（初筛 / 深入分析提示词策略）
- 自规划研判：`examples/coding-agent-keypoints.md`（`coding_agent_config.prompt` 十段结构）
- 引导输出：`examples/guided-output-keypoints.md`（Choice/Json/Regex + 场景路由初筛修复）
