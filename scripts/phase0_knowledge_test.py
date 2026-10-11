# -*- coding: utf-8 -*-
"""T06 补测：知识库接口链路（document/upload → task/status → retrieve）
用法: python scripts/phase0_knowledge_test.py
"""
import base64, hashlib, hmac, io, json, os, sys, time, urllib.error, urllib.request

BASE = "https://www.das-ai.com"
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", ".env")
SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", "恒脑平台文档", "sample_sensitive_rules.txt")


def load_env(path):
    env = {}
    if os.path.exists(path):
        with io.open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def get_sign(key, secret):
    ts = int(time.time() * 1000)
    data = "{}\n{}\n{}".format(ts, secret, key)
    digest = hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest()
    return "{}{}".format(ts, base64.b64encode(digest).decode())


def request(path, method, headers, data=None, timeout=60):
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, "ERR: {}".format(e)


def post_json(path, payload, key, secret):
    sign = get_sign(key, secret)
    headers = {"appKey": key, "sign": sign, "Content-Type": "application/json"}
    return request(path, "POST", headers, json.dumps(payload).encode("utf-8"))


def post_multipart(path, file_path, extra_fields, key, secret):
    sign = get_sign(key, secret)
    boundary = "----hengnao{}".format(int(time.time()))
    with io.open(file_path, "rb") as f:
        file_bytes = f.read()
    filename = os.path.basename(file_path)
    parts = []
    for k, v in extra_fields.items():
        parts.append("--{}\r\n".format(boundary).encode())
        parts.append('Content-Disposition: form-data; name="{}"\r\n\r\n'.format(k).encode("utf-8"))
        parts.append(str(v).encode("utf-8"))
        parts.append(b"\r\n")
    parts.append("--{}\r\n".format(boundary).encode())
    parts.append('Content-Disposition: form-data; name="file"; filename="{}"\r\n'.format(filename).encode("utf-8"))
    parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
    parts.append(file_bytes)
    parts.append(b"\r\n--" + boundary.encode() + b"--\r\n")
    headers = {"appKey": key, "sign": sign, "Content-Type": "multipart/form-data; boundary={}".format(boundary)}
    return request(path, "POST", headers, b"".join(parts))


def get_json(path, key, secret):
    sign = get_sign(key, secret)
    headers = {"appKey": key, "sign": sign, "Content-Type": "application/json"}
    return request(path, "GET", headers)


def show(tag, result, save=None):
    status, body = result
    print("=" * 60)
    print("[{}] HTTP {}".format(tag, status))
    if save:
        with io.open(save, "w", encoding="utf-8") as f:
            f.write(body)
    print(body[:2000])


def main():
    env = load_env(ENV_PATH)
    key = env.get("HENGNAO_APP_KEY", "")
    secret = env.get("HENGNAO_APP_SECRET", "")
    if not key or not secret:
        print("缺少凭据")
        return 1
    if not os.path.exists(SAMPLE):
        print("样例文件不存在:", SAMPLE)
        return 1
    print("=== T06 知识库接口链路实测 ===")

    # 1) upload（异步任务）
    r = post_multipart("/open/api/v2/document/upload", SAMPLE, {"name": "敏感信息定义样例-测试", "description": "T06 实测用"}, key, secret)
    show("document/upload", r, r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\09-document-upload.json")
    task_id = None
    try:
        d = json.loads(r[1]).get("data")
        task_id = d if isinstance(d, str) else (d.get("taskId") if isinstance(d, dict) else None)
    except Exception:
        task_id = None
    print("taskId:", task_id)
    if not task_id:
        return 1

    # 2) task/status 轮询
    for i in range(10):
        time.sleep(3)
        rs = get_json("/open/api/v2/document/task/status?taskId={}".format(task_id), key, secret)
        print("轮询 {}: {}".format(i + 1, rs[1][:300]))
        try:
            code = json.loads(rs[1]).get("code")
            if code != 0:
                break
        except Exception:
            break
    show("document/task/status", rs, r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\09-document-status.json")

    # 3) retrieve（RAG 检索验证）
    rr = post_json("/open/api/v2/document/retrieve", {"query": "手机号正则定义", "top_k": 3}, key, secret)
    show("document/retrieve", rr, r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\09-document-retrieve.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
