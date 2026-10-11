# -*- coding: utf-8 -*-
"""查询智能体详情（inputParameters 定义），支撑 execute 正确传参"""
import base64, hashlib, hmac, io, json, os, sys, time, urllib.error, urllib.request

BASE = "https://www.das-ai.com"
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", ".env")
AGENT_ID = sys.argv[1] if len(sys.argv) > 1 else "c0875de5-8b0b-4fe7-a084-24ee69447fb7"


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


def call(path, payload, key, secret):
    sign = get_sign(key, secret)
    headers = {"appKey": key, "sign": sign, "Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"code": e.code, "msg": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def main():
    env = load_env(ENV_PATH)
    key = env.get("HENGNAO_APP_KEY", "")
    secret = env.get("HENGNAO_APP_SECRET", "")
    r = call("/open/api/v2/agent/search", {"page": 1, "size": 10}, key, secret)
    # 在返回里找目标 agent 详情（search 支持 id 过滤则更优，先遍历前若干条）
    found = None
    for page in range(1, 6):
        r = call("/open/api/v2/agent/search", {"page": page, "size": 50}, key, secret)
        if r.get("code") != 0:
            break
        for a in (r.get("data") or {}).get("data", []):
            if a.get("id") == AGENT_ID:
                found = a
                break
        if found:
            break
    if not found:
        print("未找到 agent {}".format(AGENT_ID))
        return 1
    print("=== {} ===".format(found.get("name")))
    print("prologue:", (found.get("prologue") or "")[:500])
    print("inputParameters:", json.dumps(found.get("inputParameters"), ensure_ascii=False, indent=2))
    print("outputParameters:", json.dumps(found.get("outputParameters"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
