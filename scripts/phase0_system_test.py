# -*- coding: utf-8 -*-
"""T07 补测：系统信息（并发配额）+ 租户用量统计
用法: python scripts/phase0_system_test.py
"""
import base64, hashlib, hmac, io, json, os, sys, time, urllib.error, urllib.request

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
    ts = int(time.time() * 1000)
    data = "{}\n{}\n{}".format(ts, secret, key)
    digest = hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest()
    return "{}{}".format(ts, base64.b64encode(digest).decode())


def get(path, key, secret, timeout=30):
    sign = get_sign(key, secret)
    headers = {"appKey": key, "sign": sign, "Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, headers=headers, method="GET")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), time.time() - t0
    except Exception as e:
        return None, "ERR: {}".format(e), time.time() - t0


def show(tag, result, save=None):
    status, body, elapsed = result
    print("=" * 60)
    print("[{}] HTTP {} 耗时 {:.2f}s".format(tag, status, elapsed))
    if save:
        with io.open(save, "w", encoding="utf-8") as f:
            f.write(body)
    try:
        parsed = json.loads(body)
        print(json.dumps(parsed, ensure_ascii=False, indent=2)[:4000])
    except Exception:
        print(body[:2000])


def main():
    env = load_env(ENV_PATH)
    key = env.get("HENGNAO_APP_KEY", "")
    secret = env.get("HENGNAO_APP_SECRET", "")
    if not key or not secret:
        print("缺少凭据")
        return 1
    print("=== T07 补测：系统信息 / 租户用量 ===")
    show("system/info", get("/open/api/system/info", key, secret), r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\09-system-info.json")
    show("tenant/usage/statistics", get("/open/api/v2/tenant/usage/statistics", key, secret), r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\09-usage-statistics.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
