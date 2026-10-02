"""Idempotent installation of credentials, mappings, retention and Kibana assets."""

import json
import os
import time
from pathlib import Path

import httpx

es = os.getenv("ELASTICSEARCH_URL", "http://127.0.0.1:19200")
kb = os.getenv("KIBANA_URL", "http://127.0.0.1:15601")
client = httpx.Client(auth=("elastic", os.environ["ELASTIC_PASSWORD"]), timeout=120)


def request(method, url, **kwargs):
    response = client.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json()


def main():
    for folder in ("/logs", "/data", "/filebeat-data", "/var/lib/otelcol"):
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, 10001, 10001)
    request(
        "POST",
        f"{es}/_security/user/kibana_system/_password",
        json={"password": os.environ["KIBANA_PASSWORD"]},
    )
    request(
        "PUT",
        f"{es}/_security/role/telemetry_writer",
        json={
            "cluster": ["monitor"],
            "indices": [
                {
                    "names": ["logs-*-local", "logs-apm*", "metrics-*", "traces-apm*"],
                    "privileges": [
                        "auto_configure",
                        "create_doc",
                        "write",
                        "read",
                        "view_index_metadata",
                    ],
                }
            ],
        },
    )
    request(
        "PUT",
        f"{es}/_security/user/telemetry_ingest",
        json={
            "password": os.environ["INGEST_PASSWORD"],
            "roles": ["telemetry_writer"],
        },
    )
    request(
        "PUT",
        f"{es}/_ilm/policy/platform-retention",
        json={
            "policy": {
                "phases": {
                    "hot": {
                        "actions": {"rollover": {"max_age": "1d", "max_primary_shard_size": "1gb"}}
                    },
                    "delete": {"min_age": "7d", "actions": {"delete": {}}},
                }
            }
        },
    )
    request(
        "PUT",
        f"{es}/_ilm/policy/infrastructure-retention",
        json={
            "policy": {
                "phases": {
                    "delete": {"min_age": "7d", "actions": {"delete": {}}},
                }
            }
        },
    )
    request(
        "PUT",
        f"{es}/_index_template/platform-logs",
        json={
            "index_patterns": ["logs-platform-*", "logs-access-*", "logs-quarantine-*"],
            "priority": 500,
            "data_stream": {},
            "template": {
                "settings": {
                    "index.number_of_shards": 1,
                    "index.number_of_replicas": 0,
                    "index.lifecycle.name": "platform-retention",
                },
                "mappings": {
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "trace": {"properties": {"id": {"type": "keyword"}}},
                        "span": {"properties": {"id": {"type": "keyword"}}},
                        "service": {"properties": {"name": {"type": "keyword"}}},
                        "log": {"properties": {"level": {"type": "keyword"}}},
                        "event": {
                            "properties": {
                                "outcome": {"type": "keyword"},
                                "duration": {"type": "long"},
                            }
                        },
                        "http": {
                            "properties": {
                                "response": {"properties": {"status_code": {"type": "integer"}}}
                            }
                        },
                        "source": {
                            "properties": {
                                "ip": {"type": "ip"},
                                "geo": {"properties": {"location": {"type": "geo_point"}}},
                            }
                        },
                    }
                },
            },
        },
    )
    request(
        "PUT",
        f"{es}/_index_template/infrastructure-metrics",
        json={
            "index_patterns": ["metrics-infrastructure-*"],
            "priority": 500,
            "template": {
                "settings": {
                    "index.number_of_shards": 1,
                    "index.number_of_replicas": 0,
                    "index.lifecycle.name": "infrastructure-retention",
                },
                "mappings": {"properties": {"@timestamp": {"type": "date"}}},
            },
        },
    )
    deadline = time.monotonic() + 420
    while time.monotonic() < deadline:
        try:
            request("GET", f"{kb}/api/status")
            break
        except httpx.HTTPError:
            time.sleep(5)
    else:
        raise RuntimeError("Kibana did not become ready")
    headers = {"kbn-xsrf": "platform-bootstrap"}
    request(
        "POST", f"{kb}/api/fleet/epm/packages/apm/8.19.22", headers=headers, json={"force": True}
    )
    for ident, title, name in [
        ("platform-logs", "logs-*-local", "Platform logs"),
        ("platform-traces", "traces-apm*", "Distributed traces"),
        ("platform-metrics", "metrics-*", "Infrastructure and application metrics"),
    ]:
        request(
            "POST",
            f"{kb}/api/data_views/data_view",
            headers=headers,
            json={
                "data_view": {
                    "id": ident,
                    "title": title,
                    "name": name,
                    "timeFieldName": "@timestamp",
                },
                "override": True,
            },
        )
    with Path("observability/kibana/dashboard.ndjson").open("rb") as content:
        result = request(
            "POST",
            f"{kb}/api/saved_objects/_import?overwrite=true",
            headers=headers,
            files={"file": ("dashboard.ndjson", content, "application/ndjson")},
        )
    if not result.get("success"):
        raise RuntimeError(f"Dashboard import failed: {result.get('errors')}")
    print(json.dumps({"bootstrap": "complete", "dashboard": "platform-overview"}))


if __name__ == "__main__":
    main()
