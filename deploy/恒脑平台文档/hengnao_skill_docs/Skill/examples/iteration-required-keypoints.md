# 关键片段：`required` 只用于开始节点 + 代码迭代参数不重复声明

> 来源：实测脚本修复（2026-08-24）；2026-09-07 正式环境补强「同名覆盖 / 输出包装」  
> 目标：避免中间节点 `required` 误用和代码迭代参数重复声明导致的逻辑异常。

## 规则 1：`required` 仅用于根 `input_parameters`

- 开始节点（根）`input_parameters` 可配置 `required`。
- **所有中间节点**（`nodes[*].input_parameters`）不要配置 `required`，否则可能出现执行/路由异常。
- 插件 `tools[].options[].required` 属于插件入参定义，不在本规则限制范围内。

错误示例（中间节点误加 `required`）：

```json
{
  "name": "item",
  "type": "string",
  "required": true,
  "value": { "type": "reference", "expr": "$.item" }
}
```

正确做法（删除 `required`）：

```json
{
  "name": "item",
  "type": "string",
  "value": { "type": "reference", "expr": "$.item" }
}
```

## 规则 2：代码节点开启迭代后，不要重复声明迭代参数

当 `act_interpreter` 开启迭代：

```json
"iteration": true,
"iteration_values": [{
  "list_expr": { "type": "reference", "expr": "$.node4.ArrayB" },
  "dest_name": "IterA"
}]
```

代码里可以直接使用：

```python
def handler(args: Args):
    current = args.input.IterA
```

**不要**再在该代码节点的 `input_parameters` 里重复声明 `IterA`：

```json
{
  "name": "IterA",
  "type": "string",
  "value": { "type": "reference", "expr": "$.IterA" }
}
```

这类重复声明容易让生成逻辑混乱，也增加配置出错概率。

**更严重的实测后果（2026-09-07）**：同名普通输入会**覆盖**逐轮 `iteration_values` 绑定，handler **一次收到整个数组**，表面像「迭代成功」但报告只有 1 轮。错误形态：

```json
"input_parameters": [
  {"name": "current_batch", "value": {"type": "reference", "expr": "$.node_prepare.batches"}}
],
"iteration_values": [
  {"list_expr": {"type": "reference", "expr": "$.node_prepare.batches"}, "dest_name": "current_batch"}
]
```

正确：`input_parameters` 为空（或不含同名），只保留 `iteration_values`；代码读 `args.input.current_batch`。若不是当前轮对象，应抛绑定错误，不要静默处理。

## 迭代输出形状防御（下游）

平台映射后，同一份逻辑结果可能出现：按轮数组、数组的数组、一元素再包装、全部轮次聚合成单一外层；标量也可能变成 `[标量]`。

建议：

1. 每个业务 item 携带 `round_index`（轮次统计来自 item，不用外层 wrapper 长度）
2. 下游递归收集含业务键的字典，再做断言
3. 数字比较前 `isinstance(value, int)`，可安全解包单元素列表
4. 空输入不要用 `all([])` 当成功（空集为真）；至少校验非空、数量、轮次、顺序、状态、重复

结束节点模板**不要**写 `{{json ...}}`（`function "json" not defined`）；用 `{{$.nodeX.field}}` 或 `dig`。

## 最小正确骨架（代码迭代）

```json
{
  "type": "act_interpreter",
  "iteration": true,
  "iteration_values": [{
    "list_expr": { "type": "reference", "expr": "$.node4.IntArray" },
    "dest_name": "IntOnce4Code"
  }],
  "input_parameters": [],
  "interpreter": {
    "lang": "python",
    "code": "from runtime import Args\n\ndef handler(args: Args) -> dict:\n    return {\"outPut\": args.input.IntOnce4Code}\n"
  }
}
```

## 自检

- [ ] 根 `input_parameters` 允许 `required`；`nodes[*].input_parameters` 不含 `required`
- [ ] 代码节点 `iteration_values[].dest_name` 不在同节点 `input_parameters[].name` 中重复出现
- [ ] 代码里使用 `args.input.<dest_name>`；收到整表而非单轮对象时判失败
- [ ] 下游对迭代输出做包装防御；轮次看 item 内 `round_index`
- [ ] `output_expression` 不用未验证模板函数（尤其 `json`）
- [ ] 运行 `scripts/validate_agent_json.py` 通过
