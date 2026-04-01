"""
OpenTelemetry integration for sentinel-core.

Install the optional dependency to use this module:

    pip install agentcop[otel]

This module provides:
  - to_otel_log_record()  — convert a SentinelEvent to an OTel LogRecord
  - OtelSentinelExporter  — emit SentinelEvents through a LoggerProvider

Example::

    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
    from opentelemetry.sdk._logs.export.in_memory_span_exporter import InMemoryLogExporter
    from agentcop.otel import OtelSentinelExporter

    provider = LoggerProvider()
    exporter = InMemoryLogExporter()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))

    sentinel_exporter = OtelSentinelExporter(logger_provider=provider)
    sentinel_exporter.export(sentinel.detect_violations_as_events())
"""

from __future__ import annotations

from datetime import timezone
from typing import TYPE_CHECKING, List, Sequence

from .event import SentinelEvent

if TYPE_CHECKING:
    pass

_SEVERITY_NUMBER_MAP = {
    "INFO": 9,       # OTel SeverityNumber.INFO
    "WARN": 13,      # OTel SeverityNumber.WARN
    "ERROR": 17,     # OTel SeverityNumber.ERROR
    "CRITICAL": 21,  # OTel SeverityNumber.FATAL
}


def _require_otel() -> None:
    try:
        import opentelemetry  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "OpenTelemetry integration requires 'opentelemetry-sdk'. "
            "Install it with: pip install agentcop[otel]"
        ) from exc


def to_otel_attributes(event: SentinelEvent) -> dict:
    """
    Flatten a SentinelEvent into a flat OTel-compatible attribute dict.

    All `attributes` keys are namespaced under `sentinel.*` to avoid
    collisions with standard OTel resource/span attributes.
    """
    attrs: dict = {
        "sentinel.event_id": event.event_id,
        "sentinel.event_type": event.event_type,
        "sentinel.source_system": event.source_system,
    }
    if event.producer_id is not None:
        attrs["sentinel.producer_id"] = event.producer_id
    if event.trace_id is not None:
        attrs["sentinel.trace_id"] = event.trace_id
    if event.span_id is not None:
        attrs["sentinel.span_id"] = event.span_id
    for k, v in event.attributes.items():
        if v is not None:
            attrs[f"sentinel.{k}"] = str(v)
    return attrs


def to_otel_log_record(event: SentinelEvent):
    """
    Convert a SentinelEvent to an opentelemetry.sdk._logs.LogRecord.

    Requires ``opentelemetry-sdk`` to be installed.
    """
    _require_otel()
    from opentelemetry.sdk._logs import LogRecord  # type: ignore[import]
    from opentelemetry._logs.severity import SeverityNumber  # type: ignore[import]

    severity_number_value = _SEVERITY_NUMBER_MAP.get(event.severity, 9)
    severity_number = SeverityNumber(severity_number_value)

    timestamp_ns = int(
        event.timestamp.astimezone(timezone.utc).timestamp() * 1_000_000_000
    )
    observed_ns = int(
        event.observed_at.astimezone(timezone.utc).timestamp() * 1_000_000_000
    )

    return LogRecord(
        timestamp=timestamp_ns,
        observed_timestamp=observed_ns,
        severity_text=event.severity,
        severity_number=severity_number,
        body=event.body,
        attributes=to_otel_attributes(event),
        trace_id=int(event.trace_id, 16) if event.trace_id and len(event.trace_id) == 32 else 0,
        span_id=int(event.span_id, 16) if event.span_id and len(event.span_id) == 16 else 0,
    )


class OtelSentinelExporter:
    """
    Emit SentinelEvents as OTel log records through a LoggerProvider.

    Requires ``opentelemetry-sdk`` to be installed.

    Parameters
    ----------
    logger_provider:
        An ``opentelemetry.sdk._logs.LoggerProvider`` instance.
        If None, uses the global provider.
    instrumentation_name:
        Logger name used when acquiring the OTel logger (appears as
        ``otel.scope.name`` in most exporters).
    """

    def __init__(
        self,
        logger_provider=None,
        instrumentation_name: str = "sentinel-core",
    ):
        _require_otel()
        from opentelemetry.sdk._logs import LoggerProvider as _LP  # type: ignore[import]
        from opentelemetry._logs import get_logger_provider  # type: ignore[import]

        self._provider = logger_provider or get_logger_provider()
        self._logger = self._provider.get_logger(instrumentation_name)

    def export(self, events: Sequence[SentinelEvent]) -> None:
        """Emit each event as an OTel log record."""
        for event in events:
            record = to_otel_log_record(event)
            self._logger.emit(record)
