import requests, json
r = requests.get("http://127.0.0.1:8000/api/scan/00d44fa0fb72")
print(json.dumps(r.json(), indent=2, ensure_ascii=False, default=str))
