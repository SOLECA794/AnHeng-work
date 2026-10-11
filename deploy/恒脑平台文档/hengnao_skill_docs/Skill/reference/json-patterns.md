# JSON 脚本模式与顶层结构

> ⚠️ 本节描述的是**逻辑/语义结构**。平台导入器对**字段完整度**另有硬性要求
> （节点必须带全套标准字段、`model` 必须是对象、根需 `id/type/model`、描述长度 8–1024 等）。
> 生成后请用 `scripts/validate_agent_json.py` 校验，完整规则见
> [import-compatibility.md](import-compatibility.md)。

## 最小可运行骨架（实测可导入版）

```json
{
  "cot_version": "v2",
  "eng_version": "v2.3.8",
  "name": "智能体名称",
  "description": "请填写至少八个字符的业务描述，说明这个智能体做什么。",
  "prologue": "请描述本智能体的开场白，至少八个字符。",
  "entry": "node0",
  "uid": "",
  "org": "",
  "id": "在此填写一个真实UUID",
  "type": 0,
  "model": { "model": "HengNao-v4", "temperature": 0, "top_p": 1, "max_tokens": 1024, "top_k": -1, "reasoning": 1 },
  "session_round": 5,
  "input_parameters": [
    { "name": "input", "type": "string", "description": "用户输入文本内容说明" }
  ],
  "output_parameters": [],
  "output_expression": {
    "type": "rendering",
    "expr": "{{$.node0.result_key}}"
  },
  "private_envs": [],
  "public_envs": [],
  "nodes": [],
  "edges": []
}
```

> 节点不是「最小」即可，而是必须带**全套标准字段**（见 import-compatibility.md 规则 1 的模板）。
> 上面骨架里 `nodes:[]` 仅占位，真实节点请照该模板补全。

## 参数 type 与 default_value

`default_value` 必须是对应 JSON **原生类型**，不能写成字符串：

```json
{ "name": "items", "type": "array", "required": true, "default_value": ["alpha", "beta", "gamma"] }
```

错误（会报 `must be of array type`）：`"default_value": "[\"alpha\",\"beta\",\"gamma\"]"`。  
各类型对照见 [param-type-default-keypoints.md](../examples/param-type-default-keypoints.md)。

## output_parameters 文件下载

```json
{
  "name": "reportFile",
  "description": "报告文件",
  "type": "file",
  "value": {
    "type": "reference",
    "expr": "$.node8.annotatedDoc"
  }
}
```

## output_parameters 从 result_key 解析

**硬规则**：列出的每个输出参数都必须有完整 `value`。禁止只有 `name`/`type`/`description`（事故：下游 `{{$.nodeX.field}}` 为空）。

```json
{
  "name": "chunks",
  "type": "array",
  "value": {
    "type": "jsonpath",
    "expr": "$.chunks",
    "text": {
      "type": "reference",
      "expr": "$.node1.result_key"
    }
  }
}
```

代码节点 handler `return {"result": "...", "image_name": "..."}` 时，对本节点内解析可写 `text.expr: "$.result_key"`，`jsonpath.expr` 与 return 的 key 对齐。见 [output-parameters-value-keypoints.md](../examples/output-parameters-value-keypoints.md)。

**迭代节点额外规则**：`iteration: true` 时不要写 `$.[*].字段`，应写 `$.字段` / `$.字段[*]`（如 `$.processed_items[*]`）。见 [iteration-jsonpath-keypoints.md](../examples/iteration-jsonpath-keypoints.md)。

## 模型引导输出（guided_*_expr）

下游需从 `result_key` 用 jsonpath 解析时，**必须**在 `model` 内配置 guided：

```json
{
  "type": "llm_mixup",
  "result_key": "result_key",
  "system_prompt_template": "## 角色定位\n...\n## 输出约束条件\n1. 字段与 Schema 一致\n...",
  "user_prompt_template": "【用户需求】\n{{$.kv_user.input}}\n\n【硬性要求】\n1. ...",
  "model": {
    "model": "HengNao-v4",
    "temperature": 0,
    "guided_json_expr": {
      "type": "literal",
      "expr": "{\n  \"type\": \"object\",\n  \"required\": [\"agentId\", \"name\"],\n  \"properties\": {\n    \"agentId\": { \"type\": \"string\" },\n    \"name\": { \"type\": \"string\" }\n  }\n}"
    }
  },
  "output_parameters": [
    {
      "name": "agentId",
      "type": "string",
      "value": {
        "type": "jsonpath",
        "expr": "$.agentId",
        "text": { "type": "reference", "expr": "$.node12.result_key" }
      }
    }
  ]
}
```

| 字段 | expr 内容 |
|------|-----------|
| `guided_json_expr` | JSON Schema（`required` 非空） |
| `guided_choice_expr` | JSON 数组，如 `["选项A","选项B"]` |
| `guided_regex_expr` | 正则（禁 `^` `$` `*`；`{m,n}` 中 n≤50） |

完整说明：[guided-output.md](guided-output.md)。

## 插件 options 项

```json
{
  "name": "filename",
  "type": "file",
  "required": true,
  "value": { "type": "reference", "expr": "$.kv_user.inspectedDoc" }
}
```

## 知识库检索 options（FAQ / 原文召回）

知识库问答类插件优先用 `labels` 绑定检索范围：

```json
{
  "name": "labels",
  "type": "array",
  "required": false,
  "value": {
    "type": "associate",
    "expr": "[\"<知识库标签或平台要求的库标识>\"]"
  }
}
```

`permissions` 不是默认的知识库绑定字段；没有明确平台要求时不要用租户 id 或库 id 乱填。

## 开始节点后的总线模式

若脚本需要从入口并行分支，不要让开始节点直接连多条边。推荐：

```text
entry -> 空 act_interaction 节点(node_bus)
node_bus --parallel--> node_faq
node_bus --parallel--> node_doc
node_bus -----------> node_llm
```

这样 `node_llm` 会在并行分支全部结束后再执行。

## 边条件（Expr vs `$`）

`edges[].condition` 先 Go 模板、后 Expr。**纯 Expr 不要写 `$.`**：

```json
{"condition": "len(node2.results) > 0", "src_node": "node2", "dst_node": "node4"}
{"condition": "len(kv_user.tempComplianceReqDoc) > 0", "parallel": true, "src_node": "node3", "dst_node": "node22"}
```

错误：`"condition": "len($.node2.results) > 0"`

Go 模板条件（仅 `{{}}` 内用 `$`）：

```json
{
  "condition": "{{if hasKey $.node19.deleteKnowledge._answer \"A\"}}true{{else}}false{{end}}",
  "src_node": "node19",
  "dst_node": "node20"
}
```

节点 `value.expr` 的 reference 仍用 `$.`（与边条件不同）：

```json
{"type": "reference", "expr": "$.kv_user.input"}
```

## rendering 兜底（`dig`）

存在条件边 / 并行 n 选 1 时，末节点不一定执行。根或交互 `output_expression` 直接写 `{{$.node11.result}}` 可能报错；对可选上游字段用 **`dig`**：

```json
"output_expression": {
  "type": "rendering",
  "expr": "运行结果：{{dig \"result\" \"\" $.node11}}"
}
```

详见 [rendering-dig-fallback-keypoints.md](../examples/rendering-dig-fallback-keypoints.md)。

## 迭代节点

```json
{
  "iteration": true,
  "iteration_times": 0,
  "iteration_values": [{
    "list_expr": { "type": "reference", "expr": "$.kv_user.complianceReqDocs" },
    "dest_name": "complianceReqDoc"
  }]
}
```

代码节点迭代补充：

- `iteration_values[].dest_name` 定义后，代码里直接用 `args.input.<dest_name>`。
- 不要在同一代码节点 `input_parameters` 重复声明该 `dest_name`。
- 中间节点 `input_parameters` 不要配 `required`（`required` 仅用于根 `input_parameters`）。
- **抽迭代结果字段**：`output_parameters` 的 jsonpath 写 `$.processed_items[*]`，**不要**写 `$.[*].processed_items[*]`（后者实测为空）。见 [iteration-jsonpath-keypoints.md](../examples/iteration-jsonpath-keypoints.md)。

`iteration_times` 规则（已确认）：

- `0`：按迭代数组长度执行
- 正整数：最多执行该次数
- 若数组元素数量小于该次数：提前结束

`max_working`：节点最大通过次数（循环保护），默认 `30`。

## 错误与重试

```json
{
  "on_error": 0,
  "max_retries": 3,
  "on_error_setting": { "max_retries": 3 }
}
```

`on_error` 取值（已确认）：

- `0`：失败后返回（默认）
- `1`：失败后继续（可在 `on_error_setting.err_parameters` 配默认返回值）
- `2`：失败后重试（`on_error_setting.max_retries`）

失败后继续示例：

```json
{
  "on_error": 1,
  "on_error_setting": {
    "err_parameters": [
      {
        "name": "result_key",
        "type": "string",
        "value": { "type": "literal" }
      }
    ]
  }
}
```

失败后重试示例：

```json
{
  "on_error": 2,
  "on_error_setting": {
    "err_parameters": [],
    "max_retries": 3
  }
}
```

## 占位符与运行时目录（已确认）

**优先**用 `scripts/fetch_platform_catalog.py` 按当前库 + 用户 ID 拉取可访问资源，把真实 `tool_id` / `skill_id` / `server_id` / 知识库 id 写进脚本。说明见 [platform-catalog.md](platform-catalog.md)。

仅当没有数据库连接时，租户与插件 ID 才用空串或占位符：

```
""                          # 无 catalog 时 uid/org 可留空
PLACEHOLDER_PLUGIN_DOC_PARSE
PLACEHOLDER_SKILL_EMAIL
```

设计说明必须列出「已绑定的 catalog 资源」和「仍待填项」。**禁止**编造看似真实的 UUID。

## 版本字段策略（已确认）

版本相关字段按平台**最新可用版本**设置即可，不必强制锁定旧版本：

- `eng_version`：当前最新为 **`v2.3.8`**（生成时默认填此值）
- `cot_version`：使用当前平台最新（最新样例常见 **`v2`**）
- **禁止**把引擎版本（如 `v2.3.8`）写入 `cot_version`，否则导入/运行报 `the agent CoT version is invalid`
- `session_round`：正整数（常用 `10`）；不要用 `null`/字符串
- 实测 `v1.7.10` / `v2.2.5` / `v2.3.8` 均可导入，版本头不是卡点；节点字段完整度才是。
