"""Build versionable native Kibana saved objects without embedding installation IDs."""

import json
from pathlib import Path

objects = []
panels = []
references = []


def chart(ident, title, kind, aggs, query, view="platform-logs"):
    search = {
        "query": {"query": query, "language": "kuery"},
        "filter": [],
        "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index",
    }
    objects.append(
        {
            "type": "visualization",
            "id": ident,
            "attributes": {
                "title": title,
                "description": "Provisioned from source control",
                "visState": json.dumps(
                    {
                        "title": title,
                        "type": kind,
                        "params": {
                            "addLegend": True,
                            "addTooltip": True,
                            "legendPosition": "right",
                        },
                        "aggs": aggs,
                    }
                ),
                "uiStateJSON": "{}",
                "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps(search)},
            },
            "references": [
                {
                    "name": "kibanaSavedObjectMeta.searchSourceJSON.index",
                    "type": "index-pattern",
                    "id": view,
                }
            ],
        }
    )
    number = len(panels)
    ref = f"panel_{number}"
    panels.append(
        {
            "panelIndex": str(number),
            "panelRefName": ref,
            "type": "visualization",
            "embeddableConfig": {},
            "gridData": {
                "x": (number % 2) * 24,
                "y": (number // 2) * 14,
                "w": 24,
                "h": 14,
                "i": str(number),
            },
        }
    )
    references.append({"name": ref, "type": "visualization", "id": ident})


count = {"id": "1", "enabled": True, "type": "count", "schema": "metric", "params": {}}
time = {
    "id": "2",
    "enabled": True,
    "type": "date_histogram",
    "schema": "segment",
    "params": {"field": "@timestamp", "interval": "auto", "min_doc_count": 1},
}
chart(
    "platform-requests",
    "HTTP requests over time",
    "histogram",
    [count, time],
    'event.dataset: "platform"',
)
chart("platform-errors", "Errors requiring investigation", "metric", [count], 'log.level: "error"')
chart(
    "platform-latency",
    "HTTP latency p95 (nanoseconds)",
    "metric",
    [
        {
            "id": "1",
            "enabled": True,
            "type": "percentiles",
            "schema": "metric",
            "params": {"field": "event.duration", "percents": [95]},
        }
    ],
    'event.dataset: "platform"',
)
chart(
    "platform-status",
    "HTTP response codes",
    "pie",
    [
        count,
        {
            "id": "2",
            "enabled": True,
            "type": "terms",
            "schema": "segment",
            "params": {
                "field": "http.response.status_code",
                "size": 10,
                "order": "desc",
                "orderBy": "1",
            },
        },
    ],
    'event.dataset: "access"',
)
chart(
    "platform-services",
    "Requests by service",
    "table",
    [
        count,
        {
            "id": "2",
            "enabled": True,
            "type": "terms",
            "schema": "bucket",
            "params": {"field": "service.name", "size": 10, "order": "desc", "orderBy": "1"},
        },
    ],
    'event.dataset: "platform"',
)
chart(
    "platform-cpu",
    "CPU normalized utilization (0–1)",
    "line",
    [
        {
            "id": "1",
            "enabled": True,
            "type": "avg",
            "schema": "metric",
            "params": {"field": "system.cpu.total.norm.pct"},
        },
        time,
    ],
    'event.dataset: "system.cpu"',
    "platform-metrics",
)
objects.append(
    {
        "type": "dashboard",
        "id": "platform-overview",
        "references": references,
        "attributes": {
            "title": "Distributed Observability | Operations Overview",
            "description": "Request volume, failures, latency, response codes and infrastructure.",
            "panelsJSON": json.dumps(panels),
            "optionsJSON": '{"useMargins":true}',
            "version": 1,
            "timeRestore": False,
            "kibanaSavedObjectMeta": {
                "searchSourceJSON": '{"query":{"query":"","language":"kuery"},"filter":[]}'
            },
        },
    }
)
path = Path("observability/kibana/dashboard.ndjson")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(
    "\n".join(json.dumps(obj, ensure_ascii=False) for obj in objects) + "\n", encoding="utf-8"
)
print(f"Generated {len(objects)} Kibana saved objects")
