# 交互节点 `act_interaction` JSON 结构

> 已确认：参考「班级信息收集」智能体 node2（见 [class-info-keypoints.md](../examples/class-info-keypoints.md)）

## 两种能力

| 能力 | JSON 字段 | 用途 |
|------|-----------|------|
| **用户问答** | `questions[]` | 填空 / 单选 / 多选，引导用户输入 |
| **消息反馈** | `interactions[]` | 对外输出状态、进度、流式内容 |

可单独使用或组合（如：questions 提问 → 用户回答写入 `result_key` → interactions 反馈进度）。

## 问答配置 `questions`

```json
{
  "type": "act_interaction",
  "questions": [
    {
      "name": "GetInfoQ",
      "type": "type_input",
      "options": [],
      "message": {
        "type": "reference",
        "expr": "$.NextQ"
      },
      "timeout": 60
    }
  ]
}
```

### `questions[].type` 取值

| type | 含义 |
|------|------|
| `type_input` | 填空（自由输入） |
| `type_select` | 单选（`options` 非空；每项含 `name` + `value`） |

用户回答通过 **`$.node2.<question.name>._answer`** 引用，例如 `$.node2.GetInfoQ._answer`。

官方「人机交互」示例确认的授权单选形态：

```json
{
  "name": "check",
  "type": "type_select",
  "options": [
    { "name": "A", "value": { "type": "literal", "expr": "确认封禁" } },
    { "name": "B", "value": { "type": "literal", "expr": "取消封禁" } }
  ],
  "message": { "type": "literal", "expr": "参数校验通过，请确认是否封禁？" },
  "timeout": 30
}
```

## 消息反馈 `interactions`

```json
{
  "interactions": [
    {
      "from": "MsgFeedback",
      "stream": true,
      "output_expression": {
        "type": "reference",
        "expr": "$.node5.FinalInfo"
      },
      "output_parameters": [
        {
          "name": "yONmWm",
          "value": { "type": "reference", "expr": "$.node5.Completed" }
        }
      ]
    }
  ]
}
```

- `from`：消息通道标识。官方示例常用 `"msg"` / `"msg1"` / `"msg2"`；历史样例亦见 `"MsgFeedback"`。并行支路结束通知可用不同 `from` 区分。
- `stream: true`：流式输出
- `output_expression` / `output_parameters`：对外暴露的渲染内容与参数（`rendering` / `literal` / `reference`）

主动播报（无提问）最小形态见 [official-demos-orchestration-keypoints.md](../examples/official-demos-orchestration-keypoints.md) §5。

## 通用字段

```json
{
  "id": "node2",
  "name": "提问引导用户输入信息",
  "type": "act_interaction",
  "description": "...",
  "result_key": "result_key",
  "max_retries": 3,
  "timeout": 300,
  "max_working": 30,
  "on_error": 0,
  "iteration": false
}
```

## 典型流程（班级信息收集）

```
node1(模型提取) --Completed==0--> node2(交互提问)
node2 --> node3(代码累加 HistoryAnswer) --> node5(模型再判断)
node5 --Completed==0--> node2（循环直到完成）
```

边条件使用 `Completed == 0`（Expr，引用 node 输出参数）。

## 超时

- `questions[].timeout`：单题超时秒数，默认 **60**
- 节点级 `timeout`：整体节点超时（如 300）

## 已确认

- [x] 单选 `type` = `type_select`（来源：`Prompts/新示例集合/人机交互-使用示例.json`）

## 仍模糊

- [ ] 多选在 JSON 中的确切 `type` 字符串值（平台 UI 导出为准）
