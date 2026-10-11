# 样例关键片段：单自规划研判

> 来源：`Prompts/SkillGeneratePrompt.md` 历史导出（已提炼，非完整脚本）

## 目标

单节点 `act_coding_agent`（Codex 沙箱）完成「规范入库 → 密文解码 → 报文研判 → HTML 报告 → 发邮件」。

## 最小拓扑

```text
开始(input / rolefile / analyzefile) -> 自规划(node0) -> 结束
```

## 输入

| 参数 | 类型 | 说明 |
|------|------|------|
| `input` | string | 用户指令 |
| `rolefile` | file | 客户本地研判规范 |
| `analyzefile` | file | 待研判报文 |

## 关键节点字段

```json
{
  "id": "node0",
  "type": "act_coding_agent",
  "timeout": 3600,
  "coding_agent_config": {
    "name": "自规划研判",
    "model": "GLM-5.1",
    "skills": [
      "PLACEHOLDER_SKILL_MEMORY",
      "PLACEHOLDER_SKILL_CIPHER_DECODE",
      "PLACEHOLDER_SKILL_ALARM_ASSESS",
      "PLACEHOLDER_SKILL_SUMMARIZE",
      "PLACEHOLDER_SKILL_HTML",
      "PLACEHOLDER_SKILL_EMAIL"
    ],
    "prompt": "（见下方十段结构，按业务改写）"
  }
}
```

注意：`coding_agent_config.model` 是**字符串**（模型名）；节点级 `model` 仍必须是**对象**。有 catalog 时 `skills` 填真实 `skill_id`；无 catalog 才用 `PLACEHOLDER_SKILL_*`，禁止编造 UUID。

**`coding_agent_id` / `coding_agent_config.id`**：生成脚本时**不必预填**。用户把脚本拷到新平台保存时，平台会追加该信息。

## `coding_agent_config.prompt` 十段结构

1. **身份与输出规范**：对外称呼、落款、不确定处标注「待人工复核」
2. **Memory Skill**：持久化规范 / 称呼 / 最近指令；写入后短确认
3. **本地规范覆盖**：可机器判定的规则才修正标准研判；否则标人工复核
4. **内容总结 Skill**：解码要点 + 研判要点 → 执行摘要 / 结论
5. **HTML 报告**：单文件、时间戳文件名 `report_YYYYMMDD_HHMMSS.html`，禁止覆盖 `report.html`
6. **邮件 Skill**：按收件人分别发送，不一次群发
7. **自我净化 Skill**：连续报错 / 重复调用 / 上下文过长时收敛到唯一主路径
8. **密文解码 + 告警研判 Skill**：长密文先脚本提取再传入，避免截断
9. **安全与合规**：不编造明文 / IOC / 发送成功
10. **交互闭环**：上传规范 → 设定称呼 → 上传报文下指令

用户提示词可在 prompt 内用 Go 模板注入：`{{$.kv_user.analyzefile}}`、`{{$.kv_user.rolefile}}`。

## 复用建议

1. 端到端 Agent、多 Skill 协同 → 优先单自规划，不要硬拆流水线
2. Skill / MCP：有 catalog 则填真实 ID；没有对应资源再占位，设计说明列出。`coding_agent_id` 不必预填（平台保存时追加）
3. `timeout` 建议 3600（研判+解码+发信耗时长）
