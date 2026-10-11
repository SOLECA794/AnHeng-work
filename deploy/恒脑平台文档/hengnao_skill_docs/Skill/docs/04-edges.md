# 04 边的使用（并行边 / 条件边 / 普通边）

> 来源：恒脑智能体服务构建典型场景分析（边的使用）-智能体合伙人.pdf  
> 2025.3.25

## 概念

恒脑编排业务流是**有向图**。**边**表示节点间的**时序关系**与**条件关系**。

## 边的类型

| 类型 | 定义 | 优先级 |
|------|------|--------|
| **并行边** | `parallel: true`，多条同时执行 | **最高** |
| **条件边** | 带 `condition`；非并行时 n 选 1（节点序号最小者优先） | 中 |
| **普通边** | 无条件、非并行；上一节点结束后激活下一节点 | **最低** |

### 优先级公式

```
有条件并行边 (m选n, m≥n) = 无条件并行边 (n选n)
  > 非并行条件边 (n选1)
  > 普通边 (1选1)
```

## 条件语法

**实现原理：先 Go 模板渲染 → 再 Expr 执行。**

平台对 `edges[].condition` 的处理顺序：

1. 若条件字符串中含 `{{...}}`，先按 **Go 模板**渲染（模板内用 `$` 表示根上下文，如 `$.node19.deleteKnowledge._answer`）；
2. 渲染结果再交给 **Expr** 做布尔判断。

因此：**纯 Expr 条件里不要写 `$.` 前缀**，否则条件解析失败。

| 场景 | 写法 | 示例 |
|------|------|------|
| 边条件 · 纯 Expr | **无** `$` | `len(kv_user.tempComplianceReqDoc) > 0` |
| 边条件 · 节点输出 | **无** `$` | `len(node2.results) > 0`、`Completed == 0` |
| 边条件 · Go 模板段 | `{{}}` **内**用 `$` | `{{if hasKey $.node19.deleteKnowledge._answer "A"}}true{{else}}false{{end}}` |
| 节点传参 `value.expr`（reference） | **有** `$` | `$.kv_user.input`、`$.node1.result_key` |
| 提示词 / `output_expression` 渲染 | `{{}}` **内**用 `$` | `{{$.node0.result_key}}`；**条件分支后**引用可能未跑到的节点字段建议 `{{dig "result" "" $.node11}}` |

| 语言 / 开源库 | 用途 | 参考 |
|--------------|------|------|
| Go Template | 边条件模板段、`rendering` / `output_expression`、提示词变量 | 实现库 [yosida95/uritemplate/v3](https://github.com/yosida95/uritemplate)（`github.com/yosida95/uritemplate/v3`）；语法参考 https://gotemplate.io/ ；`dig` / `hasKey` 等为**该模板库自带函数**（非平台注入） |
| Expr | 边条件布尔判断 | https://expr-lang.org/ ；[expr-lang/expr](https://github.com/expr-lang/expr) |
| JSONPath | 节点/根 `output_parameters` 从 `result_key` 抽字段 | [oliveagle/jsonpath](https://github.com/oliveagle/jsonpath) |
| 正则 | `guided_regex_expr` 等输出约束 | [dlclark/regexp2](https://github.com/dlclark/regexp2) |

### 常见错误（边条件）

错误：`"condition": "len($.node2.results) > 0"`  
正确：`"condition": "len(node2.results) > 0"`

错误：`"condition": "len($.kv_user.input) > 0"`  
正确：`"condition": "len(kv_user.input) > 0"`

> `$.node2.results` 是**传参引用**写法，不是**边条件 Expr**写法。二者不要混用。

### 示例

**Expr 条件（并行）：**

```json
{
  "condition": "len(kv_user.complianceReqDocs) > 0",
  "parallel": true,
  "src_node": "node3",
  "dst_node": "node4"
}
```

**Go 模板 + Expr（非并行）：**

```json
{
  "condition": "{{if hasKey $.node19.deleteKnowledge._answer \"A\"}}true{{else}}false{{end}}",
  "parallel": false,
  "src_node": "node19",
  "dst_node": "node20"
}
```

**轮询回边：**

```json
{
  "condition": "isCompleted == false",
  "parallel": false,
  "src_node": "node9",
  "dst_node": "node5"
}
```

**意图字段分流（非并行）：**

```json
{
  "condition": "intent == \"咨询\"",
  "parallel": false,
  "src_node": "node1",
  "dst_node": "node3"
}
```

> `intent` 来自源节点 `output_parameters` 的 jsonpath 解析结果；边条件写裸字段名。生成时意图模型务必配 `guided_json_expr`。

**文件扩展名门禁（用户 file 输入会暴露 `{name}_attribute`）：**

```json
{
  "condition": "input_img != \"\"  &&  input_img_attribute.Extension in ['jpg', 'png']",
  "parallel": false,
  "src_node": "node3",
  "dst_node": "node1"
}
```

```json
{
  "condition": "f != \"\"  &&  f_attribute.Extension in ['doc', 'docx','pdf','txt','json','pptx','xlsx']",
  "parallel": false,
  "src_node": "node3",
  "dst_node": "node1"
}
```

**执行失败回环（自定义 result_key）：**

```json
{
  "condition": "tmp_result == 'error'",
  "parallel": false,
  "src_node": "node3",
  "dst_node": "node2"
}
```

## 案例索引

| 智能体 | 边模式 |
|--------|--------|
| 监管智能安全运营助手 | 非并行条件边，n 选 1 |
| 通用行业数据分类分级 v3.2.3 | 非并行条件边，复杂条件 |
| 等保测评率统计智能体 | 并行条件边，m 选 n |
| 金融文档合规检查 | 交互总线 + 多路并行 + 汇总普通边（见 [compliance-check-keypoints.md](../examples/compliance-check-keypoints.md)） |
| 官方并发执行示例 | 总线并行 fan-out + 普通边汇总（见 [official-demos-orchestration-keypoints.md](../examples/official-demos-orchestration-keypoints.md)） |
| 官方条件/知识/图表示例 | 意图字段 n 选 1（见同上 + [official-demos-plugins-keypoints.md](../examples/official-demos-plugins-keypoints.md)） |
| 官方图片/文档示例 | `*_attribute.Extension` 文件门禁 |

## 使用策略

| 规则 | 说明 | 校验 |
|------|------|------|
| 能并发不串行 | 多用并行边提效 | — |
| 并行汇总 | 多条并行边 + **一条普通边**；并行完成后走普通边可拿到**所有**并行结果 | **硬拦截** |
| 条件与普通互斥 | **任一**非并行条件边满足时，**不再执行**普通边 | — |
| 普通边唯一 | 每个源节点**只能有一条**普通边 | **硬拦截** |
| n 选 1 冲突 | 多条非并行条件边均满足 → 只执行**目标节点序号最小**的边 | — |
| 开始不直接并行分支 | `entry` 若直接挂并行边，须为 `act_interaction` 总线（意图模型条件 n 选 1 除外） | **硬拦截** |
| 无死节点 | 从 `entry` 可达 | **硬拦截** |
| 复杂判断 | 需要访问复杂结构时用 Go 模板包一层，模板内写 `$.nodeX`；渲染后再由 Expr 判断 | — |
| rendering `dig` | 条件分支后引用可能未执行节点字段时建议兜底 | **仅建议（note）** |

## 交互总线模式

合规检查等复杂流常用 **交互节点** 作 fan-out 入口：

```
node3(交互总线) --parallel--> node2, node4, node22, ...
node3 --普通边--> node0 (全部并行完成后的汇总下一跳)
```

### 开始节点不要直接并行分支

实战中，**不要让非交互节点的 entry 直接挂并行边**。若需要并行 fan-out，让 `entry` 本身是空的 `act_interaction` 总线，或 `entry → 空交互总线 → 再并行`：

```text
开始(entry = 空交互节点 node_bus)
node_bus --parallel--> 分支A
node_bus --parallel--> 分支B
node_bus --普通边--> 汇总后续节点
```

**允许**：`entry` 为意图模型（`llm_mixup`）+ 非并行条件边 n 选 1（官方条件判断示例）。  
**禁止**：`entry` 为模型/代码/插件等却直接 `parallel: true` fan-out。

这样既兼容平台行为，也便于后续继续加并行、条件边和回汇总链路。

### 并行汇总的正确写法

并行分支的“完成后再往后走”不是让分支节点彼此互连，而是：

1. **同一个源节点** 发出多条 `parallel: true` 边；
2. **同一个源节点** 再发出 **一条普通边** 作为汇总下一跳；
3. 平行分支全部结束后，再走这条普通边。

错误示例（不要这样）：

```text
node1 --parallel--> node2
node3 --parallel--> node2
```

正确示例：

```text
node_bus --parallel--> node_faq
node_bus --parallel--> node_doc
node_bus -----------> node_llm
```

此时 `node_llm` 才能稳定拿到 `$.node_faq.*` 和 `$.node_doc.*` 的结果。

### 条件分支后的 rendering 兜底（`dig`）

非并行条件边是 **n 选 1**，并非每条路径都会执行同一末节点。根 `output_expression` 或交互 `interactions[].output_expression` 若直接写 `{{$.node11.result}}`，当 `node11` 未执行或字段缺失时**系统报错**。

**推荐**：对可能不存在的前序节点字段用 Go 模板 **`dig`**：

```json
"output_expression": {
  "type": "rendering",
  "expr": "运行结果：{{dig \"result\" \"\" $.node11}}"
}
```

第二参数可为空字符串（空也是合法输出），或可读默认文案。详见 [rendering-dig-fallback-keypoints.md](../examples/rendering-dig-fallback-keypoints.md)。

## 已确认

- **n选1 节点序号**：比较 `dst_node` 的 id 字符串（如 `node4` 优先于 `node22`）
- **条件不满足行为**：对应边**不执行**（跳过该边，不触发目标节点）
- **并行分支失败行为**：由失败节点自身 `on_error` 控制（返回/继续/重试），不是并行边单独配置

### 失败策略 `on_error`（节点级）

| 值 | 行为 | 相关配置 |
|----|------|----------|
| `0` | 失败后返回（默认） | — |
| `1` | 失败后继续 | `on_error_setting.err_parameters`（可配置默认返回值） |
| `2` | 失败后重试 | `on_error_setting.max_retries` |

示例（失败后继续）：

```json
{
  "on_error": 1,
  "on_error_setting": {
    "err_parameters": [
      {
        "name": "result_key",
        "type": "string",
        "value": { "type": "literal" }
      }
    ]
  }
}
```

示例（失败后重试 3 次）：

```json
{
  "on_error": 2,
  "on_error_setting": {
    "err_parameters": [],
    "max_retries": 3
  }
}
```

## 备注

- 并行图中某分支失败后的整体表现取决于该分支节点 `on_error` 配置与后续可达性。
