# -*- coding: utf-8 -*-
"""拉取恒脑已发布智能体清单（供编排引用参考）"""
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


def call(path, payload, key, secret):
    sign = get_sign(key, secret)
    headers = {"appKey": key, "sign": sign, "Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"code": e.code, "msg": e.read().decode("utf-8", "replace")[:200]}
    except Exception as e:
        return {"code": -1, "msg": str(e)}


def main():
    env = load_env(ENV_PATH)
    key = env.get("HENGNAO_APP_KEY", "")
    secret = env.get("HENGNAO_APP_SECRET", "")
    agents = []
    for page in range(1, 6):  # 拉 5 页
        r = call("/open/api/v2/agent/search", {"page": page, "size": 50}, key, secret)
        if r.get("code") != 0:
            print("page {} failed: {}".format(page, r.get("msg")))
            break
        data = r.get("data", {})
        rows = data.get("data", [])
        if not rows:
            break
        for a in rows:
            agents.append({"id": a.get("id"), "name": a.get("name"), "publisher": a.get("publisher"), "scene": (a.get("tags") or {}).get("scene", []), "callNum": a.get("callNum"), "introduce": (a.get("introduce") or "")[:80]})
        print("page {}: {} agents, total={}".format(page, len(rows), data.get("total")))
    out = r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\08-平台已发布智能体参考清单.json"
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)
    print("saved {} agents -> {}".format(len(agents), out))
    # 打印安全相关
    kw = ["安全", "情报", "IoC", "告警", "邮件", "分类", "分级", "扫描", "漏洞", "巡检", "渗透", "Web", "网站", "敏感", "泄露"]
    hits = [a for a in agents if any(k in a["name"] for k in kw)]
    print("--- 安全相关（前 40） ---")
    for a in hits[:40]:
        print("- {} | {} | call={} | {}".format(a["name"], a["id"], a["callNum"], a["scene"]))


if __name__ == "__main__":
    sys.exit(main())
