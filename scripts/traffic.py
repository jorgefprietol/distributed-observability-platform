"""Generate authenticated synthetic workloads without credentials in output."""

import argparse
import json
import secrets
import time

import httpx
from e2e import environment

parser = argparse.ArgumentParser()
parser.add_argument("--requests", type=int, default=30)
parser.add_argument("--scenario", choices=["normal", "latency", "failure"], default="normal")
args = parser.parse_args()
if not 1 <= args.requests <= 1000:
    parser.error("requests must be between 1 and 1000")
env = environment()
with httpx.Client(timeout=10) as client:
    for number in range(args.requests):
        started = time.perf_counter()
        response = client.post(
            f"{env.get('API_URL', 'http://127.0.0.1:18090')}/api/v1/activations",
            headers={
                "Authorization": f"Bearer {env['API_TOKEN']}",
                "Idempotency-Key": f"traffic-{secrets.token_hex(16)}",
            },
            json={"plan": "fiber-100", "region": "north", "scenario": args.scenario},
        )
        print(
            json.dumps(
                {
                    "request": number + 1,
                    "status": response.status_code,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "trace_id": response.headers.get("X-Trace-ID"),
                }
            )
        )
        if response.status_code != (503 if args.scenario == "failure" else 201):
            raise SystemExit("Unexpected activation response")
