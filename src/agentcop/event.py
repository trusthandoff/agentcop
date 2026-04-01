import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SentinelEvent(BaseModel):
    """
    Universal forensic event — OTel Log Data Model aligned.

    OTel field mapping:
      TraceId           → trace_id
      SpanId            → span_id
      Timestamp         → timestamp
      ObservedTimestamp → observed_at
      SeverityText      → severity
      Body              → body
      Attributes        → attributes

    Domain-specific fields (packet_id, capability_id, etc.) live in
    `attributes` — the same pattern OTel uses for instrumentation libraries.
    `source_system` identifies the adapter that produced the event.
    """

    event_id: str
    event_type: str
    timestamp: datetime
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    severity: Literal["INFO", "WARN", "ERROR", "CRITICAL"]
    producer_id: str | None = None
    trace_id: str | None = None  # OTel TraceId / correlation_id
    span_id: str | None = None  # OTel SpanId, optional
    body: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_system: str


class ViolationRecord(BaseModel):
    """
    Structured output of a violation detector.

    `source_event_id` links back to the SentinelEvent that triggered detection.
    `detail` carries violation-specific fields (reason, capability_id, model, …).
    """

    violation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    violation_type: str
    severity: Literal["WARN", "ERROR", "CRITICAL"]
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_event_id: str
    trace_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
