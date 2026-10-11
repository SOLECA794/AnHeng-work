# 关键片段：代码节点调用开放接口

> 完整说明见 [reference/open-apis.md](../reference/open-apis.md)。  
> **HOST**：沙箱环境变量 `OPENAPI_HOST`（`os.environ["OPENAPI_HOST"]`）。  
> **入参**：一律 `args.input.<名>`，禁止 `args.<名>`（见 [code-node-args-input-keypoints.md](code-node-args-input-keypoints.md)）。  
> 密钥一律 `private_envs` / 入参，禁止写死。

## 最小模板：HMAC + 调智能体

```python
from runtime import Args
import os, hmac, hashlib, base64, time, uuid, requests

def get_sign(key, secret):
    ts = int(time.time() * 1000)
    data = f"{ts}\n{secret}\n{key}"
    sign = hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest()
    return f"{ts}{base64.b64encode(sign).decode()}"

def handler(args: Args) -> dict:
    base = os.environ["OPENAPI_HOST"].rstrip("/")
    app_key = args.input.app_key
    app_secret = args.input.app_secret
    headers = {
        "appKey": app_key,
        "sign": get_sign(app_key, app_secret),
        "Content-Type": "application/json",
    }
    sid = str(uuid.uuid4())
    resp = requests.post(
        f"{base}/open/api/v2/agent/execute",
        headers=headers,
        json={
            "sid": sid,
            "id": args.input.agent_id,
            "inputs": {"input": args.input.query},
            "stream": False,
        },
        timeout=300,
    )
    body = resp.json()
    return {
        "code": body.get("code"),
        "sid": sid,
        "results": (body.get("data") or {}).get("results"),
        "raw": body,
    }
```

节点入参声明：`app_key`、`app_secret`、`agent_id`、`query`（或从 `private_envs` 引用）。**不要**再声明 `openapi_base`。

## 最小模板：OpenAI 兼容对话

```python
from runtime import Args
from openai import OpenAI

def handler(args: Args) -> dict:
    client = OpenAI()
    resp = client.chat.completions.create(
        model="HengNao-v4",
        temperature=0.7,
        max_tokens=1024,
        top_p=1,
        extra_body={"top_k": -1},
        messages=[{"role": "user", "content": str(args.input.prompt)}],
    )
    return {"text": resp.choices[0].message.content}
```

显式 LLMKey（一般仍不必手写 host；若必须）：

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=f"{os.environ['OPENAPI_HOST'].rstrip('/')}/api/openai/v1",
    api_key=args.input.llm_key,
)
```

> 说明：大模型正式路径为能力中心 `/api/openai/v1/chat/completions`；容器内无参 `OpenAI()` 为推荐写法。仅当平台确认 LLM 与 OpenAPI 同 host 时，才用上面的 `OPENAPI_HOST` 拼接。

## 最小模板：知识原文检索

```python
from runtime import Args
import os, hmac, hashlib, base64, time, requests

def get_sign(key, secret):
    ts = int(time.time() * 1000)
    data = f"{ts}\n{secret}\n{key}"
    sign = hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest()
    return f"{ts}{base64.b64encode(sign).decode()}"

def handler(args: Args) -> dict:
    base = os.environ["OPENAPI_HOST"].rstrip("/")
    headers = {
        "appKey": args.input.app_key,
        "sign": get_sign(args.input.app_key, args.input.app_secret),
        "Content-Type": "application/json",
    }
    labels = getattr(args.input, "labels", None)
    body = {"query": args.input.query, "size": int(getattr(args.input, "size", 10) or 10)}
    if labels:
        body["labels"] = labels if isinstance(labels, list) else [labels]
    data = requests.post(
        f"{base}/open/api/v2/document/retrieve",
        headers=headers,
        json=body,
        timeout=120,
    ).json()
    chunks = data.get("data") or []
    texts = [c.get("chunkContent", "") for c in chunks]
    return {"code": data.get("code"), "chunks": chunks, "joined": "\n\n".join(texts)}
```

## FileBucket 与开放上传的区别

| 场景 | API |
|------|-----|
| 本编排内读写会话文件 | `FileBucket().get` / `save` / `put` |
| 调 Agent execute 传文件参数 | 先 `POST {OPENAPI_HOST}/open/api/v2/agent/file/upload`，把返回的 fileId 写入 `inputs` |
| 知识库入库 | `POST .../document/upload` + 轮询 `task/status` |

```python
fb = FileBucket()
data = fb.get(args.input.filename)
out = fb.save("out.bin", data)
return {"file": out}
```

## 选型提醒

- Host 固定读 **`os.environ["OPENAPI_HOST"]`**，密钥另传。  
- 编排内已有知识插件 / 子智能体节点时，不要无故改成 HTTP。  
- 代码节点流式 SSE 成本高，开放接口优先 `stream=false`。  
- `flag==2` / `-402`：检查机器时间与签名，不要死循环重试。
