# -*- coding: utf-8 -*-
"""恒脑开放接口 Phase 0 冒烟实测脚本 v2（POST 方法）
用法: python scripts/phase0_smoke_test.py
凭据: 读取 deploy/.env（不落代码、不入库）
"""
import base64
import hashlib
import hmac
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://www.das-ai.com"
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", ".env")


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
    timestamp = int(time.time() * 1000)
    data = "{}\n{}\n{}".format(timestamp, secret, key)
    digest = hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest()
    return "{}{}".format(timestamp, base64.b64encode(digest).decode())


def call(path, payload, key, secret):
    sign = get_sign(key, secret)
    url = BASE + path
    headers = {"appKey": key, "sign": sign, "Content-Type": "application/json"}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, "ERR: {}".format(e)


def show(name, result):
    status, body = result
    print("=" * 60)
    print("[{}] HTTP {}".format(name, status))
    try:
        parsed = json.loads(body)
        print(json.dumps(parsed, ensure_ascii=False, indent=2)[:3000])
    except Exception:
        print(body[:2000])


def main():
    env = load_env(ENV_PATH)
    key = env.get("HENGNAO_APP_KEY", "")
    secret = env.get("HENGNAO_APP_SECRET", "")
    if not key or not secret:
        print("缺少凭据：请先填写 deploy/.env")
        return 1

    print("=== 恒脑开放接口 Phase 0 冒烟实测 v2（POST）===")
    print("Base URL: {}".format(BASE))

    show("agent/search POST {}", call("/open/api/v2/agent/search", {}, key, secret))
    show("agent/statistics POST {}", call("/open/api/v2/agent/statistics", {}, key, secret))
    show("agent/testCases POST {}", call("/open/api/v2/agent/testCases", {}, key, secret))
    show("agent/execute 冒烟（无智能体 id）", call("/open/api/v2/agent/execute", {"sid": "00000000-0000-0000-0000-000000000001", "id": "", "input": "ping", "stream": False}, key, secret))

    print("=" * 60)
    print("判定：认证类错误（-402/-403）→ 凭据/签名问题；业务类错误（-405/-xxx）→ 链路已通，按参数修正。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
