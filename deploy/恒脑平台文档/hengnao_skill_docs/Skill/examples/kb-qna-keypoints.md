# 样例关键片段：FAQ + 原文联合检索问答

## 目标

用户输入问题后，同时检索 FAQ 知识库与文档原文知识库，再由模型汇总回答。

## 正确拓扑

```text
开始(entry) -> 空交互总线(node4)
node4 --parallel--> FAQ检索(node1)
node4 --parallel--> 原文召回(node3)
node4 -----------> 模型总结(node2)
```

关键点：

1. **开始节点后不要直接分支**，先放空 `act_interaction` 总线。
2. 并行完成后继续执行，依赖 **同一总线节点** 的普通边汇总。
3. FAQ / 原文召回优先用 `labels` 绑定知识库，不要默认把租户 id 填进 `permissions`。

## 边写法

```json
{"parallel": true, "src_node": "node4", "dst_node": "node1"}
{"parallel": true, "src_node": "node4", "dst_node": "node3"}
{"src_node": "node4", "dst_node": "node2"}
```

## FAQ 检索参数片段

```json
{
  "id": "0dc0b54c-3d12-4261-82e9-0cf1e1915c7e",
  "options": [
    {
      "name": "query",
      "type": "string",
      "value": { "type": "reference", "expr": "$.kv_user.input" },
      "required": true
    },
    {
      "name": "labels",
      "type": "array",
      "value": { "type": "associate", "expr": "[\"<FAQ知识库标签>\"]" },
      "required": false
    }
  ]
}
```

## 原文召回参数片段

```json
{
  "id": "a11aead3-fa72-4983-9f64-d0be72ff92ee",
  "options": [
    {
      "name": "query",
      "type": "string",
      "value": { "type": "reference", "expr": "$.kv_user.input" },
      "required": true
    },
    {
      "name": "labels",
      "type": "array",
      "value": { "type": "associate", "expr": "[\"<文档知识库标签>\"]" },
      "required": false
    }
  ]
}
```

## 模型节点取值

模型节点直接读取并行分支的命名结果：

```json
{
  "name": "faqResult",
  "type": "string",
  "value": { "type": "reference", "expr": "$.node1.result_key" }
}
```

```json
{
  "name": "documentResult",
  "type": "string",
  "value": { "type": "reference", "expr": "$.node3.result_key" }
}
```
