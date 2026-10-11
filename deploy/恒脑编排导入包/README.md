# 恒脑平台智能体编排导入包

> 状态：v1.1（2026-10-11，按平台导入格式答复修正）
> 依据：恒脑技能包文档（`deploy/恒脑平台文档/hengnao_skill_docs/`）import-compatibility / json-patterns / node-types
> 产物：网站安全智能巡检智能体（对齐 T27 编排设计方案）

## 1. 文件说明

| 文件 | 用途 |
| --- | --- |
| `网站安全智能巡检_编排导入.json` | 可导入编排脚本（v2 头、3 节点、全套标准字段、真实 catalog ID） |
| `网站安全智能巡检_编排导入.zip` | 同 JSON 的 zip 打包（若平台导入要求 zip 格式） |

## 2. 编排结构（3 节点 + 根）

```text
开始(根) → node0 授权确认(act_interaction)
        → node1 自规划巡检(act_coding_agent, MCP 白名单)
        → node2 结果聚合分级(act_interpreter)
        → 根 output_expression 输出
```

| 节点 | 类型 | 关键配置 |
| --- | --- | --- |
| node0 | act_interaction | 填空收集授权目标 URL + 单选授权确认（type_input/type_select） |
| node1 | act_coding_agent | coding_agent_config(model=GLM-5.1, **mcps=白名单 MCP 字符串数组**, skills=安全审计工具包)；节点级 model=HengNao-v4；MCP 绑定在 coding_agent_config.mcps（平台答复要求） |
| node2 | act_interpreter | Python 聚合：去重、分级（高危/中危/低危/信息）、生成报告 JSON |

## 3. 导入方法

1. 打开恒脑平台 `https://www.das-ai.com/development`；
2. 新建智能体 → 选择「智能体导入」→ 上传 zip（或直接粘贴/导入 JSON）；
3. 导入成功后进入画布检查：节点连线、模型、MCP/Skill 绑定；
4. 调试运行：输入授权目标（如 `http://127.0.0.1:8080`）→ 确认授权 → 观察巡检执行；
5. 发布 → 记录生成的**智能体 ID**（开放接口调用必需）。

## 4. 导入后必须核对项

- **MCP 白名单**：当前绑定 6 个 MCP 均为白名单 ✅；如需增加 Nmap/Masscan 等，先过组内评审（⚠️ 档）；SQLmap/SSH/MySQL/渗透工具集保持 ❌ 不绑定；
- **Skill**：绑定「安全审计工具包」；可按需增删（catalog 14 个 skill 的真实 ID 见 `06-platform_catalog.json`）；
- **模型**：自规划用 GLM-5.1（≥128K 上下文，自规划节点硬要求）；模型节点 HengNao-v4；
- **非破坏约束**：node1 提示词已写死"禁止注入利用/暴力破解/破坏性操作"，勿删；
- **发布范围**：建议"同租户"或"仅自己"，勿公开到公网。

## 5. 导入失败排查

- UI 仅提示"失败"时：浏览器 F12 → Network → 找到导入请求 → 看 Response 的 `message/error/detail` 原文；
- 常见原因：描述长度 <8、重复键、model 写成字符串、tools[].id 非 catalog 真实 ID（本项目已用真实 ID，若平台 catalog 变更需同步更新）；
- 增量排查：先导入单节点最小版验证格式，再逐步加节点。

## 5.1 平台答复修正记录（2026-10-11）

依据 `hengnao_import_format_answers.md`（平台专用 agent 答复）：
1. **自规划节点 MCP 绑定位置**：写 `coding_agent_config.mcps: ["<server_id>", ...]`（字符串数组），节点级 `mcps` 清空——已修正；
2. **根入参**：增加非必填 `input`（提升导入兼容性）——已修正；
3. 版本头 v2.3.8/v2、节点类型、model 对象规则、skills 绑定、on_error 无需 setting——原方案已符合；
4. **ZIP 结构**：平台只接受 zip，前端不暴露包内 manifest/目录规则，**建议以平台导出的 ZIP 为结构基准**——当前 zip 为单 JSON 打包，若导入失败，请在平台导出任一智能体 zip 作结构参照后重打包。

## 6. 变更记录

| 版本 | 日期 | 变更人 | 变更内容 | 影响范围 |
| --- | --- | --- | --- | --- |
| v1.0 | 2026-10-11 | 课题立项 | 基线发布（3 节点编排 JSON + zip + 使用说明） | T27 编排 |
