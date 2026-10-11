# 关键片段：代码节点入参必须走 `args.input`

> 实测事故（2026-08-21，gc.das-ai.com）：`AttributeError: 'Args' object has no attribute 'image'`  
> 根因：把节点入参写成了 `args.image`，正确为 **`args.input.image`**。

## 硬规则

代码节点 `handler(args: Args)` 中：

| 写法 | 结果 |
|------|------|
| `args.input.<参数名>` | ✅ 读取本节点 `input_parameters` 绑定值 |
| `args.<参数名>` | ❌ `AttributeError: 'Args' object has no attribute '...'` |
| `args.input.kv_user.<名>` | 仅当代码模板/插件执行场景显式约定；**本节点已声明 input_parameters 时不要用** |

文件入参同样：`FileBucket().get(args.input.image)`，不是 `args.image`。

## 错误 → 正确

```python
# ❌ 错误（会报 Args 没有 image）
file_id, file_name = parse_image_reference(args.image)

# ✅ 正确
file_id, file_name = parse_image_reference(args.input.image)
```

其它字段同理：`args.input.query`、`args.input.app_key`、`args.input.filename`。

## 图片 Vision 最小正确骨架

节点 `input_parameters` 声明 `image`（file）并 `reference` → `$.kv_user.image` 后：

```python
from runtime import Args
import base64
import mimetypes
from openai import OpenAI

ALLOWED = {"jpg", "jpeg", "png", "bmp", "webp", "gif"}

def parse_image_reference(reference):
    if isinstance(reference, dict):
        file_id = reference.get("file_id") or reference.get("id") or reference.get("url")
        file_name = str(reference.get("file_name") or reference.get("name") or "image.jpg")
        return file_id, file_name
    file_id = str(reference or "")
    file_name = file_id[5:] if file_id.startswith("file:") else file_id
    file_name = file_name.split("<", 1)[0] or "image.jpg"
    return file_id, file_name

def handler(args: Args) -> dict:
    file_id, file_name = parse_image_reference(args.input.image)
    if not file_id:
        raise ValueError("请上传有效的图片文件。")

    extension = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if extension not in ALLOWED:
        raise ValueError("仅支持 jpg、jpeg、png、bmp、webp、gif。")

    image_bytes = FileBucket().get(file_id)
    if isinstance(image_bytes, str):
        image_bytes = image_bytes.encode("utf-8")

    b64 = base64.b64encode(image_bytes).decode("ascii")
    mime = mimetypes.guess_type(file_name)[0] or "image/jpeg"
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    data_uri = f"data:{mime};base64,{b64}"

    client = OpenAI()
    resp = client.chat.completions.create(
        model="HengNao-v4",
        temperature=0,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": "提取图片文字并总结，输出 Markdown：## 图片文本 / ## 图片总结"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请提取文字并总结。"},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
    )
    return {
        "result": resp.choices[0].message.content,
        "image_name": file_name,
    }
```

返回 dict 的 key 还不够：节点 **`output_parameters` 必须为每个字段配完整 `value`（jsonpath + text → result_key）**，否则 `{{$.node_vision.result}}` 为空。见 [output-parameters-value-keypoints.md](output-parameters-value-keypoints.md)。

```json
"output_parameters": [
  {
    "name": "result",
    "type": "string",
    "description": "图片文字提取与总结结果",
    "value": {
      "type": "jsonpath",
      "expr": "$.result",
      "text": { "type": "reference", "expr": "$.result_key" }
    }
  },
  {
    "name": "image_name",
    "type": "string",
    "description": "解析后的图片文件名",
    "value": {
      "type": "jsonpath",
      "expr": "$.image_name",
      "text": { "type": "reference", "expr": "$.result_key" }
    }
  }
]
```

根：`"output_expression": { "type": "rendering", "expr": "{{$.node_vision.result}}" }`。

## 生成自检

写任何 `act_interpreter` 代码时：

1. 全文搜索 `args.`，除 `args.input` / `args: Args` 外不应直接访问业务字段  
2. 每个 `input_parameters[].name` 在代码里对应 `args.input.<name>`  
3. 文件：`FileBucket().get(args.input.<fileParam>)`  
4. Host：`os.environ["OPENAPI_HOST"]`（调开放接口时）  
5. 每个 `output_parameters[]` 有完整 `value`；`return` key ↔ jsonpath ↔ 下游引用对齐  

完整范式见 [reference/open-apis.md](../reference/open-apis.md) §1、[docs/03-params-iteration.md](../docs/03-params-iteration.md)。
