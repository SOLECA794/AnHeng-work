# -*- coding: utf-8 -*-
"""execute 流式（SSE）实测：验证对话主通道消息结构
用法: python scripts/phase0_stream_test.py ["输入文本"]
"""
import base64, hashlib, hmac, io, json, os, sys, time, uuid, urllib.error, urllib.request

BASE = "https://www.das-ai.com"
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", ".env")
AGENT_ID = "c5844d4c-4913-4c70-b761-d290db930275"  # 恶意IoC查询
INPUT = sys.argv[1] if len(sys.argv) > 1 else "查询 45.155.204.46 是否恶意"


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


def main():
    env = load_env(ENV_PATH)
    key = env.get("HENGNAO_APP_KEY", "")
    secret = env.get("HENGNAO_APP_SECRET", "")
    if not key or not secret:
        print("缺少凭据")
        return 1
    payload = {"sid": str(uuid.uuid4()), "id": AGENT_ID, "inputs": {"input": INPUT}, "stream": True, "debugSwitch": False}
    sign = get_sign(key, secret)
    headers = {"appKey": key, "sign": sign, "Content-Type": "application/json"}
    req = urllib.request.Request(BASE + "/open/api/v2/agent/execute", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    print("=== execute 流式（SSE）实测 ===")
    print("input:", INPUT)
    t0 = time.time()
    lines_out = []
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            print("HTTP", resp.status, "Content-Type:", resp.headers.get("Content-Type"))
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    lines_out.append(line)
                    print(line[:300])
    except Exception as e:
        print("ERR:", e)
    print("耗时 {:.2f}s，共 {} 行".format(time.time() - t0, len(lines_out)))
    with io.open(r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\_stream_raw.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out))
    # 统计消息类型
    from_types = {}
    for ln in lines_out:
        if '"from"' in ln:
            try:
                obj = json.loads(ln[5:] if ln.startswith("data:") else ln)
                ft = obj.get("data", {}).get("from") or obj.get("from")
                from_types[ft] = from_types.get(ft, 0) + 1
            except Exception:
                pass
    print("消息来源统计:", from_types)
    return 0


if __name__ == "__main__":
    sys.exit(main())
