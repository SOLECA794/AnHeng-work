# 模型节点引导输出（Guided Output）

> 来源：平台「引导生成」能力 + `Prompts/模型节点引导生成.json` 实测样例  
> 用途：约束大模型按 **JSON / 正则 / 选项** 输出，避免 `result_key` 无法被 jsonpath 解析。

## 何时必须使用

| 场景 | 推荐配置 |
|------|----------|
| 下游用 `output_parameters` + `jsonpath` 从 `result_key` 抽字段 | **`guided_json_expr`**（Schema 与字段一一对应） |
| 固定分类 / 意图识别（枚举） | **`guided_choice_expr`**（字符串数组） |
| 固定格式短文本（编号、日期片段等） | **`guided_regex_expr`** |
| 仅展示给用户、不做结构化解析 | 可不用 guided；仍建议五段式提示词 |

**反例**：提示词写「仅输出 JSON 数组」，但未配置 `guided_json_expr`，下游 `jsonpath` 常因多余文字、markdown 包裹、字段漂移而解析失败（如场景路由初筛节点）。

## 三种配置（均在 `model` 对象内）

字段名以平台 JSON 为准，均为 `{ "type": "literal", "expr": "<字符串>" }`：

| 平台字段 | 约束类型 | `expr` 内容 |
|----------|----------|-------------|
| `guided_json_expr` | JSON Schema | 合法 schema JSON 字符串 |
| `guided_choice_expr` | 选项列表 | JSON **数组**字符串，如 `["封禁操作","解封操作"]` |
| `guided_regex_expr` | 正则 | 正则表达式字符串 |

三者互斥，一个模型节点通常只配一种。

### guided_json_expr

- 写清 `type` 与 `properties` 中每个属性的类型
- **`required` 必须列出下游 jsonpath 依赖的字段，不要留空 `[]`**
- object 根：`{"type": "object", ...}`
- 数组根：`{"type": "array", "items": {...}}`

```json
"guided_json_expr": {
  "type": "literal",
  "expr": "{\n  \"type\": \"object\",\n  \"required\": [\"blockIp\", \"age\"],\n  \"properties\": {\n    \"blockIp\": { \"type\": \"string\" },\n    \"age\": { \"type\": \"string\" }\n  }\n}"
}
```

数组输出（初筛候选列表）示例：

```json
"guided_json_expr": {
  "type": "literal",
  "expr": "{\n  \"type\": \"array\",\n  \"items\": {\n    \"type\": \"object\",\n    \"required\": [\"id\", \"name\", \"capability\", \"action\", \"object\", \"evidence\", \"confidence\"],\n    \"properties\": {\n      \"id\": { \"type\": \"string\" },\n      \"name\": { \"type\": \"string\" },\n      \"capability\": { \"type\": \"string\" },\n      \"action\": { \"type\": \"string\" },\n      \"object\": { \"type\": \"string\" },\n      \"evidence\": { \"type\": \"string\" },\n      \"confidence\": { \"type\": \"number\" }\n    }\n  }\n}"
}
```

可与 `$schema` / `description` / `format` 等扩展字段一并写入（平台可接受），但 **`type` + `required` + `properties` 不可缺**。

### guided_choice_expr

`expr` 为 **JSON 数组** 字面量字符串（不是 Go 模板）：

```json
"guided_choice_expr": {
  "type": "literal",
  "expr": "[\n  \"封禁操作\",\n  \"解封操作\",\n  \"资产查询操作\",\n  \"聊天操作\"\n]"
}
```

### guided_regex_expr

**限制（平台说明）：**

- 不支持 `^`、`$`、`*`
- 使用 `{m,n}` 限长时，**n 不要超过 50**

```json
"guided_regex_expr": {
  "type": "literal",
  "expr": "\\d\\d\\d"
}
```

## 与 output_parameters 对齐

典型链路：

```text
llm_mixup.result_key  (guided 约束后的 JSON 字符串)
    → output_parameters[].value.type = jsonpath
    → value.text.expr = $.nodeN.result_key
    → value.expr = $.fieldName 或 $.items[*].id
```

生成时 **Schema 字段名 = 提示词「输出约束」字段名 = jsonpath 的 `expr` 路径**。

## 与提示词配合

1. **系统提示词**：五段式（见 [prompt-template.md](prompt-template.md)）；「输出约束条件」与 Schema 字段一致
2. **用户提示词**：结构化分块（`【用户需求】` / `【候选数据】` / `【硬性要求】` / `【输出格式】`），不用一大段散文
3. 禁止依赖「请输出 JSON」口头约束 alone；**有 jsonpath 下游就必须有 guided**
4. 禁止 \`\`\`json 代码块包裹；guided_json 下模型应直接输出 JSON 文本

## 优化现有脚本（审查清单）

审查或修复用户脚本时：

| 检查项 | 动作 |
|--------|------|
| `llm_mixup` + `jsonpath` 输出 | 补 `guided_json_expr`（或 choice/regex） |
| 提示词无 `##` 分段 | 重构为五段式系统提示词 + 结构化用户提示词 |
| 输出字段与 Schema 不一致 | 同步改提示词、`guided_json_expr`、`output_parameters.expr` |
| 分类节点输出不稳定 | 改用 `guided_choice_expr` + 枚举列表 |
| temperature 过高导致 JSON 漂移 | 结构化输出节点建议 `temperature: 0` |

## 参考

| 资源 | 位置 |
|------|------|
| Demo 三种 guided | `Prompts/模型节点引导生成.json` |
| 关键片段摘要 | [examples/guided-output-keypoints.md](../examples/guided-output-keypoints.md) |
| 合规初筛 guided_json | [examples/compliance-check-keypoints.md](../examples/compliance-check-keypoints.md) |
| Schema 生成工具 | https://www.lddgo.net/string/generate-json-schema |
