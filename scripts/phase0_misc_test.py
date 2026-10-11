# -*- coding: utf-8 -*-
"""T04/T05 补测：文件上传下载链路 + Chatbot iframe token
用法: python scripts/phase0_misc_test.py
"""
import base64, hashlib, hmac, io, json, os, sys, time, uuid, urllib.error, urllib.request

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


def post_json(path, payload, key, secret, timeout=30):
    sign = get_sign(key, secret)
    headers = {"appKey": key, "sign": sign, "Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, "ERR: {}".format(e)


def post_multipart(path, file_path, key, secret, timeout=60):
    """构造 multipart/form-data 上传文件"""
    sign = get_sign(key, secret)
    boundary = "----hengnao{}".format(int(time.time()))
    with io.open(file_path, "rb") as f:
        file_bytes = f.read()
    filename = os.path.basename(file_path)
    body = []
    body.append("--{}\r\n".format(boundary).encode())
    body.append('Content-Disposition: form-data; name="file"; filename="{}"\r\n'.format(filename).encode("utf-8"))
    body.append(b"Content-Type: application/octet-stream\r\n\r\n")
    body.append(file_bytes)
    body.append(b"\r\n--" + boundary.encode() + b"--\r\n")
    data = b"".join(body)
    headers = {"appKey": key, "sign": sign, "Content-Type": "multipart/form-data; boundary={}".format(boundary)}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, "ERR: {}".format(e)


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
    # 确保样例文件存在
    if not os.path.exists(SAMPLE):
        with io.open(SAMPLE, "w", encoding="utf-8") as f:
            f.write("敏感信息定义样例（测试用，勿外传）\n手机号: 1[3-9]\\d{9}\n身份证: \\d{17}[0-9Xx]\n内网地址: 10\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\n示例值: 13812345678 / 110101199003077777 / 10.10.10.1\n")
    print("=== T04/T05 补测 ===")

    # T05: Chatbot iframe token
    show("assistants/token", post_json("/open/api/assistants/token", {"userId": "test-user-001"}, key, secret), r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\09-assistants-token.json")

    # T04: 文件上传（multipart）
    show("agent/file/upload", post_multipart("/open/api/v2/agent/file/upload", SAMPLE, key, secret), r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\09-file-upload.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
