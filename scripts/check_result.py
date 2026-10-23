import requests, json
r = requests.get("http://127.0.0.1:8000/api/scan/82fbb452edd0").json()["data"]
print(f"status: {r['status']}")
print(f"findings: {len(r.get('findings', []))}")
print(f"tech: {r.get('tech', [])}")
for f in r.get("findings", [])[:15]:
    sev = f.get("severity", "?")
    title = f.get("title", "?")
    url = f.get("matched_at", f.get("url", "?"))
    print(f"  [{sev}] {title} @ {url}")
