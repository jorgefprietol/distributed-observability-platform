import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from platform_app.app import create_app
from platform_app.settings import Settings
from platform_app.store import ActivationStore, ConflictError
from platform_app.telemetry import ECSFormatter

TOKEN = "test-credential-with-more-than-thirty-two-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": "request-00001"}
BODY = {"plan": "fiber-100", "region": "north"}


def receipt_response(request):
    payload = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            **payload,
            "status": "reserved" if request.url.path.endswith("reservations") else "provisioned",
            "effect_count": 1,
        },
    )


@pytest.fixture
def settings(tmp_path):
    return Settings(
        "activation-api",
        TOKEN,
        database=str(tmp_path / "state.db"),
        log_dir=str(tmp_path / "logs"),
        telemetry=False,
    )


def test_success_replay_and_downstream_auth(settings):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return receipt_response(request)

    with TestClient(create_app(settings, httpx.MockTransport(handler))) as client:
        response = client.post("/api/v1/activations", headers=AUTH, json=BODY)
        assert response.status_code == 201
        repeat = client.post("/api/v1/activations", headers=AUTH, json=BODY)
        assert repeat.json() == response.json()
        assert repeat.headers["Idempotency-Replayed"] == "true"
        assert len(calls) == 2
        assert calls[0].url.path == "/internal/reservations"
        assert calls[1].url.path == "/internal/provisions"


@pytest.mark.parametrize("failure", ["503", "timeout", "invalid-json"])
def test_downstream_failure_is_durable_and_not_retried(settings, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("unavailable")
        if failure == "invalid-json":
            return httpx.Response(200, content=b"invalid")
        return httpx.Response(503, json={"error": "unavailable"})

    with TestClient(create_app(settings, httpx.MockTransport(handler))) as client:
        response = client.post("/api/v1/activations", headers=AUTH, json=BODY)
        assert response.status_code == 503
        assert response.json()["status"] == "requires_reconciliation"
    with TestClient(create_app(settings, httpx.MockTransport(handler))) as client:
        replay = client.post("/api/v1/activations", headers=AUTH, json=BODY)
        assert replay.status_code == 503
        assert replay.json() == response.json()
        assert len(calls) == 1


def test_idempotency_payload_conflict(settings):
    transport = httpx.MockTransport(receipt_response)
    with TestClient(create_app(settings, transport)) as client:
        assert client.post("/api/v1/activations", headers=AUTH, json=BODY).status_code == 201
        response = client.post(
            "/api/v1/activations", headers=AUTH, json={**BODY, "region": "south"}
        )
        assert response.status_code == 409


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_authentication(settings, headers):
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/activations", headers=headers, json=BODY).status_code == 401


@pytest.mark.parametrize(
    "body,headers",
    [
        ({**BODY, "plan": "unbounded"}, AUTH),
        ({**BODY, "customer_email": "private@example.com"}, AUTH),
        (BODY, {**AUTH, "Idempotency-Key": "invalid\tvalue"}),
        (BODY, {"Authorization": f"Bearer {TOKEN}"}),
    ],
)
def test_contract_rejects_bad_input(settings, body, headers):
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/activations", headers=headers, json=body).status_code == 422


def test_fault_injection_is_disabled_by_default(settings):
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/api/v1/activations", headers=AUTH, json={**BODY, "scenario": "failure"}
            ).status_code
            == 403
        )


@pytest.mark.parametrize(
    "service,path,status",
    [
        ("inventory", "/internal/reservations", 200),
        ("provisioning", "/internal/provisions", 200),
    ],
)
def test_internal_contract(settings, service, path, status):
    with TestClient(create_app(replace(settings, service=service))) as client:
        payload = {**BODY, "activation_id": "11111111-1111-1111-1111-111111111111"}
        assert client.post(path, json=payload).status_code == 401
        assert client.post(path, headers=AUTH, json=payload).status_code == status


@pytest.mark.parametrize(
    "scenario,enabled,status",
    [
        ("failure", True, 503),
        ("latency", True, 200),
        ("failure", False, 403),
    ],
)
def test_provisioning_incidents(settings, scenario, enabled, status):
    config = replace(settings, service="provisioning", fault_injection=enabled)
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/internal/provisions",
            headers=AUTH,
            json={
                **BODY,
                "scenario": scenario,
                "activation_id": "11111111-1111-1111-1111-111111111111",
            },
        )
        assert response.status_code == status


def test_health_and_route_isolation(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").json()["status"] == "up"
        assert client.get("/health/ready").json()["status"] == "ready"
        assert client.post("/internal/provisions", headers=AUTH, json=BODY).status_code == 404


def test_pending_claim_survives_restart_without_duplicate(tmp_path):
    path = str(tmp_path / "state.db")
    store = ActivationStore(path)
    assert store.claim("key-00001", BODY) is None
    store.close()
    reopened = ActivationStore(path)
    with pytest.raises(ConflictError, match="reconciliation"):
        reopened.claim("key-00001", BODY)
    reopened.close()


def test_environment_validation(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "short")
    with pytest.raises(ValueError, match="32"):
        Settings.from_env()
    monkeypatch.setenv("API_TOKEN", TOKEN)
    monkeypatch.setenv("SERVICE_NAME", "unknown")
    with pytest.raises(ValueError, match="SERVICE_NAME"):
        Settings.from_env()
    monkeypatch.setenv("SERVICE_NAME", "inventory")
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    assert Settings.from_env().fault_injection


def test_log_files_never_include_credentials(settings):
    from pathlib import Path

    with TestClient(create_app(settings)) as client:
        client.post("/api/v1/activations?token=private-query", headers=AUTH, json=BODY)
    content = "".join(p.read_text() for p in Path(settings.log_dir).glob("*"))
    assert TOKEN not in content
    assert "private-query" not in content
    assert '"service": {"name": "activation-api"' in content


def test_ecs_formatter_is_structured():
    import logging

    record = logging.LogRecord("test", logging.ERROR, "", 0, "Dependency failed", (), None)
    record.fields = {"event": {"outcome": "failure"}}
    value = json.loads(ECSFormatter("inventory").format(record))
    assert value["log"]["level"] == "error"
    assert value["event"]["outcome"] == "failure"
    assert value["@timestamp"].endswith("+00:00")
