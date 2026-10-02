"""Evaluate the rolling five-minute API SLO; exit 2 represents an actionable breach."""

import json
from pathlib import Path

import httpx
from e2e import environment

env = environment()
client = httpx.Client(auth=("elastic", env["ELASTIC_PASSWORD"]), timeout=20)
query = {
    "size": 0,
    "track_total_hits": True,
    "query": {
        "bool": {
            "filter": [
                {"range": {"@timestamp": {"gte": "now-5m"}}},
                {"term": {"service.name": "activation-api"}},
                {"exists": {"field": "http.response.status_code"}},
            ]
        }
    },
    "aggs": {
        "failures": {"filter": {"range": {"http.response.status_code": {"gte": 500}}}},
        "latency": {"percentiles": {"field": "event.duration", "percents": [95]}},
    },
}
response = client.post(
    f"{env.get('ELASTICSEARCH_URL', 'http://127.0.0.1:19200')}/logs-platform-local/_search",
    json=query,
)
response.raise_for_status()
data = response.json()
total = data["hits"]["total"]["value"]
failures = data["aggregations"]["failures"]["doc_count"]
p95 = data["aggregations"]["latency"]["values"]["95.0"]
report = {
    "window": "5m",
    "requests": total,
    "error_rate": failures / total if total else None,
    "p95_ms": p95 / 1_000_000 if p95 is not None else None,
    "targets": {"availability": 0.99, "p95_ms": 500},
    "status": "insufficient_traffic",
}
if total >= 20:
    report["status"] = "breach" if failures / total > 0.01 or report["p95_ms"] > 500 else "healthy"
Path("artifacts").mkdir(exist_ok=True)
Path("artifacts/slo.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
raise SystemExit(2 if report["status"] == "breach" else 0)
