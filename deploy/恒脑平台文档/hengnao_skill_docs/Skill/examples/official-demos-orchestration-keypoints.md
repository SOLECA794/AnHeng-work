# 官方新示例精髓：编排能力（并行 / 条件 / 多轮 / 规范输出 / 人机交互 / 智能体协作）

> 来源：`Prompts/新示例集合/`（2026-08）  
> 只保留可复用拓扑与字段模式；**禁止**把示例里的 `uid` / `org` / `agents[].id` / 插件 UUID 原样抄进新脚本，有 catalog 用真实 ID，否则 `PLACEHOLDER_*`。

## 总览

| 示例 | 核心能力 | 典型拓扑 |
|------|----------|----------|
| 并发执行 | 无依赖任务并行 + 总线汇总 | 空交互总线 → 并行 fan-out → 各支路结束通知 → 普通边汇总模型 |
| 条件判断 | 意图分流（n 选 1） | 意图模型(jsonpath) → 非并行条件边 → 分支专用模型 |
| 多轮对话 | 上下文记忆 | 单 `llm_mixup` + `use_memory: true` + 提高 `session_round` |
| 规范输出 | guided + jsonpath 字段拆解 | 单模型 + `guided_json_expr` + 多字段 `output_parameters` + 根 `output_expression` |
| 人机交互 | 播报 / 补参 / 授权 | `interactions` 主动消息 → `type_input` → `type_select` |
| 智能体协作 | 多子智能体串行接力 | `act_agent` → `act_agent` → `llm_mixup` 汇总；`agents[].options` 用 rendering 传上下文 |

---

## 1) 并发执行 — 总线 + 并行 + 普通边汇总

```text
任务开始通知(node1 / act_interaction)
  --parallel--> 分析任务一(node2) --> 结束通知(node4)
  --parallel--> 分析任务二(node3) --> 结束通知(node5)
  -----------> 汇总分析(node6)   ← 同一总线普通边，等并行完成再跑
```

边片段：

```json
{"parallel": true, "src_node": "node1", "dst_node": "node2"}
{"parallel": true, "src_node": "node1", "dst_node": "node3"}
{"src_node": "node1", "dst_node": "node6"}
```

要点：

1. **开始后先用交互节点当总线**（可带一句「已拆分任务」的 `interactions` 播报）。
2. 并行边只挂无依赖分支；**汇总必须挂在同一总线的普通边**上。
3. 汇总模型用 `reference` 精确取 `$.node2.result_key` / `$.node3.result_key`，不要写模糊的 `$.result_key`。
4. 各支路末尾可用独立交互节点做「任务一/二已完成」进度播报（`from` 可不同，如 `msg1`/`msg2`）。

---

## 2) 条件判断 — 意图字段驱动 n 选 1

```text
意图识别(node1) --intent=="闲聊"--> 闲聊模型(node2)
                --intent=="咨询"--> 咨询模型(node3)
```

意图节点精髓：

```json
{
  "type": "llm_mixup",
  "system_prompt_template": "...输出格式必须严格为 {\"intent\":\"xxx\"}，xxx 仅为「闲聊」或「咨询」。",
  "output_parameters": [
    {
      "name": "intent",
      "type": "string",
      "value": {
        "type": "jsonpath",
        "expr": "$.intent",
        "text": { "type": "reference", "expr": "$.result_key" }
      }
    }
  ],
  "model": {
    "temperature": 0,
    "guided_json_expr": {
      "type": "literal",
      "expr": "{\"type\":\"object\",\"required\":[\"intent\"],\"properties\":{\"intent\":{\"type\":\"string\"}}}"
    }
  }
}
```

边条件（**纯 Expr，无 `$.`**）：

```json
{"condition": "intent == \"闲聊\"", "parallel": false, "src_node": "node1", "dst_node": "node2"}
{"condition": "intent == \"咨询\"", "parallel": false, "src_node": "node1", "dst_node": "node3"}
```

要点：

1. 边条件读的是**当前源节点已解析出的输出参数名**（如 `intent`），不是 `$.node1.intent`。
2. 官方示例有时只靠提示词约束 JSON；**生成脚本时务必补 `guided_json_expr`**，避免 jsonpath 抽空。
3. 分支模型提示词要按角色分化（闲聊口语 vs 专家严谨），不要复用同一套 system prompt。
4. 非并行条件边互斥：命中一条后不走普通边（若另有普通边兜底，需想清楚优先级）。

同类模式也见于「知识检索 / 图表 / 表格」示例的 `intent == "文本"|"问答对"|"Analysis"`。

---

## 3) 多轮对话 — 记忆开关

```json
{
  "session_round": 20,
  "edges": [],
  "nodes": [
    {
      "type": "llm_mixup",
      "use_memory": true,
      "log_memory": false,
      "user_prompt_template": "用户问题：{{$.input}}\n\n注意事项：请你尽可能直接得回答用户问题，不要擅自扩展"
    }
  ]
}
```

要点：

1. **`use_memory: true`** 是多轮关键；单节点即可，不必硬造边。
2. 提高根级 `session_round`（示例用 20）以覆盖追问轮次。
3. 用户提示词加「不要擅自扩展」，减少跑题；角色约束放在 system。

---

## 4) 规范输出 — guided Schema ↔ jsonpath ↔ 根渲染

```text
单模型提取 → 多字段 output_parameters(jsonpath) → 根 output_expression 拼装展示
```

模型侧：

- `guided_json_expr`：`type:object` + `required` 全字段 + 建议 `additionalProperties: false`
- 每个下游要用的字段各建一条 `output_parameters`，**必须带完整 `value`**（`jsonpath` + `text` 指向 `$.result_key`），禁止只有 name/type；见 [output-parameters-value-keypoints.md](output-parameters-value-keypoints.md)
- 提示词给 **Few-shot 完整 JSON 样例**，字段名与 Schema 一致

根输出：

```json
{
  "output_expression": {
    "type": "rendering",
    "expr": "规范输出Json格式：{{$.node1.result_key}}\n\nIP地址：{{$.node1.ip_address}}\n..."
  }
}
```

要点：先定 jsonpath 字段列表，再反推 Schema 与提示词（与 [guided-output-keypoints.md](guided-output-keypoints.md) 一致）。

---

## 5) 人机交互 — 播报 / 补参 / 授权三段式

```text
交互_主动播报(interactions) → 交互_补充缺失(type_input) → 交互_操作授权(type_select)
```

### 5.1 主动播报（无提问，只推消息）

```json
{
  "type": "act_interaction",
  "questions": [],
  "interactions": [
    {
      "from": "msg",
      "stream": true,
      "output_expression": {
        "type": "rendering",
        "expr": "用户输入：{{$.kv_user.input}}\n\n用户意图判断：..."
      },
      "output_parameters": []
    }
  ]
}
```

### 5.2 补充缺失信息（填空）

```json
{
  "questions": [
    {
      "name": "info",
      "type": "type_input",
      "options": [],
      "message": { "type": "literal", "expr": "参数校验不通过，请补充封禁时间，如“封禁7天”" },
      "timeout": 30
    }
  ],
  "interactions": []
}
```

### 5.3 操作授权（单选）

```json
{
  "questions": [
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
  ]
}
```

要点：

1. 官方确认单选 type 为 **`type_select`**（选项 `name` + `value`）。
2. 播报可用 `from: "msg"`（不必死记 `MsgFeedback`）；复杂进度可用不同 `from` 区分通道。
3. 真实业务应在补参/授权后接条件边（如读 `check._answer`）再调插件；本示例侧重交互形态演示。
4. 根级可设 `output_expression` 作为流程结束话术。

详见 [reference/interaction-node.md](../reference/interaction-node.md)。

---

## 6) 智能体协作 — 子智能体串行 + 模型汇总

```text
act_agent(代码助手) → act_agent(图表助手) → llm_mixup(汇总)
```

子智能体入参用 **rendering** 拼任务说明与上游结果：

```json
{
  "type": "act_agent",
  "agents": [
    {
      "id": "PLACEHOLDER_AGENT_ID",
      "options": [
        {
          "name": "input",
          "type": "string",
          "required": true,
          "value": {
            "type": "rendering",
            "expr": "用户提交内容：{{$.kv_user.input}}\n\n代码助手执行结果：{{$.node1.result_key}}\n\n任务说明：请你按照用户关注的统计维度生成图表"
          }
        }
      ]
    }
  ]
}
```

要点：

1. `agents[].id` 必须来自 catalog 的 `callable_agents[].agent_id`。
2. 下游 agent / 汇总模型都要显式引用 `$.nodeN.result_key`，形成接力上下文。
3. 若子智能体产出文件，根 `output_parameters` 声明 `type:file` 并 `reference` 对应节点文件字段（示例：`$.node2.f`）。
4. 与「单自规划 `act_coding_agent`」不同：这里是**多个已发布子智能体**编排，不是沙箱 Codex。

---

## 生成时检查清单

- [ ] 并行：总线普通边是否存在？汇总是否精确引用各支路？
- [ ] 条件：边条件无 `$.`？意图字段有 guided？
- [ ] 多轮：`use_memory` + `session_round`？
- [ ] 规范输出：Schema / jsonpath / 提示词字段三者对齐？
- [ ] 交互：播报 / 填空 / 单选字段形态正确？
- [ ] 协作：子智能体 ID 来自 catalog；rendering 传入上游结果？
