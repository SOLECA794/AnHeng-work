# 样例关键片段：金融文档合规检查

> 来源：`Prompts/SkillGeneratePrompt.md` 历史导出（已提炼，非完整脚本）

## 目标

用户提供合规要求文件 + 待检查文档，切块后做合规检测，输出带违规项的 Word 报告。

## 架构摘要

```text
交互总线(node3)
  ├─ 并行: 解析待检文档(node2) → 代码切块(node1)
  ├─ 并行: 合规要求写入知识库(node4) → 轮询写入状态(node5/node9)
  ├─ 并行: 临时/全局合规文件解析与裁剪(node22/node25/…)
  └─ 普通边汇总 → 模型初筛迭代(node0)
        → 深度分析调度(node12)
            ├─ 需深入: 三路并行知识检索(node6/10/11) → 模型分析(node7) → 写入报告(node18)
            └─ 不需深入: 直接写入报告(node18)
        → 合并生成 Word(node8) → 交互确认是否删知识库(node19)
```

入口：`entry = node3`（交互总线）。

## 节点职责（按 id）

| id | 类型 | 职责 |
|----|------|------|
| node3 | `act_interaction` | 交互总线 / fan-out 入口 |
| node2 | `act_tool` | 待检文档解析 |
| node1 | `act_interpreter` | 按分隔符切块 → `chunks` |
| node4 | `act_tool` | 合规要求写入知识库 |
| node5 / node9 | tool + code | 写入状态轮询（`isCompleted`） |
| node0 | `llm_mixup` + 迭代 | 每片段提取 2 关键词 + 是否需深入 |
| node12 | `act_interpreter` | 调度下一待深入片段 |
| node17 | `act_interaction` | 检索子总线 |
| node6 / 10 / 11 | `act_tool` | 关键词1 / 关键词2 / 段落总结 三路检索 |
| node7 | `llm_mixup` | 结合知识库做合规结论 |
| node18 | `act_interpreter` | 片段结论追加到 Markdown |
| node8 | `act_interpreter` | Markdown → Word 报告（`python-docx` + `FileBucket`） |
| node19 / 20 | 交互 + 插件 | 确认并删除本轮知识库 |

## 关键边模式

### 1) 交互总线 fan-out（多并行 + 一条普通边）

```json
{"parallel": true, "src_node": "node3", "dst_node": "node2"}
{"condition": "len(kv_user.complianceReqDocs) > 0", "parallel": true, "src_node": "node3", "dst_node": "node4"}
{"condition": "len(kv_user.tempComplianceReqDoc) > 0", "parallel": true, "src_node": "node3", "dst_node": "node22"}
{"condition": "len(kv_user.input) > 0", "parallel": true, "src_node": "node3", "dst_node": "node23"}
{"src_node": "node3", "dst_node": "node0"}
```

### 2) 知识库写入轮询

```json
{"condition": "isCompleted == false", "src_node": "node9", "dst_node": "node5"}
```

### 3) 深度分析 n 选 1

```json
{"condition": "deepAnalyzeNo <= totalCount && needDeepAnalyze == true", "src_node": "node12", "dst_node": "node17"}
{"condition": "deepAnalyzeNo <= totalCount && needDeepAnalyze == false", "src_node": "node12", "dst_node": "node18"}
{"src_node": "node12", "dst_node": "node8"}
```

### 4) Go 模板条件（交互选项）

```json
{
  "condition": "{{if hasKey $.node19.deleteKnowledge._answer \"A\"}}true{{else}}false{{end}}",
  "src_node": "node19",
  "dst_node": "node20"
}
```

## 提示词策略

- node0 初筛：五段式 + `guided_json`（`number` / `keywords[2]` / `summary` / `needDeepAnalyze`）
- node7 深入：合规依据优先级（全局文件 > 临时文件 > 用户输入 > 知识库 > 内置兜底）
- 禁止输出 \`\`\`json 代码块；字段名与 Schema 一致

## 复用建议

1. 「先检索再总结」：模型提关键词 → 插件检索 → 模型分析，不要单模型挂全部知识库
2. 超长文档：代码切块 + `iteration: true`
3. 插件 `tools[].id`：有 catalog 时用系统文档解析/知识写入/检索的真实 `tool_id`；否则 `PLACEHOLDER_PLUGIN_*`，禁止抄未经核对的 UUID
4. 报告文件必须在 `output_parameters` 声明 `type: "file"`
