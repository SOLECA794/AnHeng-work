# 06 FAQ 与编排最佳实践

> 来源：文件处理 PDF 末页「使用 FAQ 及经验汇总」+ 各专题经验总结合并

## API 调用

智能体编排调试并发布后：

1. 在界面复制 **智能体 ID**
2. 调用 **智能体执行 OpenAPI**

## 语法速查（节点输出 / 边 / 引导约束）

| 场景 | 开源库 | 参考 |
|------|--------|------|
| 边条件布尔 | Expr | [expr-lang/expr](https://github.com/expr-lang/expr) / [expr-lang.org](https://expr-lang.org/) |
| 边条件模板段 / 提示词 / `output_expression` 渲染 | Go Template | [yosida95/uritemplate/v3](https://github.com/yosida95/uritemplate)；[gotemplate.io](https://gotemplate.io/)；`dig` / `hasKey` 为**库自带函数**（非平台注入）；可选 `{{dig "field" "" $.nodeX}}`（非强制） |
| `output_parameters` 抽字段 | JSONPath | [oliveagle/jsonpath](https://github.com/oliveagle/jsonpath) / [jsonpath.com](https://jsonpath.com/) |
| `guided_regex_expr` 等 | 正则 | [dlclark/regexp2](https://github.com/dlclark/regexp2) / [regex101.com](https://regex101.com/) |

## 模型选型

| 类型 | 特点 | 适用 |
|------|------|------|
| DeepSeek（推理） | 复杂推理强，提示词敏感 | 强推理、规划类 |
| 恒脑（通用） | 指令遵循好 | 结构化输出、流程固定 |
| 恒脑 + Guided | Json/Choice/Regex 约束 | 高约束 JSON/枚举输出 |

**提示词风格：**

- DeepSeek：讲清楚目标，少举例
- 恒脑：角色定位 + 任务要求 + 约束条件 + Few-shot

## 编排十大原则

0. **导入兼容性优先** — 节点全套标准字段、`model` 为对象、描述 ≥ 8 字；生成后跑 `scripts/validate_agent_json.py`（见 [import-compatibility.md](../reference/import-compatibility.md)）
1. **能工程化不用大模型** — 代码节点 / 插件节点，又准又快
2. **能并发不串行** — 并行边
3. **代码结合 AI 编程** — 提效 Python / Go 模板；**代码入参一律 `args.input.X`，禁止 `args.X`**
4. **强推理用 DeepSeek，高约束用恒脑 + Guided**
5. **插件/知识库：先检索再总结，模型做判断插件做执行**
6. **超长文件：切块 + 迭代**（迭代变量勿与普通输入同名；下游防包装）
7. **文件输出：结束节点声明 file 类型**
8. **条件路由：边 + Go 模板 + Expr**（模板勿用未验证函数如 `json`）
9. **并行汇总：多并行边 + 一条普通边**
10. **条件分支后 rendering 用 `dig` 兜底** — 引用可能未执行节点字段时，`{{dig "field" "" $.nodeX}}` 防报错
11. **有 catalog 则绑定真实 ID 并最大化接线**；无数据库才占位。**禁止编造 UUID**
12. **版本头分家** — `cot_version`（如 `v2`）与 `eng_version`（如 `v2.3.8`）勿混填

## 代码节点 FAQ

**Q：`AttributeError: 'Args' object has no attribute 'image'`（或其它业务字段名）？**  
A：入参不在 `args` 顶层。节点 `input_parameters` 里声明的 `image` 必须写成 `args.input.image`。完整说明：[code-node-args-input-keypoints.md](../examples/code-node-args-input-keypoints.md)。

**Q：代码/`return` 有字段，但 `{{$.nodeX.result}}` 为空或下游引用失败？**  
A：`output_parameters` 不能只写 name/type，必须为每个字段配完整 `value`（常用 jsonpath + `text` 引用 `$.result_key`）。见 [output-parameters-value-keypoints.md](../examples/output-parameters-value-keypoints.md)。

**Q：中间节点 `input_parameters` 需要配 `required` 吗？**  
A：不需要。`required` 仅用于开始节点（根）`input_parameters`。中间节点配置 `required` 可能造成逻辑异常。

**Q：代码节点开启迭代后，还要在 `input_parameters` 声明迭代参数吗？**  
A：不要重复声明。迭代变量来自 `iteration_values[].dest_name`，代码中直接使用 `args.input.<dest_name>`。见 [iteration-required-keypoints.md](../examples/iteration-required-keypoints.md)。

**Q：迭代节点为什么一次处理了全部数组，但报告只有 1 轮？**  
A：检查普通 `input_parameters` 是否声明了与 `iteration_values[].dest_name` 同名的参数。删除同名普通输入，只保留迭代绑定，并在代码中读取 `args.input.<dest_name>`。若收到的不是「当前轮对象」应显式报错，不要静默吃掉整表。

**Q：下游报 `'list' object has no attribute 'get'` 或 `list >= int`？**  
A：平台可能按输出参数把数组/标量包装成嵌套列表。递归收集业务字典、解包单元素标量列表，并用 `isinstance(value, int)` 后再做比较。禁止假设迭代输出形状唯一；轮次用 item 内 `round_index`，不要用外层 wrapper 长度。

**Q：结束节点报 `function "json" not defined`？**  
A：该模板运行时没有 `json` 函数。改为直接引用字段 `{{$.nodeX.field}}`；缺失字段可用 `dig` 兜底。生成时只使用已验证模板函数。

**Q：`the agent CoT version is invalid`？**  
A：`cot_version` 是配置版本（当前常用 `v2`），**不要**把引擎版本（如 `v2.3.8`）填进去；引擎版本写 `eng_version`。`session_round` 用正整数。

**Q：`the value for the parameter must be of array type: items`？**  
A：`type: array` 的 `default_value` 必须是 JSON 数组 `["a","b"]`，不能写成字符串 `"[\"a\",\"b\"]"`。integer/boolean/object/file 同理必须原生类型。见 [param-type-default-keypoints.md](../examples/param-type-default-keypoints.md)。

**Q：迭代节点配置了 `processed_items`，但解析结果一直是空数组？**  
A：不要用 `$.[*].processed_items[*]`。迭代后 `result_key` 是字符串数组，jsonpath 应写 `$.processed_items[*]`。见 [iteration-jsonpath-keypoints.md](../examples/iteration-jsonpath-keypoints.md)。

**Q：条件分流后，根 `output_expression` 引用 `{{$.node11.result}}` 报错？**  
A：n 选 1 条件边下末节点不一定执行。对可能缺失的字段改用 `dig`：`{{dig "result" "" $.node11}}`（空字符串也是合法输出；也可设默认文案）。见 [rendering-dig-fallback-keypoints.md](../examples/rendering-dig-fallback-keypoints.md)。

**Q：代码节点能用 JavaScript 吗？**  
A：平台支持 `interpreter.lang: "javascript"`，与 Python 同等可用。本 Skill 默认只生成 Python 脚本与示例。

## 生成脚本时的 ID 策略

用户提供恒脑库 + 用户 ID 时，先跑 `scripts/fetch_platform_catalog.py`，用返回的真实 ID 生成脚本（见 [platform-catalog.md](../reference/platform-catalog.md)）。

无 catalog 时：`uid`、`org`、`tools[].id`、`skills[]` 可先为空或 `PLACEHOLDER_*`。勿编造 UUID。

`model` 字段必须是对象：`{"model":"HengNao-v4","temperature":0,...}`，不能写成裸字符串。

## 生成脚本前必问用户

见 [OPEN_QUESTIONS.md](../OPEN_QUESTIONS.md) 与用户确认清单。
