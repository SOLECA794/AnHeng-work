# 关键片段：迭代节点 jsonpath 用 `$.字段`，不要用 `$.[*].字段`

> 实测（2026-08-24）：迭代代码节点 `return {"processed_items":[...]}` 后，  
> `result_key` 是**每轮返回值的 JSON 字符串数组**。  
> 用 `$.[*].processed_items[*]` 抽字段 → **空数组**；改成 `$.processed_items[*]` 才能抽到。

## 现象

迭代后节点状态大致是：

```json
{
  "result_key": [
    "{\"processed_items\":[{\"source\":\"alpha\",...}]}",
    "{\"processed_items\":[{\"source\":\"beta\",...}]}"
  ],
  "processed_items": []
}
```

`output_parameters` 里若写：

```json
{
  "name": "processed_items",
  "type": "array",
  "value": {
    "type": "jsonpath",
    "expr": "$.[*].processed_items[*]",
    "text": { "type": "reference", "expr": "$.result_key" }
  }
}
```

则 `processed_items` 仍为空。

## 正确写法

对**每轮 return 的字段名**直接写路径（平台会按轮解析字符串再收集）：

```json
{
  "name": "processed_items",
  "type": "array",
  "value": {
    "type": "jsonpath",
    "expr": "$.processed_items[*]",
    "text": { "type": "reference", "expr": "$.result_key" }
  }
}
```

也可写 `$.processed_items`（视字段是标量还是数组而定）。

| 场景 | jsonpath `expr` | 结果 |
|------|-----------------|------|
| 迭代抽 return 字段 | `$.processed_items[*]` | ✅ 按轮抽出 |
| 迭代抽 return 字段 | `$.[*].processed_items[*]` | ❌ 空 |
| 非迭代、整段已是对象数组 | `$.[*].id` | ✅（另一类用法，见 guided 数组输出） |

## 下游注意

`$.processed_items[*]` 常得到「数组的数组」（每轮一份）：

```json
[
  [{ "source": "alpha", ... }],
  [{ "source": "beta", ... }]
]
```

汇总节点若读 `$.node_iter.result_key`，需自行 `json.loads` 每轮字符串再扁平化；若读 `$.node_iter.processed_items`，则按二维数组处理或再 flatten。

## 硬规则

1. **`iteration: true` 的节点**：从 `result_key` 拆字段时，jsonpath **禁止**以 `$.[*].` 开头去点字段。
2. 正确形态：`$.<return字段>` 或 `$.<return字段>[*]`，字段名与 `handler` return 的 key 一致。
3. 不要把「整段数组对象」的 `$.[*].id` 套用到「迭代多轮字符串结果」场景。

## 生成自检

- [ ] 迭代节点 `output_parameters` 的 jsonpath 无 `$.[*].xxx`
- [ ] jsonpath 字段名 = 代码 `return` 的 key
- [ ] 下游消费时考虑「按轮数组 / 字符串数组」形态
- [ ] 跑过 `scripts/validate_agent_json.py`
