import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from platform_app.app import create_app
from platform_app.settings import Settings
from platform_app.store import ActivationStore, ConflictError

TOKEN = "reconciliation-test-credential-over-thirty-two-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": "recovery-0001"}
BODY = {"plan": "fiber-100", "region": "north", "scenario": "normal"}
KEY = "recovery-0001"
ACTIVATION = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        "activation-api",
        TOKEN,
        database=str(tmp_path / "api.db"),
        log_dir=str(tmp_path / "logs"),
        telemetry=False,
        fault_injection=True,
    )


@contextmanager
def cluster(settings, lose_ack=False):
    calls = []
    with ExitStack() as stack:
        services = {}
        for service in ("inventory", "provisioning"):
            config = replace(settings, service=service, database=f"{settings.database}.{service}")
            services[service] = stack.enter_context(TestClient(create_app(config)))

        def handler(request):
            calls.append((request.method, request.url.path))
            service = "inventory" if "/reservations" in request.url.path else "provisioning"
            response = services[service].request(
                request.method,
                request.url.path,
                headers=dict(request.headers),
                json=json.loads(request.content) if request.content else None,
            )
            if lose_ack and service == "provisioning" and request.method == "POST":
                assert response.status_code == 200
                raise httpx.ReadTimeout("Acknowledgement lost after durable effect")
            return httpx.Response(response.status_code, json=response.json())

        api = stack.enter_context(TestClient(create_app(settings, httpx.MockTransport(handler))))
        yield api, services, calls


@pytest.mark.parametrize("pending_after_crash", [False, True])
def test_recovery_after_lost_ack_and_restart_never_reexecutes_effect(settings, pending_after_crash):
    with cluster(settings, lose_ack=True) as (api, _, calls):
        failed = api.post("/api/v1/activations", headers=AUTH, json=BODY)
        assert failed.status_code == 503
        activation_id = failed.json()["activation_id"]
        assert len(calls) == 2
    if pending_after_crash:
        # Represents termination after both effects committed, before API completion persisted.
        with sqlite3.connect(settings.database) as db:
            db.execute("UPDATE activations SET result=NULL, status=NULL, state='pending'")
    with cluster(settings) as (api, services, calls):
        item = api.get(f"/api/v1/activations/{KEY}", headers=AUTH).json()
        assert item["activation_id"] == activation_id
        assert item["payload"] == BODY
        response = api.post(f"/api/v1/activations/{KEY}/reconcile", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["status"] == "active"
        assert len(calls) == 2 and all(method == "GET" for method, _ in calls)
        assert api.post(f"/api/v1/activations/{KEY}/reconcile", headers=AUTH).status_code == 200
        assert len(calls) == 2
        for service, path in (("inventory", "reservations"), ("provisioning", "provisions")):
            receipt = services[service].get(f"/internal/{path}/{activation_id}", headers=AUTH)
            assert receipt.json()["effect_count"] == 1
        replay = api.post("/api/v1/activations", headers=AUTH, json=BODY)
        assert replay.status_code == 201 and replay.headers["Idempotency-Replayed"] == "true"
    with cluster(settings) as (api, _, calls):
        item = api.get(f"/api/v1/activations/{KEY}", headers=AUTH).json()
        assert item["status"] == "active"
        assert item["reconciliation"]["outcome"] == "confirmed"
        assert api.post("/api/v1/activations", headers=AUTH, json=BODY).status_code == 201
        assert calls == []


def test_missing_effect_blocks_reconciliation_without_post(settings):
    body = {**BODY, "scenario": "failure"}
    with cluster(settings) as (api, _, calls):
        failed = api.post("/api/v1/activations", headers=AUTH, json=body)
        assert failed.status_code == 503
        response = api.post(f"/api/v1/activations/{KEY}/reconcile", headers=AUTH)
        assert response.status_code == 409
        assert response.json()["evidence"] == {
            "inventory": "confirmed",
            "provisioning": "missing",
            "outcome": "blocked",
        }
        assert [method for method, _ in calls] == ["POST", "POST", "GET", "GET"]
        assert api.post("/api/v1/activations", headers=AUTH, json=body).json() == failed.json()
        assert len(calls) == 4
    with cluster(settings) as (api, _, _):
        item = api.get(f"/api/v1/activations/{KEY}", headers=AUTH).json()
        assert item["reconciliation"]["outcome"] == "blocked"
        assert item["status"] == "requires_reconciliation"


@pytest.mark.parametrize("failure", ["timeout", "invalid-json", "wrong-receipt", "missing"])
def test_reconciliation_cannot_infer_success_from_bad_evidence(settings, failure):
    store = ActivationStore(settings.database)
    store.claim(KEY, BODY)
    store.close()
    calls = []

    def handler(request):
        calls.append(request.method)
        if failure == "timeout":
            raise httpx.ReadTimeout("Unavailable")
        if failure == "invalid-json":
            return httpx.Response(200, content=b"not-json")
        return httpx.Response(404 if failure == "missing" else 200, json={"status": "provisioned"})

    with TestClient(create_app(settings, httpx.MockTransport(handler))) as api:
        response = api.post(f"/api/v1/activations/{KEY}/reconcile", headers=AUTH)
        assert response.status_code == 409
        expected = (
            "missing"
            if failure == "missing"
            else "invalid"
            if failure == "wrong-receipt"
            else "unavailable"
        )
        assert response.json()["evidence"]["inventory"] == expected
        assert api.get(f"/api/v1/activations/{KEY}", headers=AUTH).json()["status"] == "pending"
        assert api.post("/api/v1/activations", headers=AUTH, json=BODY).status_code == 409
        assert calls == ["GET", "GET"]


def test_reconciliation_contract_and_authorization(settings):
    with TestClient(create_app(settings)) as api:
        for method, suffix in (("GET", ""), ("POST", "/reconcile")):
            assert api.request(method, f"/api/v1/activations/{KEY}{suffix}").status_code == 401
            assert (
                api.request(method, f"/api/v1/activations/{KEY}{suffix}", headers=AUTH).status_code
                == 404
            )
            assert (
                api.request(method, f"/api/v1/activations/bad{suffix}", headers=AUTH).status_code
                == 422
            )


@pytest.mark.parametrize(
    "service,path,status",
    [
        ("inventory", "reservations", "reserved"),
        ("provisioning", "provisions", "provisioned"),
    ],
)
def test_effect_receipts_are_durable_immutable_and_authenticated(settings, service, path, status):
    config = replace(settings, service=service)
    payload = {**BODY, "activation_id": ACTIVATION}
    with TestClient(create_app(config)) as client:
        assert client.get(f"/internal/{path}/{ACTIVATION}", headers=AUTH).status_code == 404
        first = client.post(f"/internal/{path}", headers=AUTH, json=payload)
        assert first.json()["status"] == status
    with TestClient(create_app(config)) as client:
        assert client.get(f"/internal/{path}/{ACTIVATION}").status_code == 401
        assert client.get(f"/internal/{path}/{ACTIVATION}", headers=AUTH).json() == first.json()
        assert client.post(f"/internal/{path}", headers=AUTH, json=payload).json() == first.json()
        conflict = client.post(
            f"/internal/{path}", headers=AUTH, json={**payload, "region": "south"}
        )
        assert conflict.status_code == 409


def test_synthetic_response_loss_is_reconciled_using_receipts(settings):
    with cluster(settings) as (api, _, calls):
        body = {**BODY, "scenario": "response_loss"}
        assert api.post("/api/v1/activations", headers=AUTH, json=body).status_code == 503
        assert api.post(f"/api/v1/activations/{KEY}/reconcile", headers=AUTH).status_code == 200
        assert [method for method, _ in calls] == ["POST", "POST", "GET", "GET"]


def test_legacy_pending_rows_are_preserved_and_cannot_be_guessed(settings):
    with sqlite3.connect(settings.database) as db:
        db.execute(
            "CREATE TABLE activations (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
            "result TEXT, status INTEGER)"
        )
        db.execute("INSERT INTO activations VALUES (?, 'historical', NULL, NULL)", (KEY,))
    with TestClient(create_app(settings)) as api:
        response = api.post(f"/api/v1/activations/{KEY}/reconcile", headers=AUTH)
        assert response.status_code == 409
        assert response.json()["evidence"]["inventory"] == "unknown"
        assert api.get(f"/api/v1/activations/{KEY}", headers=AUTH).json()["payload"] is None


def test_concurrent_effect_attempts_commit_once(tmp_path):
    store = ActivationStore(str(tmp_path / "state.db"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: store.apply_effect(ACTIVATION, BODY, "reserved"), range(32))
        )
    assert sum(not replayed for _, replayed in results) == 1
    assert store.db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
    with pytest.raises(ConflictError):
        store.apply_effect(ACTIVATION, {**BODY, "region": "south"}, "reserved")
    store.close()


def test_late_failure_cannot_overwrite_reconciled_success(tmp_path):
    store = ActivationStore(str(tmp_path / "state.db"))
    store.claim(KEY, BODY)
    result = {
        "activation_id": store.get(KEY)["activation_id"],
        "status": "active",
        "plan": BODY["plan"],
    }
    store.reconcile(KEY, result, {"outcome": "confirmed"})
    store.complete(KEY, 503, {"status": "requires_reconciliation"})
    assert store.claim(KEY, BODY) == (201, result)
    store.close()
