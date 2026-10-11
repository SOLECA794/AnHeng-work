# -*- coding: utf-8 -*-
"""execute 实测 v2：inputs 对象传参；测恶意IoC查询智能体（参数仅 input）
用法: python scripts/phase0_execute_test.py [agent_id] ["输入文本"]
"""
import base64, hashlib, hmac, io, json, os, sys, time, uuid, urllib.error, urllib.request

BASE = "https://www.das-ai.com"
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", ".env")
DEFAULT_AGENT_ID = "c5844d4c-4913-4c70-b761-d290db930275"  # 恶意IoC查询智能体
DEFAULT_INPUT = "查询 83.171.237.173 是否是恶意 IP，以及 martin27.xyz 域名的威胁情报"


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


def call(payload, key, secret, timeout=90):
    sign = get_sign(key, secret)
    headers = {"appKey": key, "sign": sign, "Content-Type": "application/json"}
    req = urllib.request.Request(BASE + "/open/api/v2/agent/execute", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), time.time() - t0
    except Exception as e:
        return None, "ERR: {}".format(e), time.time() - t0


def main():
    env = load_env(ENV_PATH)
    key = env.get("HENGNAO_APP_KEY", "")
    secret = env.get("HENGNAO_APP_SECRET", "")
    if not key or not secret:
        print("缺少凭据")
        return 1
    agent_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_AGENT_ID
    user_input = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_INPUT
    payload = {"sid": str(uuid.uuid4()), "id": agent_id, "inputs": {"input": user_input}, "stream": False, "debugSwitch": False}
    print("=== execute v2 实测 ===")
    print("agent_id:", agent_id)
    print("input:", user_input[:150])
    status, body, elapsed = call(payload, key, secret)
    print("HTTP {} 耗时 {:.2f}s".format(status, elapsed))
    out = r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\_execute_ioc_raw.json"
    with io.open(out, "w", encoding="utf-8") as f:
        f.write(body)
    try:
        parsed = json.loads(body)
        print("code:", parsed.get("code"), "| msg:", parsed.get("msg"), "| flag:", parsed.get("flag"))
        data = parsed.get("data")
        if isinstance(data, dict):
            print("data keys:", list(data.keys()))
            print("session:", data.get("session"))
            print("results:", json.dumps(data.get("results"), ensure_ascii=False)[:4000])
            print("token_usage:", data.get("token_usage"))
        else:
            print(json.dumps(parsed, ensure_ascii=False, indent=2)[:3000])
    except Exception:
        print(body[:4000])
    print("原始返回已存:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
