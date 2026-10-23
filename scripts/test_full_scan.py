import requests, json, time

# 触发完整巡检
r = requests.post("http://127.0.0.1:8000/api/scan", json={
    "target_url": "http://127.0.0.1:8080",
    "run_nuclei": True
})
data = r.json()["data"]
task_id = data["task_id"]
print(f"任务已提交: {task_id}, 初始状态: {data['status']}")

# 轮询
for i in range(60):
    time.sleep(5)
    t = requests.get(f"http://127.0.0.1:8000/api/scan/{task_id}").json()["data"]
    status = t["status"]
    tech = t.get("tech", [])
    findings_count = len(t.get("findings", []))
    print(f"[{i*5}s] status={status}, tech={tech}, findings={findings_count}")
    if status in ("done", "failed", "timeout"):
        break

# 打印完整结果
print("\n=== 完整结果 ===")
print(json.dumps(t, indent=2, ensure_ascii=False, default=str))
