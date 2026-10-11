# 导入兼容性硬规则（实测，2026-08-16）

> ⚠️ **最重要的一节，先于其他文档阅读。**
> 本 skill 其他文档（如 `json-patterns.md` 的「最小骨架」）描述的是**逻辑/语义层面**的结构，
> 但平台导入器对**字段完整度**有硬性要求。按旧「精简 v2 风格」生成的脚本会**静默失败或报无详情错误**。
> 本节记录经过**增量测试（T1~T5 + 类型能力测试）验证**的真实可导入规则。

## 一句话结论

- 版本头：`eng_version` 填引擎版（当前最新 **`v2.3.8`**）；`cot_version` 填配置版（常用 **`v2`**）。二者职责不同——**不要**把 `v2.3.8` 写进 `cot_version`（会 `CoT version is invalid`）。引擎侧 v1.7.10 / v2.2.5 / v2.3.8 均实测可导入。
- 节点类型 `act_interpreter` / `act_tool` / `llm_mixup` / `act_interaction` 均**被支持**（实测通过）。
- **真正的硬要求**：节点必须带**全套标准字段**，`model` 必须是**对象**，根节点要有 `id/type/model`，
  且 `description` / `prologue` 长度在 **8–1024 字符**之间。

## 规则 1：节点必须带全套标准字段（最关键）

任何一个节点都至少包含以下字段（缺任何一个都可能导入失败）：

```
max_retries, input_parameters, iteration_values, description, max_working,
type, tools, mcps, timeout, use_memory, agents, log_memory, result_key,
output_parameters, max_reflections, name, iteration, on_error, id,
no_action, no_repeat
```

- `act_interpreter` 额外需要 `interpreter:{lang,code}`。
- `act_tool` 额外需要 `tools:[{id,options}]`，且**不应带 `model` / `user_prompt_template`**（与模型节点区分）。
- `llm_mixup` 需要 `model`（对象）与 `system_prompt_template` / `user_prompt_template`。

✅ **可导入的「最小完整节点」模板**（直接照抄，只改内容）：

```json
{
  "id": "node0",
  "name": "模型节点",
  "description": "一句话说明本节点职责，至少八个字符",
  "type": "llm_mixup",
  "input_parameters": [
    { "name": "input", "type": "string", "value": { "type": "reference", "expr": "$.kv_user.input" } }
  ],
  "output_parameters": [],
  "result_key": "result_key",
  "model": { "top_p": 1, "max_tokens": 1024, "reasoning": 1, "top_k": -1, "temperature": 0, "model": "HengNao-v4" },
  "system_prompt_template": "你是……请只输出……",
  "user_prompt_template": "用户输入：{{$.kv_user.input}}",
  "tools": [],
  "mcps": [],
  "timeout": 300,
  "max_retries": 3,
  "max_working": 30,
  "max_reflections": 5,
  "use_memory": false,
  "log_memory": false,
  "agents": [],
  "iteration": false,
  "iteration_values": [],
  "on_error": 0,
  "no_action": false,
  "no_repeat": false
}
```

## 规则 2：`model` 必须是对象，不是字符串

❌ 错误：`"model": "HengNao-v4"`
✅ 正确：`"model": { "model": "HengNao-v4", "temperature": 0, "top_p": 1, "max_tokens": 1024, "top_k": -1, "reasoning": 1 }`

（根节点 `model` 同样用对象。仅 `act_tool` 节点不带 `model`。）

## 规则 3：根节点必须有 `id` / `type` / `model`

```json
"id": "这里填一个真实 UUID",
"type": 0,
"model": { "model": "HengNao-v4", "temperature": 0, "top_p": 1, "max_tokens": 1024, "top_k": -1, "reasoning": 1 }
```

`uid` / `org` 可留空字符串（合规），但 `id` 建议填 UUID（`type:0` 表示普通智能体）。

## 规则 4：`description` / `prologue` 长度 8–1024

平台会校验 `描述长度要求8-1024字符!`——
- 根 `description`、`prologue`、每个节点的 `description` 应 **≥ 8 字符**。
- ⚠️ **节点 `input_parameters[].description` 可空**（实测最终可导入版为空仍成功导入），不强制长度。
- ⚠️ 平台只报**第一个**违规项，所以根/节点 `description`、`prologue` 务必一次性写够长度（宁可长不要短）。
- 空 `prologue:""` 在某些部署会触发同一条规则，建议填一句 8 字以上的开场白。

## 规则 5：工具 / 租户 ID — catalog 真实 ID 或占位符，禁止假 UUID

- **有运行时 catalog**（`scripts/fetch_platform_catalog.py`）：`tools[].id` 必须是 catalog 里的 `tool_id`；Skill / MCP 同理。用 `validate_agent_json.py --catalog catalog.json` 核对。
- **无 catalog**：`tools[].id` 用 `""` 或 `PLACEHOLDER_PLUGIN_<用途>`（如 `PLACEHOLDER_PLUGIN_SECURITY_SCAN`）。
- **禁止编造看似真实的 UUID**——平台会去查，查不到就失败。也不要把别的环境的 ID 抄进来。
- 占位符只用于「当前环境目录里确实没有的能力」；有目录时应最大化绑定真实资源。详见 [platform-catalog.md](platform-catalog.md)。

## 规则 6：避免重复键（JSON 静态错误）

同一对象内**不能出现两个同名键**。曾出现过的坑：迭代节点顶部写了 `iteration/iteration_values`，
末尾又补一遍 `iteration:false / iteration_values:[]`，导致：
- 标准解析器静默保留后者（迭代配置被清空）；
- 严格导入器直接抛「解析失败」且**无详情**。

生成后务必用校验脚本（`scripts/validate_agent_json.py` 或 `scripts/validate_agent_json.py`）跑一遍。

## 规则 7：先验证、再交付

生成完 JSON 后，**必须**用 `scripts/validate_agent_json.py <file>.json` 自检（有目录时加 `--catalog catalog.json`），
全部 PASS 再交付用户导入。脚本会逐项检查上述规则并给出具体违规位置。

## 规则 8：代码节点入参必须 `args.input.X`

`act_interpreter` 中读取节点 `input_parameters` 时只能写 **`args.input.<参数名>`**。  
写成 `args.image` / `args.query` 会在运行时报 `AttributeError: 'Args' object has no attribute '...'`。  
校验器会拦截该错误写法；说明见 [examples/code-node-args-input-keypoints.md](../examples/code-node-args-input-keypoints.md)。

## 规则 9：输出参数必须配完整 `value`

节点或根的 `output_parameters[]` 只要声明了字段，就必须有完整 **`value`**（UI：输入 literal / 引用 reference / 渲染 rendering；从 result_key 拆字段用 jsonpath + `text.reference`）。  
只写 `name`/`type`/`description` 会导致下游 `{{$.nodeX.field}}` 为空。  
见 [examples/output-parameters-value-keypoints.md](../examples/output-parameters-value-keypoints.md)；校验器会拦截缺 `value` 的项。

## 规则 10：`required` 只放开始节点 input_parameters

根（开始节点）`input_parameters` 可配置 `required`。  
`nodes[*].input_parameters`（中间节点）不应出现 `required`，否则可能触发逻辑错误。  
注意：插件参数 `tools[].options[].required` 不在此限制内。

## 规则 11：代码迭代参数不要在 input_parameters 重复声明

`act_interpreter` 开启迭代时，`iteration_values[].dest_name` 已注入 `args.input`。
不要在同一节点 `input_parameters` 再声明同名参数，直接在代码中使用 `args.input.<dest_name>`。
同名普通输入会覆盖逐轮绑定，导致一次 handler 处理整个数组。

结束节点 / `output_expression` 模板没有 `json` 函数；用直接字段引用或 `dig`。
见 [examples/iteration-required-keypoints.md](../examples/iteration-required-keypoints.md)；校验器会拦截重复声明。

## 规则 12：参数 type 与 default_value 必须原生对齐

根 `input_parameters` / 环境变量只要声明了 `type` 并给了 `default_value`，JSON 值必须匹配该类型：

- `array` → `["alpha","beta"]`，禁止 `"[\"alpha\",\"beta\"]"`
- `integer` → `10`，禁止 `"10"`
- `boolean` → `true`，禁止 `"true"`
- `object` → `{...}`，禁止 `"{...}"`
- `file` → `"file:名称.ext<uuid>"`

实测（2026-08-24）：array 默认值写成字符串会报 `the value for the parameter must be of array type: items`。  
见 [examples/param-type-default-keypoints.md](../examples/param-type-default-keypoints.md)；校验器会拦截。

## 规则 13：迭代节点 jsonpath 不要用 `$.[*].字段`

`iteration: true` 时，`result_key` 是每轮返回的 JSON **字符串**数组。  
从中拆字段应写 **`$.字段` / `$.字段[*]`**（如 `$.processed_items[*]`）。  

写成 `$.[*].processed_items[*]` 会得到空数组（2026-08-24 实测）。  
见 [examples/iteration-jsonpath-keypoints.md](../examples/iteration-jsonpath-keypoints.md)；校验器会拦截。

## 规则 14：条件分支后 rendering 建议 `dig` 兜底

存在**条件边**或**并行 n 选 1**（并非所有路径都会汇合到同一节点）时，根 `output_expression` 及交互 `interactions[].output_expression` 若直接引用 `{{$.nodeX.field}}`，当该节点未执行或字段缺失会**运行报错**。

对可能不存在的前序节点字段，rendering 模板内改用 **`dig`**：

```json
"output_expression": {
  "type": "rendering",
  "expr": "运行结果：{{dig \"result\" \"\" $.node11}}"
}
```

`dig` 第二参数为默认值（`""` 或用户可读文案）。单线串行、节点必定执行时可继续直接 `{{$.nodeX.field}}`。  
见 [examples/rendering-dig-fallback-keypoints.md](../examples/rendering-dig-fallback-keypoints.md)；校验器在存在条件边时会给出 **advisory note**（非强制，不阻断 PASS）。

## 规则 15：图/边拓扑硬规则

校验器会拦截（来自 [docs/04-edges.md](../docs/04-edges.md)）：

| 规则 | 违规表现 |
|------|----------|
| 节点 id 唯一 | 两个节点同 `id` |
| 每源仅一条普通边 | 同一 `src_node` 有 ≥2 条无 condition、非 parallel 的边 |
| 开始节点不直接并行分支 | `entry` 有 `parallel` 出边但 type ≠ `act_interaction` |
| 并行须有同级汇总边 | 某源有 `parallel: true` 边，但没有普通边 |
| 无死节点 | 从 `entry` 沿边不可达的节点 |

`dig` 兜底仍仅为建议，不因缺 `dig` 判 FAIL。

## 增量排查法（当仍失败时）

从一个**已知可导入的最小单元**起步，逐版叠加特性，第一个失败的就是元凶：

1. 单 `llm_mixup` 节点（全字段 + v2 头）→ 验证「格式本身能进」
2. + `act_interpreter` 代码节点 → 验证代码节点
3. + `act_tool` 插件节点（占位 id）→ 验证插件节点
4. 插件节点 + 迭代 → 验证迭代
5. 完整流程 → 验证细节字段（on_error_setting / 嵌套 jsonpath / guided_json_expr）

**关键诊断技巧**：UI 只显示「失败」时，按 F12 → Network 找到那条导入请求 → 看 **Response** 里的
`message` / `error` / `detail` 原文，往往能直接定位。

## 实测记录（本平台）

| 测试 | 头部 | 节点结构 | 结果 |
|------|------|----------|------|
| T1（精简 v2 节点） | v2 | 缺全套字段 + model 字符串 | ❌ 失败（描述长度 / 字段缺失） |
| T1_v1built | v1 | Demo 全字段 + model 对象 | ✅ 成功 |
| T_code_v1 | v1 | 单 `act_interpreter` | ✅ 成功 |
| T_tool_v1 | v1 | 单 `act_tool`（占位 id） | ✅ 成功（占位 id 查不到属预期，换真实 id 即可） |
| 最终 5 节点完整版 | v2 | 全字段 + model 对象 | ✅ 成功 |
