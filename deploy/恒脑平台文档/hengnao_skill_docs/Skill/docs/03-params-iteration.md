# 03 智能体传参及节点迭代

> 来源：恒脑智能体服务构建典型场景分析（智能体传参及节点迭代）-智能体合伙人.pdf  
> 2025.3.20

## 开始节点

### 输入参数 `input_parameters`

| 能力 | 说明 |
|------|------|
| 类型 | `string`、`integer`、`float`、`boolean`、`array`、`object`、`file` |
| 必填/默认值 | 根参数可配 `required`、`default_value`；**`default_value` 必须与 `type` 原生对齐** |
| 引用 | 下游通过 `$.kv_user.<name>` 引用 |

**`default_value` 硬规则（高频事故）**

- `type: array` → `default_value` 必须是 JSON 数组 `["a","b"]`，禁止 `"[\"a\",\"b\"]"` 字符串  
  实测报错：`the value for the parameter must be of array type: items`
- `integer`/`float`/`boolean`/`object`/`file` 同理，禁止用字符串去“描述”该类型
- 见 [param-type-default-keypoints.md](../examples/param-type-default-keypoints.md)

### 环境变量

| 类型 | 字段 | 说明 |
|------|------|------|
| 私有 | `private_envs` | 默认不公开，如全局知识库 ID |
| 公开 | `public_envs` | `hidden: false`，客户现场可改的一次性参数（如第三方 token） |

引用：`$.kv_envs.<name>`

## 各节点传参

**`required` 使用边界（高频坑）**

- 根（开始节点）`input_parameters` 可配置 `required`。
- `nodes[*].input_parameters`（中间节点）**不要**配置 `required`，否则可能引发逻辑错误。
- 插件 `tools[].options[].required` 为插件参数定义，按插件要求填写。

### 模型节点 `llm_mixup`

**输入参数**

- 默认预留 `input`，可自定义
- 赋值方式：**输入**（字面量）/ **引用**（前序节点）/ **渲染**（Go 模板）

**系统提示词 / 用户提示词**

- 支持 Go 模板拼接变量
- 参考：https://gotemplate.io/ ；完整开源库清单见 [04-edges.md](04-edges.md)（含 URI Template / Expr / JSONPath / 正则）

**结构化输出辅助工具**

| 用途 | 开源库 / 链接 |
|------|---------------|
| 正则 | [dlclark/regexp2](https://github.com/dlclark/regexp2) · https://regex101.com/ |
| JSONPath | [oliveagle/jsonpath](https://github.com/oliveagle/jsonpath) · https://jsonpath.com/ |
| Json Schema | https://www.lddgo.net/string/generate-json-schema |
| Go Template | [yosida95/uritemplate/v3](https://github.com/yosida95/uritemplate) · https://gotemplate.io/ |

### 插件节点 `act_tool`

- 输入/输出与**插件定义**保持一致
- 默认 `result_key` 封装 JSON 输出

### 代码节点 `act_interpreter`

- **`interpreter.lang`**：平台也支持 `javascript`，与本 Skill 一致时**默认只生成 `python`**（示例、预装库文档、开放接口范式均按 Python）
- 默认 `input` 可删，自定义参数在代码中：**`args.input.X`**（不是 `args.X`）
- **禁止** `args.image` / `args.query` 这类顶层访问 → 会 `AttributeError: 'Args' object has no attribute '...'`
- 默认 `result_key` 接收 handler 返回值
- **输出参数硬规则**：凡在 `output_parameters` 声明的字段，必须配完整 `value`（输入 literal / 引用 reference / 渲染 rendering / **jsonpath+text**）。禁止只有 `name`+`type`。从 `return` dict 拆字段用 jsonpath，`text` 指向 `$.result_key`。见 [output-parameters-value-keypoints.md](../examples/output-parameters-value-keypoints.md)
- 事故与入参骨架：[code-node-args-input-keypoints.md](../examples/code-node-args-input-keypoints.md)
- **迭代参数规则**：若代码节点开启 `iteration` 并配置 `iteration_values[].dest_name`，不要在 `input_parameters` 再次声明同名参数；直接在代码里用 `args.input.<dest_name>`。**实测**：同名普通输入会覆盖逐轮绑定，导致一次 handler 收到整个数组、报告却只有 1 轮。见 [iteration-required-keypoints.md](../examples/iteration-required-keypoints.md)
- **迭代结果防御**：下游可能收到按轮数组、嵌套列表、一元素包装或单外层聚合；标量（如 `round_index`）也可能变成 `[标量]`。汇总代码先递归收集业务字典，比较前用 `isinstance(..., int)` / 单元素列表解包；轮次统计用 item 内字段，不要用外层 wrapper 长度推断。

### 智能体节点 `act_agent`

- 输入/输出与**被引用智能体**定义一致
- 默认 `result_key` 封装 JSON
- 多子智能体接力时，`agents[].options[].value` 常用 **`rendering`**，把 `{{$.kv_user.input}}` 与 `{{$.nodeN.result_key}}` 拼进下游任务说明（见 [official-demos-orchestration-keypoints.md](../examples/official-demos-orchestration-keypoints.md) §6）
- `agents[].id` 必须来自 catalog `callable_agents[].agent_id`

### 模型节点记忆 / 自定义 result_key

- 多轮对话：节点 `use_memory: true`，并适当提高根 `session_round`
- 代码生成回环等场景：把 `result_key` 改成业务名（如 `tmp_code` / `tmp_result`），与 `private_envs`、边条件字段对齐

### 结束节点

**回答内容 `output_expression`**

- 赋值：输入 / 引用 / 渲染（Go 模板）
- 文件下载：须在 `output_parameters` 声明 `type: "file"`，回答中引用该参数

**输出参数（节点与根共通）**

赋值三件套（与 UI「输入 / 引用 / 渲染」一致）+ 从 `result_key` 拆字段时的 **jsonpath**：

| UI | `value.type` | 要点 |
|----|--------------|------|
| 输入 | `literal` | 固定值 |
| 引用 | `reference` | `$.kv_user.*` / `$.nodeN.*` |
| 渲染 | `rendering` | Go 模板 |
| — | `jsonpath` | 必配 `text.reference → $.result_key` |

**禁止**只写 `name`/`type`/`description` 不配 `value` —— 下游 `$.nodeX.field` 会空。关键片段：[output-parameters-value-keypoints.md](../examples/output-parameters-value-keypoints.md)。

**条件分支兜底**：存在条件边时，rendering 引用可能未执行节点字段建议 `{{dig "field" "" $.nodeX}}`，见 [rendering-dig-fallback-keypoints.md](../examples/rendering-dig-fallback-keypoints.md)。

**模板函数**：结束节点 / `output_expression` **没有** `json` 函数（会报 `function "json" not defined`）。结构化字段直接 `{{$.nodeX.field}}`；缺失字段用 `dig`。只使用已验证函数（如 `dig`、`len`、标准 Go template 内置）。

## 节点迭代

### 机制

- 对前序**数组**循环执行同一节点
- 每次迭代结果存入 `result_key`（**按轮的 JSON 字符串数组**）
- 用 `output_parameters` + jsonpath 抽字段时：**写 `$.字段` / `$.字段[*]`**，禁止 `$.[*].字段`（实测会抽空）
- `iteration_times` 用于限制迭代次数
- 关键片段：[iteration-jsonpath-keypoints.md](../examples/iteration-jsonpath-keypoints.md)

### 配置示例

```json
{
  "iteration": true,
  "iteration_times": 0,
  "iteration_values": [{
    "list_expr": { "type": "reference", "expr": "$.node1.chunks" },
    "dest_name": "chunk"
  }],
  "input_parameters": [{
    "name": "input",
    "value": { "type": "reference", "expr": "$.chunk" }
  }]
}
```

代码节点迭代推荐形态（不重复声明迭代参数）：

```json
{
  "type": "act_interpreter",
  "iteration": true,
  "iteration_values": [{
    "list_expr": { "type": "reference", "expr": "$.node1.chunks" },
    "dest_name": "chunk"
  }],
  "input_parameters": [],
  "interpreter": { "lang": "python", "code": "def handler(args): return {\"chunk\": args.input.chunk}" }
}
```

### `iteration_times` 与 `max_working`（已确认）

| 字段 | 含义 | 推荐 |
|------|------|------|
| `iteration_times` | 当前节点迭代执行次数上限 | `0` 表示按迭代数组长度执行；`>0` 表示最多执行该次数；若数组更短则提前结束 |
| `max_working` | 节点通过（进入）最大次数上限（含循环场景） | 默认 `30`；超过后系统自动终止 |

### 案例

| 案例 | 场景 |
|------|------|
| 迭代演示 | 基础数组循环 |
| 招标参数分析智能体 | 多文件/多条目批处理 |
| 班级信息收集 | 历史数据累加（交互 + 迭代） |

## 并行场景下的引用

当多个节点输出同名参数 `X`：

- `$.X` → 取**最后执行**节点的 X（并行汇总时有用）
- `$.node1.X` → **精确**取 node1 的输出

> **与边条件的区别**：上述 `$` 写法用于节点传参 `value.expr`、提示词 `{{}}` 等 **reference** 场景。`edges[].condition` 的纯 Expr 段应写 `node1.X` / `kv_user.X`，不要写 `$.node1.X`（见 [04-edges.md](04-edges.md)）。

## 使用策略

- ✅ Go 模板处理空键、条件拼接
- ✅ AI 辅助编写代码节点 Python 与 Go 模板
- ✅ 超长文件 / 重复业务 → 迭代
- ✅ 历史累加 → 迭代 + 交互节点问答

## 已确认

- `iteration_times`：迭代次数约束，`0` 按数组长度，正整数按上限执行
- `max_working`：节点最大通过次数，默认 `30`
- 迭代节点从 `result_key` 抽字段：jsonpath 用 `$.字段[*]`，不用 `$.[*].字段`（2026-08-24 实测）
- 同名普通输入与 `iteration_values.dest_name` 冲突会导致一次调用处理整个数组；每个 item 宜携带 `round_index`，下游递归扁平化后再统计（2026-09-07 实测）
- 迭代输出可能出现多层包装 / 标量包装；结束模板禁用 `json` 函数（2026-09-07 实测）

## 待补充

- [ ] 二维结果（`$.processed_items[*]` → 数组的数组）在 UI 展示的更多样例
