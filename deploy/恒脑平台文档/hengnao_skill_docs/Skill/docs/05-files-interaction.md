# 05 文件处理及交互应用

> 来源：恒脑智能体服务构建典型场景分析（文件处理及交互应用）-智能体合伙人.pdf  
> 2025.3.26

## 文件输入来源

1. **开始节点**传参（`type: file` / `files`）
2. **插件**（官方或自定义）生成 file id
3. **代码节点**生成 file id

### 文件属性（边条件门禁）

用户 file 入参（如 `input_img`、`f`）在边条件里可访问 **`{name}_attribute.Extension`**，用于扩展名白名单：

```text
input_img != "" && input_img_attribute.Extension in ['jpg', 'png']
f != "" && f_attribute.Extension in ['doc','docx','pdf','txt','json','pptx','xlsx']
```

完整模式见 [official-demos-plugins-keypoints.md](../examples/official-demos-plugins-keypoints.md)（图片识别 / 文档解析）。

## 文件处理手段

| 方式 | 说明 |
|------|------|
| 官方插件 | 向量计算、文档解析、图片内容提取等 |
| 自定义插件 | 接入外部引擎处理 |
| 代码节点 | `FileBucket` 读写 + `openpyxl`、`docx` 等内置库 |

> 完整预装库见 [reference/code-node-libraries.md](../reference/code-node-libraries.md)（215 个包）。  
> **开放服务接口**（Agent / RAG / OpenAI 兼容 LLM）在代码节点中的鉴权与调用见 [reference/open-apis.md](../reference/open-apis.md)，关键片段见 [examples/code-node-open-api-keypoints.md](../examples/code-node-open-api-keypoints.md)。

### 代码节点内调大模型（平台推荐）

优先使用预装 `openai` SDK（容器内常已注入 endpoint）；亦可 `langchain_openai.ChatOpenAI`。对外 HTTP 则用 `Authorization: Bearer <LLMKey>` 调 `{host}/api/openai/v1/chat/completions`。

### 代码节点内调 Agent / RAG 开放接口

使用 `appKey` + HMAC-SHA256 `sign`（`timestamp\\nsecret\\nkey`）请求 `{OPENAPI_HOST}/open/api/v2/agent/*` 或 `/open/api/v2/document/*`。  
**Host 从沙箱系统环境变量读取**：`os.environ["OPENAPI_HOST"]`（平台内置，勿再让用户配置 base）。密钥放 `private_envs`，不要写进提示词或公开字段。

### 代码节点文件模式

`FileBucket` **仅三种方法**（已确认，无其它 API）：

| 方法 | 作用 |
|------|------|
| `get(file_id)` | 读取文件字节 |
| `put(file_id, data)` | 按指定 file id 写入 |
| `save(filename, data)` | 保存并返回新 file id |

```python
from runtime import Args

def handler(args: Args) -> dict:
    fb = FileBucket()
    # 文件入参在 args.input 下，不是 args.file_id
    data = fb.get(args.input.file_id)
    # 处理 ...
    out_id = "file:报告.docx<uuid>"
    fb.put(out_id, binary_data)
    # 或：out_id = fb.save("报告.docx", binary_data)
    return {"status": "success", "file_id": out_id}
```

> **常见错误**：`args.image` / `args.filename` → 必须 `args.input.image` / `args.input.filename`。见 [code-node-args-input-keypoints.md](../examples/code-node-args-input-keypoints.md)。

### 文件 ID 格式

```
file:文件名.ext<uuid>
```

## 文件输出

| 渠道 | 要求 |
|------|------|
| 平台 UI 下载 | `output_parameters` 声明 `type: "file"` |
| 插件输出到第三方 | 插件节点配置 |
| OpenAPI | 结束节点 `output_parameters` + 智能体执行接口 |

## 限制与策略

| 规则 | 说明 |
|------|------|
| 单文件 ≤ 100MB | 平台限制 |
| 多文件 | 支持，由用户自定义 |
| 超长内容 | 代码切块 → 模型/迭代分析 |
| 代码无法处理的格式 | 先走插件再进业务流 |
| Word 小模板 | 可 base64 放代码常量 |
| Word 大模板含图 | 每次调用时作为传参输入 |

## 案例

| 智能体 | 要点 |
|--------|------|
| 商密文本替换 | Excel 数据替换 docx 占位符 |
| 等保测评率统计 | 外部文件输入 |
| 安全情报分析 | FileBucket 缓存，降低外部接口调用 |
| 金融合规检查 | 文档解析插件 → 代码分片 → 报告 Word 输出 |
| 全局文件记忆媒介 | `private_envs` 文件长期累积、按意图清空、7天窗口清理 |
| 官方图表/表格示例 | 根 `output_parameters` 声明 file；`output_expression` 按 intent 分支展示下载 |
| 官方人机交互示例 | 播报(`interactions`) → 补参(`type_input`) → 授权(`type_select`) |

### 全局文件作为长期记忆（新增）

参考样例：`examples/global-file-memory-keypoints.md`（`Demo_全局文件作为记忆存储媒介的方案`）

核心设计：

1. 在 `private_envs` 中声明文件类型变量（如 `知识积累`）
2. 模型节点输出结构化意图：`intent` / `userInput` / `answerOutput`
3. 代码节点通过 `FileBucket.get/put` 读写同一个文件 ID
4. 当 `intent == 1` 执行“清空记忆（保留标题）”
5. 正常写入时追加 Markdown 条目，并清理 7 天前记录
6. 插件节点再次读取文件内容，作为 `output_expression` 对外展示

记忆写入条目模板：

```markdown
### 📝 插入时间：2026-06-30 16:55:00

**用户输入：** ...

**问题回复：** ...
```

注意事项：

- 环境文件变量默认不会被系统回收，适合跨次运行积累
- 复制智能体后务必替换为租户自己的环境文件，避免写入串扰

## 交互节点 `act_interaction`

### 信息传递（消息）

- 对外（UI/API）输出参数与状态
- 输出参数可多选；API 可读取这些值

### 用户交互（问答）

| 类型 | 说明 |
|------|------|
| 填空 | 自由文本 |
| 单选 | 选项 |
| 多选 | 多选项 |

问题内容支持：输入 / 引用 / 渲染（Go 模板）。

**默认问答超时：60 秒**（后续版本计划可配置）。

### 案例

| 案例 | 场景 |
|------|------|
| 班级信息收集 | 交互问答 + 历史累加 |
| 历史对话记录 | 消息传递 |
| 排班智能体 | 交互收集约束 |
| 日志解析规则智能体 | 交互确认 |

### 路线图（PDF 透露）

- 交互节点支持上传文件
- 可配置交互超时时长

## 交互节点 JSON

完整结构见 [reference/interaction-node.md](../reference/interaction-node.md)。  
实战样例：[examples/class-info-keypoints.md](../examples/class-info-keypoints.md)（node2 关键片段）。