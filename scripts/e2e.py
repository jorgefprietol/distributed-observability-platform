"""Verify actual cross-service trace correlation, ingestion and Kibana provisioning."""

import json
import os
import secrets
import subprocess
import time
from pathlib import Path

import httpx


def environment():
    values = dict(os.environ)
    if Path(".env").exists():
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values.setdefault(key, value)
    return values


def main():
    env = environment()
    api = env.get("API_URL", "http://127.0.0.1:18090")
    es = env.get("ELASTICSEARCH_URL", "http://127.0.0.1:19200")
    kb = env.get("KIBANA_URL", "http://127.0.0.1:15601")
    client = httpx.Client(timeout=10)
    admin = httpx.Client(auth=("elastic", env["ELASTIC_PASSWORD"]), timeout=20)
    assert client.get(f"{es}/_cluster/health").status_code == 401
    assert client.post(f"{api}/api/v1/activations", json={}).status_code == 401
    ingest = httpx.Client(auth=("telemetry_ingest", env["INGEST_PASSWORD"]), timeout=10)
    assert ingest.get(f"{es}/_security/user").status_code == 403
    traces = {}
    results = {}
    subprocess.run(
        ["docker", "compose", "exec", "-T", "activation-api", "python", "scripts/seed-access.py"],
        check=True,
    )
    for scenario in ("normal", "latency", "failure"):
        trace_id = secrets.token_hex(16)
        traces[scenario] = trace_id
        headers = {
            "Authorization": f"Bearer {env['API_TOKEN']}",
            "Idempotency-Key": f"e2e-{secrets.token_hex(12)}",
            "traceparent": f"00-{trace_id}-1234567890abcdef-01",
        }
        body = {"plan": "fiber-100", "region": "north", "scenario": scenario}
        started = time.monotonic()
        response = client.post(f"{api}/api/v1/activations", json=body, headers=headers)
        elapsed = time.monotonic() - started
        expected = 503 if scenario == "failure" else 201
        assert response.status_code == expected, (scenario, response.status_code, response.text)
        assert response.headers["X-Trace-ID"] == trace_id
        if scenario == "latency":
            assert elapsed >= 0.35
        repeat = client.post(f"{api}/api/v1/activations", json=body, headers=headers)
        assert repeat.status_code == expected
        assert repeat.json() == response.json()
        assert repeat.headers["Idempotency-Replayed"] == "true"
        results[scenario] = {
            "status": response.status_code,
            "trace_id": trace_id,
            "activation_id": response.json()["activation_id"],
        }

    def search(index, query):
        response = admin.post(f"{es}/{index}/_search", json={"size": 100, "query": query})
        response.raise_for_status()
        return response.json()["hits"]["hits"]

    deadline = time.monotonic() + 480
    last = {}
    while time.monotonic() < deadline:
        try:
            spans = search("traces-apm*", {"term": {"trace.id": traces["normal"]}})
            logs = search("logs-platform-local", {"term": {"trace.id": traces["normal"]}})
            errors = search(
                "logs-platform-local",
                {
                    "bool": {
                        "filter": [
                            {"term": {"trace.id": traces["failure"]}},
                            {"term": {"log.level": "error"}},
                        ]
                    }
                },
            )
            access = search("logs-access-local", {"exists": {"field": "http.response.status_code"}})
            infra = search(
                "metrics-infrastructure-*", {"exists": {"field": "system.cpu.total.norm.pct"}}
            )
            application = search("metrics-apm*", {"term": {"service.name": "activation-api"}})
            geo = search("logs-access-local", {"exists": {"field": "source.geo.location"}})
            quarantine = search(
                "logs-quarantine-local",
                {
                    "match": {
                        "message": "synthetic-malformed-access-fixture",
                    }
                },
            )
            span_services = {hit["_source"]["service"]["name"] for hit in spans}
            log_services = {hit["_source"]["service"]["name"] for hit in logs}
            last = {
                "span_services": sorted(span_services),
                "log_services": sorted(log_services),
                "errors": len(errors),
                "access_events": len(access),
                "infrastructure_metrics": len(infra),
                "application_metrics": len(application),
            }
            assert span_services == {"activation-api", "inventory", "provisioning"}
            assert log_services == span_services
            assert errors and access and infra and application and geo and quarantine
            assert any("platform.http" in json.dumps(hit["_source"]) for hit in application)
            failure_spans = search(
                "traces-apm*",
                {
                    "bool": {
                        "filter": [
                            {"term": {"trace.id": traces["failure"]}},
                            {"term": {"event.outcome": "failure"}},
                        ]
                    }
                },
            )
            assert failure_spans
            break
        except (httpx.HTTPError, AssertionError, KeyError):
            time.sleep(5)
    else:
        raise RuntimeError(f"Telemetry not complete: {last}")
    dashboard = admin.get(f"{kb}/api/saved_objects/dashboard/platform-overview")
    dashboard.raise_for_status()
    assert len(json.loads(dashboard.json()["attributes"]["panelsJSON"])) == 6
    policy = admin.get(f"{es}/logs-platform-local/_ilm/explain")
    policy.raise_for_status()
    assert all(value.get("managed") for value in policy.json()["indices"].values())
    views = admin.get(f"{kb}/api/data_views")
    views.raise_for_status()
    assert {"platform-logs", "platform-traces", "platform-metrics"} <= {
        view["id"] for view in views.json()["data_view"]
    }
    Path("artifacts").mkdir(exist_ok=True)
    evidence = {
        "result": "passed",
        "scenarios": results,
        "ingestion": last,
        "dashboard_panels": 6,
        "retention": "managed",
        "security": "verified",
        "geoip": "verified_synthetic_fixture",
        "quarantine": "verified_malformed_fixture",
    }
    Path("artifacts/e2e.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
