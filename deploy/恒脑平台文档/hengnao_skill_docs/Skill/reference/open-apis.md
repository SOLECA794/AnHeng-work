# 开放服务接口（代码节点可调用）

> 来源：`Prompts/智能体服务-开放服务接口.pdf`、`知识检索与创作-开放服务接口.pdf`、`大模型问答-开放服务接口.pdf`（v1.5，2026-07-21）  
> 用途：在 `act_interpreter` 代码节点中按需调用平台能力。生成脚本时把密钥放进 `private_envs` / 节点入参，**禁止硬编码** appSecret / LLMKey。

## 何时用开放接口 vs 编排节点

| 需求 | 优先方式 |
|------|----------|
| 本智能体内调模型 / 知识库插件 / 子智能体 | 编排节点（`llm_mixup` / `act_tool` / `act_agent`） |
| 代码里二次加工、批量、自定义协议、调用**其它**已发布智能体 | 代码节点 + 开放接口 |
| 仅本地算文件 / Excel / 正则 | 代码节点 + 预装库，不必走开放接口 |

`{BASE}` = **代码沙箱系统环境变量 `OPENAPI_HOST`**（平台内置，无尾斜杠；对应「接口认证信息」中的开放接口地址）。  
`{LLM_HOST}` = 能力中心域名（与 Web 一致），大模型路径为 `/api/openai/v1/...`；容器内 `OpenAI()` 通常已注入，一般不必手写 host。

### 获取开放接口 HOST（必读）

代码节点沙箱已注入环境变量 **`OPENAPI_HOST`**，调用 `/open/api/v2/...` 时**优先从系统环境变量读取**，不要让用户再填一遍 base URL，也不要硬编码：

```python
import os

base = os.environ["OPENAPI_HOST"].rstrip("/")
# 或缺省兜底（仅调试）：
# base = os.environ.get("OPENAPI_HOST", "").rstrip("/")
```

---

## 1) 代码节点基础范式（必读）

### 入参路径硬规则（高频事故）

**所有**节点 `input_parameters` 只能通过 **`args.input.<参数名>`** 读取。

```python
# ✅ 正确
img = args.input.image
q = args.input.query

# ❌ 错误 → AttributeError: 'Args' object has no attribute 'image'
img = args.image
```

实测（2026-08-21）：图片 Vision 代码节点写 `args.image` 导致执行失败。关键片段见 [code-node-args-input-keypoints.md](../examples/code-node-args-input-keypoints.md)。

代码节点若开启迭代：在 `iteration_values[].dest_name` 定义变量后，代码里直接 `args.input.<dest_name>`，不要在 `input_parameters` 重复声明同名参数（见 [iteration-required-keypoints.md](../examples/iteration-required-keypoints.md)）。

```python
from runtime import Args

def handler(args: Args) -> dict:
    # 入参：只能 args.input.<变量名>
    a = args.input.a

    # 读文件（file 类型入参同样在 args.input 下）
    fb = FileBucket()
    filename = args.input.filename
    byte_stream = fb.get(filename)

    # 写文件（推荐 save，返回平台 file id）
    out_id = fb.save("results.xlsx", binary_data)

    # 临时本地文件（进程内）
    with open("1.txt", "w") as f:
        f.write("...")
    with open("1.txt", "r") as f:
        result = f.readline()

    return {"ok": True, "file": out_id}
```

### 调大模型 — 方法一（推荐，OpenAI SDK）

代码容器内 `OpenAI()` 通常已注入平台默认 endpoint / key；若需显式指定，用 `base_url` + `api_key=LLMKey`。

```python
from openai import OpenAI

def handler(args: Args) -> dict:
    client = OpenAI()  # 或 OpenAI(base_url=f"{host}/api/openai/v1", api_key=args.input.llm_key)
    resp = client.chat.completions.create(
        model="HengNao-v4",
        temperature=0.7,
        max_tokens=1024,
        top_p=1,
        extra_body={"top_k": -1},
        messages=[{"role": "user", "content": args.input.prompt}],
    )
    return {"text": resp.choices[0].message.content}
```

### 调大模型 — 方法二（LangChain）

```python
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

def handler(args: Args) -> dict:
    llm = ChatOpenAI(model="HengNao-v4")
    response = llm.invoke([HumanMessage(content=args.input.prompt)])
    return {"text": response.content}
```

### 调 Agent / RAG 开放接口 — 签名头

Agent 与 RAG 共用 **appKey + sign**（不要传 appSecret）：

```python
import hmac, hashlib, base64, time, requests

def get_sign(app_key: str, app_secret: str) -> str:
    timestamp = int(time.time() * 1000)
    data = f"{timestamp}\n{app_secret}\n{app_key}"
    digest = hmac.new(app_secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).digest()
    return f"{timestamp}{base64.b64encode(digest).decode('utf-8')}"

def openapi_headers(app_key: str, app_secret: str) -> dict:
    return {
        "appKey": app_key,
        "sign": get_sign(app_key, app_secret),
        "Content-Type": "application/json",
    }
```

约定：

- **Host**：`os.environ["OPENAPI_HOST"]`（沙箱内置）；不要用 `private_envs` 再传一遍 base，除非做跨环境覆盖调试
- `app_key` / `app_secret` / `llm_key` 来自 `private_envs` 或 `args.input`（**禁止硬编码**）
- 签名约 **60 分钟**有效；`flag==2`（如 -402）多半是时间不同步，应停止盲重试
- `flag`：`0` 成功；`1` 失败可跳过；`2` 服务异常，等待后重试

密钥建议声明（不含 HOST）：

```json
"private_envs": [
  { "name": "app_key", "type": "string", "hidden": true },
  { "name": "app_secret", "type": "string", "hidden": true },
  { "name": "llm_key", "type": "string", "hidden": true }
]
```

---

## 2) 大模型问答（OpenAI 兼容）

| 项 | 值 |
|----|-----|
| 路径 | `POST {LLM_HOST}/api/openai/v1/chat/completions` |
| 鉴权 | `Authorization: Bearer <LLMKey>`（仅 Bearer，不支持 Query） |
| 标准 | OpenAI Chat Completions |

常用 body：`model`、`messages`、`temperature`、`top_p`、`max_tokens`、`stream`、`tools` / `tool_choice`、`response_format`、`structured_outputs`、`chat_template_kwargs`（如 `enable_thinking`）、`reasoning_effort`。

Vision：`messages[].content` 可为数组；`image_url.url` **只支持 Base64 / Data URI**，不支持 http(s) 外链。

纯 HTTP 备选：

```python
import requests

def chat(host, llm_key, messages, model="HengNao-v4"):
    r = requests.post(
        f"{host.rstrip('/')}/api/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {llm_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "stream": False},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
```

错误体常见平台统一 JSON（HTTP 往往仍 200）：看 `code` / `msg`；未开通 `openapiAccess`、无 LLMKey、401 鉴权失败需提示用户检查「接口认证信息」。

---

## 3) 智能体服务 `/open/api/v2/agent/*`

公共头：`appKey`、`sign`。

| 能力 | 方法 | 路径 | 要点 |
|------|------|------|------|
| 执行 | POST | `/open/api/v2/agent/execute` | Body：`sid`(UUID)、`id`(智能体ID)、`input` 或 `inputs`、`stream`、`order`、`debugSwitch`；文件先 upload 得 fileId |
| 停止 | POST | `/open/api/v2/agent/execute/stop` | `sid` |
| 查询列表 | POST | `/open/api/v2/agent/search` | `keyword`、`scope`(0全部/1官方/2非官方)、`withForbid`、`page`、`size` |
| 上传文件 | POST | `/open/api/v2/agent/file/upload` | multipart `file` → `data` 为 `file:name<uuid>` |
| 下载文件 | POST | `/open/api/v2/agent/file/download` | `{"fileId":"..."}` → 文件流 |
| 执行统计 | POST | `/open/api/v2/agent/statistics` | 分页 |
| 异步提交 | POST | `/open/api/v2/agent/task/submit` | 返回 `taskId` |
| 异步取消 | POST | `/open/api/v2/agent/task/stop` | `taskId` |
| 异步状态 | POST | `/open/api/v2/agent/task/status` | data：1运行/2完成/3取消/4失败 |
| 异步结果 | POST | `/open/api/v2/agent/task/messages` | `full` 全量/增量 |
| 交互恢复 | POST | `/open/api/v2/agent/execute/resume` | 流式遇到 `mode=question` 后提交答案 |
| 测试集 | POST | `/open/api/v2/agent/testCases` | `agentId` → `content` 可直接作 execute 的 `inputs` |

可选 Header `ext`：`child_preview:0|1;reason:0|1`（子智能体预览 / 推理是否拆到 `reasoning_content`）。

### 同步执行骨架

```python
import os, uuid, requests

def execute_agent(app_key, app_secret, agent_id, inputs, stream=False):
    base = os.environ["OPENAPI_HOST"].rstrip("/")
    sid = str(uuid.uuid4())
    r = requests.post(
        f"{base}/open/api/v2/agent/execute",
        headers=openapi_headers(app_key, app_secret),
        json={"sid": sid, "id": agent_id, "inputs": inputs, "stream": stream},
        timeout=300,
        stream=stream,
    )
    return sid, r
```

### 人机交互对接

1. `execute` 且 `stream=true`  
2. 收到 `mode="question"`（含 `message_id`、`options`、`type`）  
3. `resume`：`{"messageId":"...","inputs":{"<问题名>":{"A":"选项文案"}}}`  
4. 继续读流；再遇 question 重复

答案会落到节点结构的 `_question` / `_options` / `_answer`。

### 长任务

`task/submit` → 轮询 `task/status` 至 2/3/4 → `task/messages` 取结果（可与编排里「条件边轮询」模式类似）。

---

## 4) 知识检索与创作 `/open/api/v2/document/*`

公共头：`appKey`、`sign`。知识范围优先用 **`labels`**（与编排插件一致），勿无依据填 `permissions`。

| 能力 | 方法 | 路径 | 要点 |
|------|------|------|------|
| 预处理上传 | POST | `/open/api/v2/document/upload` | multipart：`file`、`type`(doc/faq)、`sourceId`或`fileId`、`labels`、`blockSize`(256~1024)、`separator`、`imageocr` → `taskId` |
| 任务状态 | GET | `/open/api/v2/document/task/status?taskId=` | 0未完成 / 1完成 / 2失败 |
| 删除 | POST | `/open/api/v2/document/delete` | `sourceIds` 或 `fileIds` |
| 属性更新 | POST | `/open/api/v2/document/attribute_update` | `files[]`；`labels=[]` 表示清空 |
| 内置下载 | GET | `/open/api/v2/document/internal_download?fileId=` | 文件流 |
| 元数据 | POST | `/open/api/v2/document/metadata_query` | `fileId`/`sourceId` + `verbose` |
| 列表 | POST | `/open/api/v2/document/metadata_page_query` | `page`/`size`/`range`/`verbose` |
| 检索原文 | POST | `/open/api/v2/document/retrieve` | `query`、`size`、`labels`… → chunks |
| 检索问答 | POST | `/open/api/v2/document/chat` | `query`、`stream`、`sid`、`labels`… → content + references |
| 文档创作 | POST | `/open/api/v2/document/creation` | `prompt` + `chunks[]` |
| 文档翻译 | POST | `/open/api/v2/document/translate` | `content`、`translate`∈{CN,EN,JP,FR} |
| 会话停止 | GET | `/open/api/v2/document/stop?sid=&type=` | type：0创作 / 1翻译 / 2检索问答 |

### 检索原文骨架

```python
import os, requests

def retrieve(app_key, app_secret, query, labels=None, size=10):
    base = os.environ["OPENAPI_HOST"].rstrip("/")
    body = {"query": query, "size": size}
    if labels:
        body["labels"] = labels
    r = requests.post(
        f"{base}/open/api/v2/document/retrieve",
        headers=openapi_headers(app_key, app_secret),
        json=body,
        timeout=120,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(data)
    return data.get("data") or []
```

### 上传→等到完成

```python
import os, time, requests

def upload_and_wait(app_key, app_secret, files, form_fields, poll_sec=2, timeout=300):
    base = os.environ["OPENAPI_HOST"].rstrip("/")
    # multipart 请求头仅含 appKey/sign，不要强制 Content-Type: application/json
    headers = {"appKey": app_key, "sign": get_sign(app_key, app_secret)}
    r = requests.post(
        f"{base}/open/api/v2/document/upload",
        headers=headers,
        files=files,
        data=form_fields,
        timeout=120,
    )
    task_id = r.json()["data"]["taskId"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = requests.get(
            f"{base}/open/api/v2/document/task/status",
            headers=headers,
            params={"taskId": task_id},
            timeout=30,
        ).json()["data"]
        if st == 1:
            return task_id
        if st == 2:
            raise RuntimeError("upload failed")
        time.sleep(poll_sec)
    raise TimeoutError(task_id)
```

流式接口（chat/creation/translate）在代码节点里优先 **`stream=false`**，避免 SSE 解析复杂度；需要流式时再按行读 `text/event-stream`。

---

## 5) 生成编排时的接线建议

1. 用户说「代码里调平台接口 / 调其它智能体 / 自己做 RAG 流水线」→ 读本文，生成 `act_interpreter`：Host 写死为 `os.environ["OPENAPI_HOST"]`，密钥用 `private_envs`。  
2. 用户只说「问答 / 检索」且可用官方插件 → 优先插件节点（见 [official-demos-plugins-keypoints.md](../examples/official-demos-plugins-keypoints.md)）。  
3. 文件：编排内用 `FileBucket`；开放 Agent 执行前用 **agent file/upload** 换 fileId 再塞进 `inputs`。  
4. ID：智能体 `id`、知识库 `labels` 有 catalog 用真实值，否则 `PLACEHOLDER_*`。  
5. 校验：代码优先使用 [code-node-libraries.md](code-node-libraries.md) 中的库；大模型优先 `openai` / `langchain_openai`（平台官方代码节点说明），HTTP 用 `requests`/`httpx`。

## 场景速查

| 用户说法 | 调用 |
|----------|------|
| 代码里问恒脑模型 | OpenAI SDK / `/api/openai/v1/chat/completions` |
| 代码里跑另一个智能体 | `/agent/execute` 或异步 task 系列 |
| 代码里补交互问答 | execute 流式 + `/agent/execute/resume` |
| 上传知识再检索 | document upload → status → retrieve/chat |
| 基于片段写作 / 翻译 | creation / translate |
| 上传给智能体当附件 | agent file/upload → execute `inputs` |

## 相关文档

- 文件与交互：[docs/05-files-interaction.md](../docs/05-files-interaction.md)  
- 预装库：[code-node-libraries.md](code-node-libraries.md)  
- 编排内知识库插件：[docs/02-plugins-knowledge-base.md](../docs/02-plugins-knowledge-base.md)  
- 关键片段示例：[examples/code-node-open-api-keypoints.md](../examples/code-node-open-api-keypoints.md)
