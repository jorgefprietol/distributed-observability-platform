import asyncio
import hmac
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from opentelemetry import metrics, propagate, trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import BaseModel, ConfigDict, Field

from platform_app.settings import Settings
from platform_app.store import ActivationStore, ConflictError
from platform_app.telemetry import configure_logging, configure_telemetry


class ActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: Literal["fiber-100", "fiber-500"]
    region: Literal["north", "south"]
    scenario: Literal["normal", "latency", "failure", "response_loss"] = "normal"


class ProvisionRequest(ActivationRequest):
    activation_id: str = Field(pattern=r"^[a-f0-9-]{36}$")


def create_app(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    providers = []
    if settings.telemetry:
        providers.extend(configure_telemetry(settings.service))
    logger, access = configure_logging(settings.service, settings.log_dir)
    tracer = trace.get_tracer("activation-platform")
    meter = metrics.get_meter("activation-platform")
    requests = meter.create_counter("platform.http.requests", unit="{request}")
    duration = meter.create_histogram("platform.http.duration", unit="s")
    store = ActivationStore(settings.database)

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=2.0, transport=transport) as client:
            app.state.client = client
            yield
        store.close()
        for provider in providers:
            provider.shutdown()

    app = FastAPI(
        title=settings.service,
        version="1.0.0",
        lifespan=lifespan,
        telemetry={
            "auto_configure": False,
            "tracing": False,
            "metrics": False,
            "logs": False,
            "operation_spans": False,
        },
    )

    async def authenticate(authorization: Annotated[str | None, Header()] = None):
        expected = f"Bearer {settings.token}"
        if authorization is None or not hmac.compare_digest(
            authorization.encode(), expected.encode()
        ):
            raise HTTPException(401, "Invalid credentials")

    auth = [Depends(authenticate)]

    @app.middleware("http")
    async def observe(request: Request, call_next):
        if request.url.path.startswith("/health/"):
            return await call_next(request)
        # Use a bounded label instead of paths supplied by clients.
        route = (
            request.url.path
            if request.url.path
            in {"/api/v1/activations", "/internal/reservations", "/internal/provisions"}
            else "/other"
        )
        if request.url.path.startswith("/api/v1/activations/"):
            route = (
                "/api/v1/activations/{key}/reconcile"
                if request.url.path.endswith("/reconcile")
                else "/api/v1/activations/{key}"
            )
        elif request.url.path.startswith("/internal/") and request.method == "GET":
            route = "/internal/effects/{activation_id}"
        started = time.perf_counter()
        with tracer.start_as_current_span(
            f"{request.method} {route}",
            context=propagate.extract(dict(request.headers)),
            kind=SpanKind.SERVER,
            attributes={"http.request.method": request.method, "http.route": route},
        ) as span:
            response = await call_next(request)
            elapsed = time.perf_counter() - started
            status = response.status_code
            span.set_attribute("http.response.status_code", status)
            if status >= 500:
                span.set_status(Status(StatusCode.ERROR, "HTTP server error"))
            trace_id = format(span.get_span_context().trace_id, "032x")
            response.headers["X-Trace-ID"] = trace_id
            attrs = {
                "http.route": route,
                "http.request.method": request.method,
                "http.response.status_code": status,
            }
            requests.add(1, attrs)
            duration.record(elapsed, attrs)
            logger.log(
                40 if status >= 500 else 20,
                "HTTP request completed",
                extra={
                    "fields": {
                        "http": {
                            "request": {"method": request.method},
                            "response": {"status_code": status},
                        },
                        "url": {"path": route},
                        "event": {
                            "duration": int(elapsed * 1_000_000_000),
                            "outcome": "failure" if status >= 500 else "success",
                        },
                    }
                },
            )
            timestamp = datetime.now(UTC).strftime("%d/%b/%Y:%H:%M:%S +0000")
            # Fixed synthetic source IP: no client IPs, credentials or query strings persisted.
            access.info(
                '127.0.0.1 - - [%s] "%s %s HTTP/1.1" %s 0 "-" "platform-client"',
                timestamp,
                request.method,
                route,
                status,
            )
            return response

    @app.get("/health/live")
    async def live():
        return {"status": "up", "service": settings.service}

    @app.get("/health/ready")
    async def ready():
        store.db.execute("SELECT 1")
        return {"status": "ready", "service": settings.service}

    async def downstream(method: str, url: str, payload: dict | None = None):
        with tracer.start_as_current_span(f"{method} downstream", kind=SpanKind.CLIENT) as span:
            span.set_attribute("server.address", httpx.URL(url).host)
            span.set_attribute("http.request.method", method)
            headers = {"Authorization": f"Bearer {settings.token}"}
            propagate.inject(headers)
            response = await app.state.client.request(method, url, json=payload, headers=headers)
            span.set_attribute("http.response.status_code", response.status_code)
            if method == "GET" and response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    def confirmed(receipt: dict | None, payload: dict, expected: str) -> bool:
        return isinstance(receipt, dict) and all(
            receipt.get(key) == value
            for key, value in {
                "activation_id": payload["activation_id"],
                "plan": payload["plan"],
                "region": payload["region"],
                "status": expected,
                "effect_count": 1,
            }.items()
        )

    if settings.service == "activation-api":

        @app.post("/api/v1/activations", dependencies=auth)
        async def activate(
            body: ActivationRequest,
            idempotency_key: Annotated[str, Header(pattern=r"^[A-Za-z0-9_-]{8,80}$")],
        ):
            if body.scenario != "normal" and not settings.fault_injection:
                raise HTTPException(403, "Fault injection disabled")
            try:
                cached = store.claim(idempotency_key, body.model_dump())
            except ConflictError as error:
                raise HTTPException(409, str(error)) from error
            if cached:
                status, result = cached
                return JSONResponse(
                    result, status_code=status, headers={"Idempotency-Replayed": "true"}
                )
            activation_id = store.get(idempotency_key)["activation_id"]
            payload = {**body.model_dump(), "activation_id": activation_id}
            with tracer.start_as_current_span(
                "activation.process",
                attributes={
                    "business.activation.id": activation_id,
                    "business.plan": body.plan,
                    "business.region": body.region,
                },
            ) as span:
                try:
                    for url, expected in (
                        (f"{settings.inventory_url}/internal/reservations", "reserved"),
                        (f"{settings.provisioning_url}/internal/provisions", "provisioned"),
                    ):
                        receipt = await downstream("POST", url, payload)
                        if not confirmed(receipt, payload, expected):
                            raise ValueError("Invalid downstream receipt")
                    result = {"activation_id": activation_id, "status": "active", "plan": body.plan}
                    status = 201
                except (httpx.HTTPError, ValueError) as error:
                    span.set_status(Status(StatusCode.ERROR, "Downstream activation failed"))
                    logger.error(
                        "Activation requires reconciliation",
                        extra={
                            "fields": {
                                "business": {"activation": {"id": activation_id}},
                                "error": {"type": type(error).__name__},
                                "event": {"outcome": "failure"},
                            }
                        },
                    )
                    result = {"activation_id": activation_id, "status": "requires_reconciliation"}
                    status = 503
                store.complete(idempotency_key, status, result)
                return JSONResponse(result, status_code=status)

        @app.get("/api/v1/activations/{key}", dependencies=auth)
        async def activation_status(key: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{8,80}$")]):
            item = store.get(key)
            if item is None:
                raise HTTPException(404, "Activation not found")
            return item

        @app.post("/api/v1/activations/{key}/reconcile", dependencies=auth)
        async def reconcile_activation(
            key: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{8,80}$")],
        ):
            item = store.get(key)
            if item is None:
                raise HTTPException(404, "Activation not found")
            if item["status"] == "active":
                return item["result"]
            evidence = {"inventory": "unknown", "provisioning": "unknown"}
            if item["activation_id"] and item["payload"]:
                payload = {**item["payload"], "activation_id": item["activation_id"]}
                with tracer.start_as_current_span(
                    "activation.reconcile",
                    attributes={"business.activation.id": item["activation_id"]},
                ):
                    for service, url, expected in (
                        (
                            "inventory",
                            f"{settings.inventory_url}/internal/reservations",
                            "reserved",
                        ),
                        (
                            "provisioning",
                            f"{settings.provisioning_url}/internal/provisions",
                            "provisioned",
                        ),
                    ):
                        try:
                            receipt = await downstream("GET", f"{url}/{item['activation_id']}")
                            evidence[service] = (
                                "confirmed"
                                if confirmed(receipt, payload, expected)
                                else "missing"
                                if receipt is None
                                else "invalid"
                            )
                        except (httpx.HTTPError, ValueError):
                            evidence[service] = "unavailable"
            success = all(value == "confirmed" for value in evidence.values())
            evidence["outcome"] = "confirmed" if success else "blocked"
            result = (
                {
                    "activation_id": item["activation_id"],
                    "status": "active",
                    "plan": payload["plan"],
                }
                if success
                else None
            )
            store.reconcile(key, result, evidence)
            logger.info(
                "Activation reconciliation completed",
                extra={
                    "fields": {
                        "business": {"activation": {"id": item["activation_id"]}},
                        "reconciliation": evidence,
                    }
                },
            )
            return JSONResponse(
                result or {"status": "requires_reconciliation", "evidence": evidence},
                status_code=200 if success else 409,
            )

    elif settings.service == "inventory":

        @app.post("/internal/reservations", dependencies=auth)
        async def reserve(body: ProvisionRequest):
            with tracer.start_as_current_span(
                "inventory.reserve",
                attributes={
                    "business.activation.id": body.activation_id,
                },
            ):
                logger.info(
                    "Capacity reservation evaluated",
                    extra={
                        "fields": {
                            "business": {"activation": {"id": body.activation_id}},
                        }
                    },
                )
                try:
                    result, _ = store.apply_effect(
                        body.activation_id, body.model_dump(), "reserved"
                    )
                except ConflictError as error:
                    raise HTTPException(409, str(error)) from error
                return result

    else:

        @app.post("/internal/provisions", dependencies=auth)
        async def provision(body: ProvisionRequest):
            if body.scenario != "normal" and not settings.fault_injection:
                raise HTTPException(403, "Fault injection disabled")
            with tracer.start_as_current_span(
                "provisioning.activate",
                attributes={
                    "business.activation.id": body.activation_id,
                },
            ) as span:
                if body.scenario == "latency":
                    await asyncio.sleep(0.35)
                if body.scenario == "failure":
                    span.set_status(Status(StatusCode.ERROR, "Dependency unavailable"))
                    logger.error(
                        "Provisioning dependency unavailable",
                        extra={
                            "fields": {
                                "business": {"activation": {"id": body.activation_id}},
                                "event": {"outcome": "failure"},
                            }
                        },
                    )
                    raise HTTPException(503, "Provisioning dependency unavailable")
                try:
                    result, replayed = store.apply_effect(
                        body.activation_id, body.model_dump(), "provisioned"
                    )
                except ConflictError as error:
                    raise HTTPException(409, str(error)) from error
                logger.info(
                    "Service provisioned",
                    extra={
                        "fields": {
                            "business": {"activation": {"id": body.activation_id}},
                        }
                    },
                )
                if body.scenario == "response_loss" and not replayed:
                    raise HTTPException(503, "Synthetic acknowledgement lost after durable commit")
                return result

    if settings.service in {"inventory", "provisioning"}:
        effect_route = (
            "/internal/reservations/{activation_id}"
            if settings.service == "inventory"
            else "/internal/provisions/{activation_id}"
        )

        @app.get(effect_route, dependencies=auth)
        async def effect_status(
            activation_id: Annotated[str, Path(pattern=r"^[a-f0-9-]{36}$")],
        ):
            receipt = store.effect(activation_id)
            if receipt is None:
                raise HTTPException(404, "Effect not found")
            return receipt

    return app


def factory():
    return create_app(Settings.from_env())
