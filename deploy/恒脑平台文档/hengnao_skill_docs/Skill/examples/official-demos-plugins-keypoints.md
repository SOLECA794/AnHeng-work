# 官方新示例精髓：插件能力（知识检索 / 代码执行 / 图片 / 文档 / 图表 / 表格）

> 来源：`Prompts/新示例集合/`（2026-08）  
> 插件 `tools[].id` **禁止**照抄示例 UUID；有 catalog 按名称匹配真实 `tool_id`，否则 `PLACEHOLDER_*`。

## 总览

| 示例 | 核心能力 | 精髓一句话 |
|------|----------|------------|
| 知识检索 | 原文召回 vs FAQ 召回分流 | 意图路由 → 不同知识库插件 + `labels` → 专用回答模型 |
| 代码执行 | LLM 写码 → 插件跑码 → 失败回环 | `private_envs` 存代码；`result_key` 改名；条件边重试 |
| 图片识别 | 文件门禁 + OCR | `*_attribute.Extension` 条件；再调图片提取插件 |
| 文档解析 | 文件门禁 + 文档拆解 | 扩展名白名单；`split_mode=page/toc` |
| 图表生成 | 意图分流 + 模型挂图表工具 | `llm_mixup.tools[]` 挂图表能力；根声明文件输出 |
| 表格生成 | JSON 数组 → 表格插件 | guided array → `act_tool` 表格生成 → 根 `file` 输出 |

---

## 1) 知识检索 — 库类型分流（非并行联合）

与 [kb-qna-keypoints.md](kb-qna-keypoints.md)「FAQ+原文**并行**」不同，本示例是 **意图二选一**：

```text
意图识别(文本|问答对)
  --intent=="文本"--> 原文召回插件 --> 基于文本库回答
  --intent=="问答对"--> FAQ 召回插件 --> 基于问答对回答
```

原文召回参数精髓（`labels` + `associate`）：

```json
{
  "options": [
    { "name": "query", "type": "string", "required": true,
      "value": { "type": "reference", "expr": "$.kv_user.input" } },
    { "name": "size", "type": "integer", "value": { "type": "literal", "expr": "1" } },
    { "name": "labels", "type": "array",
      "value": { "type": "associate", "expr": "[\"PLACEHOLDER_KB_LABEL\"]" } }
  ]
}
```

FAQ 召回额外常见项：`answerNum`、`withKeywords`（boolean literal）。

要点：

1. **优先 `labels`，不要无依据填 `permissions`。**
2. 回答模型把召回结果绑进 `input_parameters`，提示词强调「只依据背景知识、勿扩展」。
3. 意图节点同样建议配 `guided_json_expr`（字段 `intent`/`reason`）。

选型：

- 需要两路证据一起看 → 用并行总线（kb-qna 样例）
- 明确两类问题互斥 → 用本示例条件分流

---

## 2) 代码执行 — 生成 / 执行 / 播报 / 判错回环

```text
生成代码(node2, result_key=tmp_code)
  → 代码执行插件(node1, code=$.node2.tmp_code, lang=python)
  → 交互播报执行结果(node4)
  → 问题回答(node3, result_key=tmp_result)
       --tmp_result == 'error'--> 回到 node2 重新生成
```

根级私有变量：

```json
"private_envs": [{ "hidden": true, "name": "tmp_code", "type": "string" }]
```

生成代码模型精髓：

1. **只输出可执行代码**：禁止 markdown 围栏、禁止附带说明。
2. 强制 `handler(args: Args)` 模板；数据从 `args.input.kv_user.input` 取，并去掉题干只留数据。
3. 文件名格式：`file:{文件名}<{uuid}>`；用 `FileBucket` put/get。
4. `result_key` 设为 `tmp_code`，与插件入参、`private_envs` 对齐。

判错模型：若执行结果像报错则只输出 `error`，边条件：

```json
{"condition": "tmp_result == 'error'", "parallel": false, "src_node": "node3", "dst_node": "node2"}
```

要点：回环依赖 **重命名的 result_key**（`tmp_code` / `tmp_result`），边条件读裸字段名。

---

## 3) 图片识别 — 文件属性门禁

```text
交互总线(node3)
  -- input_img != "" && input_img_attribute.Extension in ['jpg','png'] --> OCR插件 → 基于图片内容回复
  -----------> 无图/不支持时的兜底交互(node4)
```

条件精髓（注意 **文件变量名 + `_attribute.Extension`**）：

```json
{
  "condition": "input_img != \"\"  &&  input_img_attribute.Extension in ['jpg', 'png']",
  "parallel": false,
  "src_node": "node3",
  "dst_node": "node1"
}
```

插件参数：`file` ← `$.kv_user.input_img`；`ocrType` 默认 `ocr_system`（文本），可选 `structure_table`。

下游模型引用提取结果：示例用 `{{$.node1.data.text}}`（以实际插件出参为准，生成前用 catalog/实测确认路径）。

总线可用 Go 模板播报是否检测到图片：

```gotemplate
{{if eq $.kv_user.input_img ""}}未检测到用户提交图片{{else}}即将调用图片内容提取插件{{end}}
```

---

## 4) 文档解析 — 扩展名白名单 + 拆分模式

与图片同构，文件字段示例为 `f`：

```json
{
  "condition": "f != \"\"  &&  f_attribute.Extension in ['doc', 'docx','pdf','txt','json','pptx','xlsx']",
  "parallel": false,
  "src_node": "node3",
  "dst_node": "node1"
}
```

插件参数精髓：

| 参数 | 说明 |
|------|------|
| `filename` | file，引用用户文件 |
| `filetype` | 可选；不填则按扩展名推断 |
| `split_mode` | `page`（按页）/ `toc`（按标题） |

回答模型引用 `$.node1.result_key` 作为文件正文上下文。

---

## 5) 图表生成 — 意图分流 + 模型挂载图表工具

```text
意图(Analysis|Chat)
  --Analysis--> 进度交互 --> 数据分析模型(挂 charts tools) → 文件输出
  -----------> 闲聊/非分析模型
```

关键差异：图表能力挂在 **`llm_mixup.tools[]`**（只有 `id`，由模型自主调工具），不是单独 `act_tool` 节点：

```json
{
  "type": "llm_mixup",
  "name": "大模型_数据分析生成图表",
  "tools": [
    { "id": "PLACEHOLDER_CHART_TOOL_1" },
    { "id": "PLACEHOLDER_CHART_TOOL_2" }
  ],
  "max_reflections": 1,
  "on_error": 1
}
```

提示词强制柱状图数据结构：

- x：分钟标签数组长度 N  
- labels：长度 M  
- y：**二维数组** M×N，例如 `[[a,b,c,...]]`

根输出：

```json
"output_parameters": [
  { "name": "f", "type": "file", "value": { "type": "reference", "expr": "$.node1.file" } }
]
```

```gotemplate
{{if eq $.node2.intent "Analysis"}}
  {{if eq $.node1.file ""}}{{$.node1.result_key}}{{else}}...{{$.node1.file}}{{end}}
{{else}}{{$.node4.result_key}}{{end}}
```

要点：**凡要下载的文件，必须在根 `output_parameters` 声明 file 类型引用**（官方 prologue 硬性提醒）。

---

## 6) 表格生成 — guided 数组 → 表格插件 → 文件

```text
意图(Analysis|Chat)
  --Analysis--> 进度交互 --> 数据提取(guided array) --> 表格插件(text=$.node1.result_key)
  -----------> 非提取模型
```

提取模型：

```json
{
  "guided_json_expr": { "type": "literal", "expr": "{\"type\":\"array\"}" },
  "system_prompt_template": "...输出 array<obj>，如[{\"ip\":\"1.1.1.1\"}]，不要多余内容"
}
```

表格插件：

```json
{
  "type": "act_tool",
  "tools": [
    {
      "id": "PLACEHOLDER_TABLE_TOOL",
      "options": [
        {
          "name": "text",
          "type": "string",
          "required": true,
          "value": { "type": "reference", "expr": "$.node1.result_key" }
        }
      ]
    }
  ]
}
```

根文件引用示例：`$.node3.file`；`output_expression` 用 `intent` 分支决定展示下载链接还是纯文本回答。

---

## 插件接线通用原则

1. **ID**：只来自 `fetch_platform_catalog.py` 的 `tool_id` / 知识库 label。  
2. **文件门禁**：用户 file 输入 → 边条件用 `{name}_attribute.Extension`。  
3. **文件下载**：根 `output_parameters` 必须有 file 引用。  
4. **两种挂载方式**：  
   - 确定性一步调用 → `act_tool` 节点  
   - 模型自主选图/多工具 → `llm_mixup.tools[]`  
5. **知识库**：`labels` + `associate`；FAQ 与原文工具不同，勿混参。

## 生成时检查清单

- [ ] 插件 / 知识库 ID 非示例硬编码 UUID  
- [ ] 文件条件使用 `*_attribute.Extension`  
- [ ] 代码回环：`private_envs` + 自定义 `result_key` + 边条件字段名一致  
- [ ] 图表/表格：根 file 输出已声明  
- [ ] 意图分流均配 guided（推荐）  
