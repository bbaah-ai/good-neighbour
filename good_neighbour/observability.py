"""Structured logging, OpenTelemetry tracing, and intent/outcome capture.

Call :func:`configure_observability` once at process start (``agent.py`` does
this on import). Everything else in the package logs through
``logging.getLogger("good_neighbour.*")`` with ``extra=`` fields, which the JSON
formatter turns into first-class keys.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from . import config
from .guardrails import PiiRedactingFilter

_configured = False
tracer = trace.get_tracer("good_neighbour")
logger = logging.getLogger("good_neighbour")


def _json_formatter() -> logging.Formatter:
    """Build a JSON formatter, tolerating both python-json-logger APIs."""
    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    try:  # python-json-logger >= 3
        from pythonjsonlogger.json import JsonFormatter

        return JsonFormatter(fmt, rename_fields={"asctime": "timestamp", "levelname": "severity"})
    except ImportError:  # pragma: no cover - legacy package layout
        from pythonjsonlogger import jsonlogger

        return jsonlogger.JsonFormatter(
            fmt, rename_fields={"asctime": "timestamp", "levelname": "severity"}
        )


def configure_observability() -> None:
    """Install JSON logging with PII redaction and an OTel tracer provider.

    Idempotent. Exporter selection follows ``GN_TRACE_EXPORTER``:

    * ``console`` (default) — spans printed to stderr, handy for local demos.
    * ``gcp`` — Cloud Trace via ``opentelemetry-exporter-gcp-trace``; requires
      ``GOOGLE_CLOUD_PROJECT`` and the ``gcp`` extra.
    * ``none`` — provider installed, nothing exported.
    """
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(config.LOG_LEVEL)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_json_formatter())
    root.handlers = [handler]
    root.addFilter(PiiRedactingFilter())
    handler.addFilter(PiiRedactingFilter())

    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: config.APP_NAME}))
    exporter_name = config.TRACE_EXPORTER
    if exporter_name == "gcp" and config.GOOGLE_CLOUD_PROJECT:
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            provider.add_span_processor(
                BatchSpanProcessor(CloudTraceSpanExporter(project_id=config.GOOGLE_CLOUD_PROJECT))
            )
            logger.info("tracing.exporter", extra={"exporter": "cloud_trace"})
        except ImportError:
            logger.warning(
                "tracing.exporter_unavailable",
                extra={"exporter": "cloud_trace", "hint": "pip install 'good-neighbour[gcp]'"},
            )
    elif exporter_name == "console" and not os.environ.get("PYTEST_CURRENT_TEST"):
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr)))
    trace.set_tracer_provider(provider)


# --- Intent / outcome capture -------------------------------------------------


def log_intent(action: str, **fields: Any) -> float:
    """Record what the agent is *about* to do. Returns a start timestamp."""
    logger.info("agent.intent", extra={"event_phase": "INTENT", "action": action, **fields})
    return time.perf_counter()


def log_outcome(action: str, started: float, *, ok: bool, **fields: Any) -> None:
    """Record what *actually* happened, paired with the preceding intent."""
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.log(
        logging.INFO if ok else logging.WARNING,
        "agent.outcome",
        extra={
            "event_phase": "OUTCOME",
            "action": action,
            "ok": ok,
            "latency_ms": latency_ms,
            **fields,
        },
    )


@contextmanager
def traced(name: str, **attributes: Any) -> Iterator[trace.Span]:
    """Open a span with the given attributes; records exceptions automatically."""
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(
                    key, value if isinstance(value, (str, int, float, bool)) else str(value)
                )
        yield span
