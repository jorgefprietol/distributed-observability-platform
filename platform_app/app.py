import asyncio
import hmac
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
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
    scenario: Literal["normal", "latency", "failure"] = "normal"


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

    app = FastAPI(title=settings.service, version="1.0.0", lifespan=lifespan)

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

    async def downstream(url: str, payload: dict):
        with tracer.start_as_current_span("POST downstream", kind=SpanKind.CLIENT) as span:
            span.set_attribute("server.address", httpx.URL(url).host)
            span.set_attribute("http.request.method", "POST")
            headers = {"Authorization": f"Bearer {settings.token}"}
            propagate.inject(headers)
            response = await app.state.client.post(url, json=payload, headers=headers)
            span.set_attribute("http.response.status_code", response.status_code)
            response.raise_for_status()
            return response.json()

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
            activation_id = str(uuid4())
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
                    await downstream(f"{settings.inventory_url}/internal/reservations", payload)
                    await downstream(f"{settings.provisioning_url}/internal/provisions", payload)
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
                return {"activation_id": body.activation_id, "status": "reserved"}

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
                logger.info(
                    "Service provisioned",
                    extra={
                        "fields": {
                            "business": {"activation": {"id": body.activation_id}},
                        }
                    },
                )
                return {"activation_id": body.activation_id, "status": "provisioned"}

    return app


def factory():
    return create_app(Settings.from_env())
