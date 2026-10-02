"""Exercise uncertainty, durable receipts, restart recovery and a genuinely missing effect."""

import argparse
import json
import secrets
import subprocess
import time
from pathlib import Path

import httpx
from e2e import environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--restart", action="store_true", help="Restart the three application services"
    )
    args = parser.parse_args()
    env = environment()
    api = env.get("API_URL", "http://127.0.0.1:18090")
    reports = {}
    with httpx.Client(timeout=15) as client:
        for scenario in ("response_loss", "failure"):
            key = f"drill-{secrets.token_hex(12)}"
            headers = {"Authorization": f"Bearer {env['API_TOKEN']}", "Idempotency-Key": key}
            body = {"plan": "fiber-100", "region": "north", "scenario": scenario}
            response = client.post(f"{api}/api/v1/activations", headers=headers, json=body)
            assert response.status_code == 503, response.status_code
            initial = response.json()
            if args.restart and scenario == "response_loss":
                subprocess.run(
                    ["docker", "compose", "restart", "activation-api", "inventory", "provisioning"],
                    check=True,
                )
            deadline = time.monotonic() + 180
            while True:
                try:
                    snapshot = client.get(f"{api}/api/v1/activations/{key}", headers=headers)
                    snapshot.raise_for_status()
                    assert snapshot.json()["activation_id"] == initial["activation_id"]
                    reconciled = client.post(
                        f"{api}/api/v1/activations/{key}/reconcile", headers=headers
                    )
                    if scenario == "response_loss" and reconciled.status_code != 200:
                        raise AssertionError("Waiting for durable downstream receipts")
                    assert reconciled.status_code == (200 if scenario == "response_loss" else 409)
                    break
                except (httpx.HTTPError, AssertionError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(2)
            replay = client.post(f"{api}/api/v1/activations", headers=headers, json=body)
            assert replay.status_code == (201 if scenario == "response_loss" else 503)
            assert replay.headers["Idempotency-Replayed"] == "true"
            final = client.get(f"{api}/api/v1/activations/{key}", headers=headers).json()
            assert final["reconciliation"]["outcome"] == (
                "confirmed" if scenario == "response_loss" else "blocked"
            )
            reports[scenario] = {
                "activation_id": initial["activation_id"],
                "initial_trace_id": response.headers["X-Trace-ID"],
                "reconciliation_trace_id": reconciled.headers["X-Trace-ID"],
                "initial_status": 503,
                "reconciled_status": reconciled.status_code,
                "replayed_status": replay.status_code,
                "evidence": final["reconciliation"],
                "restart_verified": args.restart and scenario == "response_loss",
            }
    report = {"result": "passed", "scenarios": reports}
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/incident-drill.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
