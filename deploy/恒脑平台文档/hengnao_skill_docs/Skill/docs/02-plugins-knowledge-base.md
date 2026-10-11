# 02 插件及知识库

> 来源：恒脑智能体服务构建典型场景分析（插件及知识库）-智能体合伙人.pdf  
> 2025.3.19

## 定位

> **清晰表达插件和知识库能力及使用场景，是模型思维链用好它们的前置条件。**

在智能体范式中：

- **插件/工具** = 智能体的「手」（调用外部能力）
- **知识库/RAG** = 智能体的「长期记忆」（检索增强）

## 案例索引（平台内实战智能体）

### 插件调用

| 场景 | 说明 |
|------|------|
| 监管智能安全运营助手 | 插件路由、条件分支 |
| 模型路由器 | 模型节点判断后调插件 |

### 知识库检索

| 场景 | 说明 |
|------|------|
| FAQ 知识库检索 | 问答类 |
| 知识召回及总结智能体 | 检索 + 模型总结 |
| 图文检索 | 模型调知识库 |

## 推荐架构模式

### 知识问答

```
知识检索（插件） → 模型总结
```

先检索、再总结，**内容可控**。

### 知识上下文学习（如合规检查）

```
模型提取关键词 → 知识检索（可多路并行） → 模型业务分析
```

见 [examples/compliance-check-keypoints.md](../examples/compliance-check-keypoints.md)：`node0` 提关键词 → `node6/10/11` 并行检索 → `node7` 深入分析。

### 模型 + 插件解耦

```
模型节点（条件判断，输出参数） → 插件节点（执行调用）
```

**不要让一个模型节点路由大量插件或知识库**——受上下文限制且逻辑难控。

## 使用策略（经验总结）

| 建议 | 原因 |
|------|------|
| ✅ 模型做判断，插件做执行 | 发挥各自优势 |
| ✅ 先检索再总结 | 可控、可溯源 |
| ✅ 先提关键词再检索 | 提高召回精度 |
| ❌ 单模型节点挂大量插件/知识库 | 上下文爆炸、路由混乱 |
| ❌ 模型节点路由超长返回的插件 | 上下文限制 |

## 脚本中的插件 / MCP / 子智能体

插件、`act_tool` 上 MCP、子智能体入参 **必须从 catalog/DB 复制** 后在脚本里配 `value`。三种 MCP 挂法见 [mcp-wiring-keypoints.md](../examples/mcp-wiring-keypoints.md)。

### 插件 `act_tool`

```json
{
  "type": "act_tool",
  "tools": [{
    "id": "PLACEHOLDER_PLUGIN_KB_RETRIEVE",
    "options": [
      {
        "name": "query",
        "type": "string",
        "value": { "type": "reference", "expr": "$.node12.keyword1" }
      }
    ]
  }],
  "result_key": "result_key"
}
```

知识库写入常见参数：`file`、`sourceId`、`labels`、`type`（doc/faq）、`blockSize` 等——**以 catalog 里该能力的 `input_parameters` 为准**。

### FAQ / 原文召回绑定知识库

当目标是“查询我的某个知识库并回答问题”时，FAQ 检索与原文内容召回**优先**使用 `labels` 传知识库标签/库标识，不要把知识库 id 或租户 id 填进 `permissions`。

- `labels`：用于限定检索到指定 FAQ / 文档知识范围
- `permissions`：权限过滤字段，不是默认的知识库绑定字段；没有明确需求时不要乱填
- `owner`：仅在明确要按上传者过滤时再传

参考模式：

```json
{
  "name": "labels",
  "type": "array",
  "value": {
    "type": "associate",
    "expr": "[\"<知识库标签或平台要求的库标识>\"]"
  },
  "required": false
}
```

如果 catalog 已知某个知识库可用，就优先在设计说明中标明“FAQ 检索绑定哪个 labels、原文召回绑定哪个 labels”；只有平台目录和示例都无法确认时，才保留占位。

有运行时 catalog 时：`tools[].id` 填能力的真实 `tool_id`，知识库 id 填 `knowledge_bases[].id`（字符串）。无 catalog 时用 `PLACEHOLDER_PLUGIN_*`，禁止编造 UUID。拉取方式见 [platform-catalog.md](../reference/platform-catalog.md)。

## 最大化接线

在用户可见的系统插件 + 租户插件/知识库范围内主动绑定，而不是只搭空 `act_tool`：

- 文档/合规：文档解析 → 知识写入 → 状态轮询 → 原文召回 / FAQ 检索
- 知识库问答：开始节点后先接**空交互总线**，再并行 FAQ 检索 / 原文召回，最后由总线普通边汇总到模型总结
- 知识库类型互斥：意图识别后条件分流到「原文召回」或「FAQ 召回」（见 [official-demos-plugins-keypoints.md](../examples/official-demos-plugins-keypoints.md)）；与并行联合检索二选一
- 图表：可将图表类工具挂在 `llm_mixup.tools[]` 由模型调用；表格更常见「guided array → `act_tool` 表格插件」
- 代码执行插件：LLM 只输出裸代码 → 插件执行 → 判错回环（自定义 `result_key`）
- 自规划：按名称匹配 Skill 与 MCP 模板，填 `skill_id` / `server_id`
- 不要把整个 catalog 挂到单个模型节点；模型判断、插件执行

## 模糊项

- [x] 官方/租户插件 UUID：不写进 Skill，运行时从库拉取（`scripts/fetch_platform_catalog.py`）
- [ ] 「模型路由器」具体节点拓扑脚本
- [ ] FAQ vs doc 知识库写入插件的选型规则细节（catalog 里 `type_hint` 可辅助：1=doc，2=faq）
