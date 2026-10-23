import requests, json
r = requests.post("http://127.0.0.1:8000/api/scan", json={"target_url": "http://127.0.0.1:8080", "run_nuclei": False})
print(json.dumps(r.json(), indent=2, ensure_ascii=False))
