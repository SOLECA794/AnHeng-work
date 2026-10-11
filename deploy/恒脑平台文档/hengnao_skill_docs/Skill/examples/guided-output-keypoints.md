# 样例关键片段：模型节点引导输出

> 来源：`Prompts/模型节点引导生成.json`（Demo_引导生成）  
> 用途：Choice / Json / Regex 三种 guided 配置与条件分流拓扑。

## 架构摘要

```text
交互中转(node3) --条件并行-->
  JC=="Choice"  → node0 GuidedChoice   (guided_choice_expr)
  JC=="Json"    → node1 GuidedJson     (guided_json_expr)
  JC=="Regex"   → node2 GuidedRegex    (guided_regex_expr)
```

用户通过根 `input_parameters` 传入 `JC`（类型）与 `Content`（待分析内容）。

## 1) guided_choice_expr — 意图分类

```json
{
  "type": "llm_mixup",
  "name": "GuidedChoice",
  "system_prompt_template": "你是一位意图分类专家。...【分类指南】\n1、`封禁操作`：...\n9、`聊天操作`：...",
  "user_prompt_template": "【用户需求】\n{{$.kv_user.Content}}\n\n【历史对话】\n{{$.history}}",
  "model": {
    "temperature": 0,
    "guided_choice_expr": {
      "type": "literal",
      "expr": "[\n  \"封禁操作\",\n  \"解封操作\",\n  \"资产查询操作\",\n  \"漏洞查询操作\",\n  \"归并告警查询操作\",\n  \"情报查询操作\",\n  \"攻击行为查询操作\",\n  \"归并告警解读操作\",\n  \"聊天操作\"\n]"
    }
  }
}
```

要点：选项与系统提示词「分类指南」枚举**完全一致**；输出为单个选项字符串，无需 jsonpath。

## 2) guided_json_expr — 结构化参数

```json
{
  "type": "llm_mixup",
  "name": "GuidedJson",
  "system_prompt_template": "你是一位接口调用参数构造专家...\n步骤三：严格按照JSON格式化输出。",
  "user_prompt_template": "[接口文档]\n...\n[用户需求]\n{{- printf \"%s\" $.kv_user.Content -}}",
  "model": {
    "temperature": 0,
    "guided_json_expr": {
      "type": "literal",
      "expr": " {\n  \"type\": \"object\",\n  \"properties\": {\n    \"blockIp\": { \"type\": \"string\" },\n    \"age\": { \"type\": \"string\" }\n  },\n  \"required\": [\"blockIp\", \"age\"]\n}"
    }
  }
}
```

要点：`required` 非空；下游若 jsonpath 抽 `blockIp`，Schema 必须声明该字段。

## 3) guided_regex_expr — 格式片段

```json
{
  "type": "llm_mixup",
  "name": "GuidedRegex",
  "system_prompt_template": "##角色定义\n...\n##任务开始\n用户输入：{{$.kv_user.Content}}",
  "model": {
    "temperature": 0,
    "guided_regex_expr": {
      "type": "literal",
      "expr": "\\d\\d\\d"
    }
  }
}
```

要点：不用 `^` `$` `*`；限长 `{1,n}` 时 n ≤ 50。

## 4) 场景路由：初筛数组（常见修复模式）

**问题**：`大模型_根据用户意图进行推荐` 仅口头要求 JSON 数组，无 guided → `select_list` jsonpath 失败。

**修复**：补 array schema + 结构化用户提示词：

```json
{
  "name": "大模型_根据用户意图进行推荐",
  "system_prompt_template": "## 角色定位\n你是场景智能体路由初筛器。\n\n## 任务描述\n1. 提取用户核心对象、动作、输入形态\n2. 仅依据候选名称与能力简介判断匹配\n3. 无确定匹配返回空数组\n\n## 输出约束条件\n1. 输出 JSON 数组；无匹配为 []\n2. 每项含 id、name、capability、action、object、evidence、confidence\n3. confidence 仅 0.90、0.95、1.00\n4. 禁止 markdown 代码块\n\n## 任务开始\n请根据用户输入与候选列表完成初筛。",
  "user_prompt_template": "【用户需求】\n{{$.kv_user.input}}\n\n【候选智能体】\n{{range $.node2.results}}\n编号：{{.id}}\n名称：{{.name}}\n能力简介：{{.introduce}}\n---------------------\n{{end}}\n\n【硬性要求】\n1. 仅使用名称和能力简介\n2. evidence 须引用能力简介原文功能词\n3. 边界不清晰则不输出该候选",
  "model": {
    "temperature": 0,
    "guided_json_expr": {
      "type": "literal",
      "expr": "{\n  \"type\": \"array\",\n  \"items\": {\n    \"type\": \"object\",\n    \"required\": [\"id\", \"name\", \"capability\", \"action\", \"object\", \"evidence\", \"confidence\"],\n    \"properties\": {\n      \"id\": { \"type\": \"string\" },\n      \"name\": { \"type\": \"string\" },\n      \"capability\": { \"type\": \"string\" },\n      \"action\": { \"type\": \"string\" },\n      \"object\": { \"type\": \"string\" },\n      \"evidence\": { \"type\": \"string\" },\n      \"confidence\": { \"type\": \"number\" }\n    }\n  }\n}"
    }
  },
  "output_parameters": [
    {
      "name": "select_list",
      "type": "string",
      "value": {
        "type": "jsonpath",
        "expr": "$.[*].id",
        "text": { "type": "reference", "expr": "$.node4.result_key" }
      }
    }
  ]
}
```

## 复用建议

1. **先定下游 jsonpath 路径，再反推 Schema 与提示词字段**
2. 枚举类意图 → `guided_choice_expr`，不要硬写 JSON
3. 优化旧脚本时：无结构提示词 → 五段式；有 jsonpath 无 guided → 补 Schema
4. 完整规则见 [reference/guided-output.md](../reference/guided-output.md)
