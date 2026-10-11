# 关键片段：条件分支后引用前序节点用 `dig` 兜底

> 来源：场景推荐智能体实测（2026-08-25）  
> 拓扑：多路并行 + 条件边 n 选 1 → 并非每条路径都会走到同一末节点。  
> 根 `output_expression` 直接写 `{{$.node11.result}}` 时，若 `node11` 未执行或字段缺失 → **系统报错**。  
> 改用 `dig` 可返回默认值（空字符串也是合法输出）。

## 硬规则

当脚本存在**条件边 / 并行分支 / 并非所有路径都会汇合到同一节点**时：

| 场景 | 推荐 | 风险 |
|------|------|------|
| 根 `output_expression` 引用可能未跑到的节点字段 | `{{dig "result" "" $.node11}}` | 直接 `{{$.node11.result}}` 可能报错 |
| 交互 `interactions[].output_expression` | 同上 | 同上 |
| 子智能体 `agents[].options` rendering 拼接 | 对**可选**上游字段用 `dig` | 缺字段时模板失败 |
| 节点**必定执行**且无分支 | 可直接 `{{$.nodeX.field}}` | — |

> **非强制**：`dig` 是质量建议与兜底写法；校验器仅给 note，不因缺 `dig` 判 FAIL。

`dig` 是 **Go 模板库自带函数**（非平台注入）：`dig "字段名" "默认值" 节点上下文`。可用函数以模板库文档为准。

## 错误 → 正确（来自 `Prompts/错误示例1.md`）

**❌ 条件分流后，末节点不一定有 `result`：**

```json
"output_expression": {
  "type": "rendering",
  "expr": "{{$.node11.result}}"
}
```

**✅ 用 `dig` 兜底（空也是输出）：**

```json
"output_expression": {
  "type": "rendering",
  "expr": "运行结果：{{dig \"result\" \"\" $.node11}}"
}
```

也可设可读默认文案：

```json
"expr": "运行结果：{{dig \"result\" \"暂无匹配场景的执行结果，请补充需求后重试。\" $.node11}}"
```

## 举一反三

### 1) 多个可选上游节点（走 A 或 B）

```json
"expr": "{{if ne (dig \"result\" \"\" $.node11) \"\"}}执行结果：{{dig \"result\" \"\" $.node11}}{{else}}说明：{{dig \"result\" \"\" $.node10}}{{end}}"
```

更简洁：末节点统一汇总，只在汇总节点输出里用一次 `dig`。

### 2) 取 `result_key` 整段 JSON

```json
"expr": "原始输出：{{dig \"result_key\" \"{}\" $.node8}}"
```

### 3) 文件字段可能不存在

```json
"expr": "{{if ne (dig \"file\" \"\" $.node3) \"\"}}下载：{{dig \"file\" \"\" $.node3}}{{else}}未生成文件{{end}}"
```

### 4) 与 `output_parameters` 配合

先保证节点 `output_parameters` 用 jsonpath 拆好字段（见 [output-parameters-value-keypoints.md](output-parameters-value-keypoints.md)），再在 rendering 里 **`dig` 读具名字段**，不要假设分支一定产生该字段。

## 何时不必用 `dig`

- 单线流水线，无条件的串行边，且该节点**一定**在 `output_expression` 之前执行过
- 引用 `$.kv_user.*` / `$.kv_envs.*`（用户输入、环境变量通常始终存在）
- 边条件 Go 模板里已有 `{{if hasKey ...}}` 完整分支（但 `dig` 往往更短）

## 与边条件语法区分

| 位置 | 写法 |
|------|------|
| `edges[].condition` 纯 Expr | `len(node8.ss_list) > 0`（无 `$`） |
| `output_expression` / 提示词 rendering | `{{dig "result" "" $.node11}}`（`{{}}` 内用 `$`） |

## 生成自检

- [ ] 存在条件边时，根 `output_expression` 对「可能未执行节点」的字段用 `dig`
- [ ] `dig` 第二参数给了合理默认（`""` 或用户可读文案）
- [ ] 不把 `dig` 写进 Expr 条件段（只在 rendering 模板里）
- [ ] 仍保证节点 `output_parameters` + jsonpath 配置完整
