# 样例关键片段：全局文件记忆媒介

> 来源：`Prompts/SkillGeneratePrompt.md`（已提炼，非完整脚本）

## 目标

把 `private_envs` 文件变量作为长期记忆载体，实现“写入、清空、读取回显”。

## 最小拓扑

```text
模型意图判断(node0) -> 代码写入记忆(node1) -> 插件读取文件(node2)
```

## 关键配置

### 1) 全局记忆文件变量

```json
{
  "private_envs": [
    {
      "name": "知识积累",
      "type": "file",
      "default_value": "file:长期记忆知识积累.txt<...>"
    }
  ]
}
```

### 2) 模型输出结构（意图 + 内容）

```json
{
  "intent": 0,
  "userInput": "用户输入",
  "answerOutput": "模型回复"
}
```

### 3) 代码节点核心逻辑

```python
existing = FileBucket().get(knowledgeFile)
if intent == 1:
    new_content = header_text          # 清空但保留标题
else:
    new_content = remove_old_entries(existing_text) + "\\n\\n" + append_text
FileBucket().put(knowledgeFile, new_content.encode("utf-8"))
```

## 记忆维护策略

- 标题固定，正文按 Markdown 条目追加
- 清理 7 天前历史记录
- 支持自然语言触发“清空记忆”

## 注意事项

- 复制智能体后，必须替换默认环境文件变量，避免多个实例写入同一文件
- 输出可直接引用 `$.kv_envs.知识积累` 作为下载文件
