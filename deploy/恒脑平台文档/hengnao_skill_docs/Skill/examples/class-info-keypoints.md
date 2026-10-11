# 样例关键片段：交互班级信息收集

> 来源：`Prompts/SkillGeneratePrompt.md`（已提炼，非完整脚本）

## 目标

通过 `act_interaction` 连续追问，直到班级信息完整（`Completed=1`）。

## 最小拓扑

```text
模型提取(node1) -> 交互提问(node2) -> 代码累加历史(node3) -> 再次提取(node5)
node5 条件边 Completed==0 回到 node2
```

## 关键节点结构

### 1) 交互节点（核心）

```json
{
  "id": "node2",
  "type": "act_interaction",
  "questions": [
    {
      "name": "GetInfoQ",
      "type": "type_input",
      "message": { "type": "reference", "expr": "$.NextQ" },
      "timeout": 60
    }
  ],
  "interactions": [
    {
      "from": "MsgFeedback",
      "stream": true,
      "output_expression": { "type": "reference", "expr": "$.node5.FinalInfo" }
    }
  ]
}
```

### 2) 回环条件边

```json
{
  "condition": "Completed == 0",
  "parallel": false,
  "src_node": "node5",
  "dst_node": "node2"
}
```

### 3) 历史拼接代码（核心逻辑）

```python
HistoryAnswer = HistoryAnswer + "\\n" + LatestAnswer
return {"HistoryAnswer": HistoryAnswer}
```

## 可复用点

- 追问循环：`Completed` + 条件边回路
- 交互输入读取：`$.node2.GetInfoQ._answer`
- 交互输出回显：`interactions[].output_expression`
