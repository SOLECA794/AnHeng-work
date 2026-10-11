# 关键片段：输出参数必须配完整 `value`（输入 / 引用 / 渲染 / jsonpath）

> 事故（接「图片文本提取与总结」代码节点，2026-08-21）：  
> `output_parameters` 只写了 `name` + `type` + `description`，**没有 `value`**。  
> 根 `output_expression` 用 `{{$.node_vision.result}}` 时字段为空或下游引用失败。

平台 UI 对赋值的三种叫法与 JSON `value.type` 对应：

| UI / 文档说法 | `value.type` | 何时用 |
|---------------|--------------|--------|
| **输入**（字面量） | `literal` | 固定字符串/数字 |
| **引用** | `reference` | 指向上游 `$.kv_user.*` / `$.nodeN.*` / `$.result_key` |
| **渲染** | `rendering` | Go 模板拼接（常见于根 `output_expression`、子智能体 options） |
| （解析） | `jsonpath` | 从本节点 `result_key` 抽出具名字段；**必须**带 `text.reference` |

## 硬规则

1. **凡声明的输出参数，必须带完整 `value`**。禁止只有 `name`/`type`/`description`。
2. 要用 `$.nodeX.field` 引用时，该 `field` **必须**是该节点 `output_parameters[].name`，且其 `value` 已绑定。
3. 从模型/代码返回的 JSON 拆字段 → 用 **`jsonpath` + `text` 指向 `$.result_key`**（本节点内常写 `$.result_key`）。
4. 根结束回答用 **`output_expression.type = rendering`**，模板里引用已绑定字段或 `$.nodeX.result_key`。
5. 文件下载：根 `output_parameters` 项 `type: "file"` + `value.type: "reference"`。

`output_parameters: []` 表示不拆字段、只靠 `result_key` —— 合法；一旦列出某字段，就必须配 `value`。

## ❌ 错误（缺 value）

```json
"output_parameters": [
  {
    "name": "result",
    "type": "string",
    "description": "模型返回的图片文字提取与总结结果"
  },
  {
    "name": "image_name",
    "type": "string",
    "description": "经过解析的图片文件名"
  }
]
```

下游 `{{$.node_vision.result}}` **拿不到** handler 返回里的 `result`。

## ✅ 正确：代码 / 模型节点拆字段（jsonpath）

handler / 模型返回：`{"result": "...", "image_name": "..."}`，且 `result_key` 为默认名时：

```json
"result_key": "result_key",
"output_parameters": [
  {
    "name": "result",
    "type": "string",
    "description": "图片文字提取与总结结果",
    "value": {
      "type": "jsonpath",
      "expr": "$.result",
      "text": {
        "type": "reference",
        "expr": "$.result_key"
      }
    }
  },
  {
    "name": "image_name",
    "type": "string",
    "description": "解析后的图片文件名",
    "value": {
      "type": "jsonpath",
      "expr": "$.image_name",
      "text": {
        "type": "reference",
        "expr": "$.result_key"
      }
    }
  }
]
```

根回答：

```json
"output_expression": {
  "type": "rendering",
  "expr": "{{$.node_vision.result}}"
}
```

`jsonpath.expr` 的字段名必须与 `return` dict / guided Schema 的 key **一致**。

**迭代节点**：`result_key` 是按轮字符串数组时，写 `$.processed_items[*]`，不要写 `$.[*].processed_items[*]`（会抽空）。见 [iteration-jsonpath-keypoints.md](iteration-jsonpath-keypoints.md)。

## ✅ 正确：规范输出官方形态（对照）

来源：`Prompts/新示例集合/规范输出-使用示例.json` —— 每个字段都是 `jsonpath` + `text.reference → $.result_key`：

```json
{
  "name": "ip_address",
  "type": "string",
  "value": {
    "type": "jsonpath",
    "expr": "$.ip_address",
    "text": { "type": "reference", "expr": "$.result_key" }
  }
}
```

`llm_mixup` 用 jsonpath 时还须配 `guided_json_expr`（见 [guided-output.md](../reference/guided-output.md)）。  
**代码节点**返回的是结构化 dict，一般不需要 guided，但仍须给每个拆出字段配 jsonpath。

## ✅ 正确：根文件输出（reference）

```json
"output_parameters": [
  {
    "name": "f",
    "type": "file",
    "value": {
      "type": "reference",
      "expr": "$.node2.f"
    }
  }
]
```

## 三种赋值速查（入参同样适用）

```json
{ "type": "literal", "expr": "固定文案" }
{ "type": "reference", "expr": "$.kv_user.image" }
{ "type": "rendering", "expr": "结果：{{$.node1.result}}" }
```

## 生成自检

- [ ] 每个非空 `output_parameters[]` 都有 `value.type` + `expr`
- [ ] `jsonpath` 必有 `text: { type: reference, expr: "$.result_key" }`（或本节点自定义 result_key 名）
- [ ] `return {..}` / Schema 字段名 ↔ `jsonpath.expr` ↔ `output_parameters[].name` 三者对齐
- [ ] 根 `output_expression` 只引用已绑定字段或 `result_key`
- [ ] 跑过 `scripts/validate_agent_json.py`

完整范式：[json-patterns.md](../reference/json-patterns.md)、[docs/03-params-iteration.md](../docs/03-params-iteration.md)。
