# -*- coding: utf-8 -*-
"""异步任务链路实测：task/submit → task/status → task/messages（巡检长任务主通道）
用法: python scripts/phase0_task_test.py [agent_id] ["输入文本"]
"""
import base64, hashlib, hmac, io, json, os, sys, time, uuid, urllib.error, urllib.request

BASE = "https://www.das-ai.com"
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deploy", ".env")
DEFAULT_AGENT_ID = "c5844d4c-4913-4c70-b761-d290db930275"  # 恶意IoC查询智能体（参数仅 input，成功路径验证）
DEFAULT_INPUT = "查询 83.171.237.173 是否是恶意 IP"


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


def post(path, payload, key, secret, timeout=60):
    sign = get_sign(key, secret)
    headers = {"appKey": key, "sign": sign, "Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), time.time() - t0
    except Exception as e:
        return None, "ERR: {}".format(e), time.time() - t0


def show(tag, result, save_to=None):
    status, body, elapsed = result
    print("=" * 60)
    print("[{}] HTTP {} 耗时 {:.2f}s".format(tag, status, elapsed))
    if save_to:
        with io.open(save_to, "w", encoding="utf-8") as f:
            f.write(body)
    print(body[:2500])


def main():
    env = load_env(ENV_PATH)
    key = env.get("HENGNAO_APP_KEY", "")
    secret = env.get("HENGNAO_APP_SECRET", "")
    if not key or not secret:
        print("缺少凭据")
        return 1
    agent_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_AGENT_ID
    user_input = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_INPUT
    sid = str(uuid.uuid4())

    print("=== 异步任务链路实测 ===")
    print("agent_id:", agent_id)

    # 1) submit
    submit = post("/open/api/v2/agent/task/submit", {"sid": sid, "id": agent_id, "inputs": {"input": user_input}}, key, secret)
    show("task/submit", submit, r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\_task_submit_raw.json")
    try:
        parsed_submit = json.loads(submit[1])
        data_field = parsed_submit.get("data")
        if isinstance(data_field, dict):
            task_id = data_field.get("taskId") or data_field.get("task_id")
        else:
            task_id = data_field if data_field else None
    except Exception:
        task_id = None
    if not task_id:
        print("未拿到 taskId，终止。")
        return 1
    print("taskId:", task_id)

    # 2) status 轮询（最多 12 次，间隔 5s）
    status_code = None
    for i in range(12):
        time.sleep(5)
        r = post("/open/api/v2/agent/task/status", {"taskId": task_id}, key, secret)
        try:
            st = json.loads(r[1])
            d = st.get("data")
            if isinstance(d, dict):
                status_code = d.get("status") or d.get("taskStatus")
            else:
                status_code = d
            print("轮询 {}: status={} msg={} data={}".format(i + 1, status_code, st.get("msg"), json.dumps(d, ensure_ascii=False)[:200]))
        except Exception:
            print("轮询 {}: raw={}".format(i + 1, r[1][:200]))
            continue
        if status_code in (2, 3, 4):  # 2 完成 / 3 取消 / 4 失败
            break
    print("最终 status:", status_code)

    # 3) messages（full 全量）
    msgs = post("/open/api/v2/agent/task/messages", {"taskId": task_id, "full": True}, key, secret)
    show("task/messages", msgs, r"D:\Desktop\AnHeng-work\deploy\恒脑平台文档\_task_messages_raw.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
