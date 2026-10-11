# 样例关键片段：排班智能体

> 来源：`Prompts/SkillGeneratePrompt.md`（已提炼，非完整脚本）

## 目标

输入排班要求 + 人员文件，生成值班表与值班安排表，并支持后处理优化。

## 架构摘要

```text
文件解析 -> 任务状态轮询 -> 首次排班要求提取 -> 交互补充 -> 排班循环 -> 结果合并 -> Excel输出
```

## 关键边模式

### 1) 文件解析轮询

```json
{"condition":"code == 1","src_node":"node3","dst_node":"node4"}
{"src_node":"node3","dst_node":"node2"}
```

### 2) 交互补充回环

```json
{"condition":"Completed == \"0\"","src_node":"node9","dst_node":"node7"}
```

### 3) 并行后处理分流（同条件多分支）

```json
{"condition":"rota_code == \"1\"","parallel":true,"src_node":"node11","dst_node":"node14"}
{"condition":"rota_code == \"1\"","parallel":true,"src_node":"node11","dst_node":"node13"}
{"condition":"rota_code == \"1\"","parallel":true,"src_node":"node11","dst_node":"node16"}
```

## 关键提示词策略

- node4/node9：仅提取 `Completed/NextQ/Info`
- node11：按历史排班与昨日排班约束进行“下一天排班”
- node16/node17：先提修改意见，再执行结构化修正

## 关键代码节点职责

| 节点 | 作用 |
|------|------|
| node8 | 追加用户补充要求（HistoryAnswer） |
| node12 | 合并历史排班 + 本轮排班，输出 `history_input` |
| node13/node15/node18 | 生成不同阶段 Excel 文件（`openpyxl`） |

## 最终输出字段（示例）

- `值班表` / `值班安排表`
- `值班表_后处理` / `值班安排表_后处理`
- `值班表_后处理1` / `值班安排表_后处理1`

## 复用建议

1. 先拆成“提取需求”“排班生成”“后处理优化”三段
2. 用 `rota_code` 控制是否继续循环
3. 文件输出统一走代码节点 + `output_parameters(type=file)`
