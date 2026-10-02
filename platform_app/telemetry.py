import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class ECSFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record):
        span = trace.get_current_span().get_span_context()
        event = {
            "@timestamp": datetime.now(UTC).isoformat(),
            "ecs": {"version": "8.11.0"},
            "service": {"name": self.service, "environment": "local"},
            "log": {"level": record.levelname.lower()},
            "message": record.getMessage(),
        }
        if span.is_valid:
            event["trace"] = {"id": format(span.trace_id, "032x")}
            event["span"] = {"id": format(span.span_id, "016x")}
        event.update(getattr(record, "fields", {}))
        return json.dumps(event, ensure_ascii=False)


def configure_logging(service: str, log_dir: str):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"platform.{service}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    for handler in (
        logging.StreamHandler(),
        RotatingFileHandler(Path(log_dir) / f"{service}.json", maxBytes=10_000_000, backupCount=3),
    ):
        handler.setFormatter(ECSFormatter(service))
        logger.addHandler(handler)
    access = logging.getLogger(f"access.{service}")
    access.setLevel(logging.INFO)
    access.propagate = False
    for handler in access.handlers[:]:
        access.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(
        Path(log_dir) / f"{service}.access", maxBytes=10_000_000, backupCount=3
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    access.addHandler(handler)
    return logger, access


def configure_telemetry(service: str):
    resource = Resource.create(
        {"service.name": service, "service.version": "1.0.0", "deployment.environment": "local"}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(), schedule_delay_millis=1000))
    trace.set_tracer_provider(provider)
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=5000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)
    return provider, meter_provider
