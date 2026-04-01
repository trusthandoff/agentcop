"""
Datadog adapter for agentcop.

Translates ddtrace spans — LLM, HTTP, database, and generic application spans —
into SentinelEvents for forensic auditing.

ddtrace instruments Python applications through a ``Tracer`` that collects
spans and writes complete traces to a ``TraceWriter`` for export to the Datadog
Agent. This adapter wraps ``tracer._writer.write()`` to intercept every
finished trace, converts each span in the trace into a ``SentinelEvent``, and
buffers it for later auditing.

The original ``write()`` method is always called, so Datadog Agent export is
unaffected.

Spans are classified by their ``component`` tag:

  AI/LLM spans (openai, anthropic, langchain, …): llm_span_finished / llm_span_error
  HTTP client/server spans (requests, httpx, …):  http_span_finished / http_span_error
  Database/cache spans (sqlalchemy, redis, …):    db_span_finished / db_span_error
  All other spans:                                 span_finished / span_error

Install the optional dependency to use this adapter:

    pip install agentcop[ddtrace]

Quickstart::

    import ddtrace
    from agentcop import Sentinel
    from agentcop.adapters.datadog import DatadogSentinelAdapter

    adapter = DatadogSentinelAdapter(run_id="run-001")
    adapter.setup()                          # wraps ddtrace.tracer._writer.write

    # ... your ddtrace-instrumented application code ...

    sentinel = Sentinel()
    adapter.flush_into(sentinel)
    violations = sentinel.detect_violations()
    sentinel.report()

``to_sentinel_event(raw)`` also accepts plain dicts for manual translation
or testing without a live tracer::

    event = adapter.to_sentinel_event({
        "type": "llm_span_error",
        "span_name": "openai.request",
        "service": "my-agent",
        "component": "openai",
        "model": "gpt-4o-mini",
        "error_message": "rate limit exceeded",
    })
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from agentcop.event import SentinelEvent


def _require_ddtrace() -> None:
    try:
        import ddtrace  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Datadog adapter requires 'ddtrace'. Install it with: pip install agentcop[ddtrace]"
        ) from exc


# Span component → event category mapping
_LLM_COMPONENTS = frozenset(
    {
        "openai",
        "anthropic",
        "cohere",
        "langchain",
        "llamaindex",
        "huggingface_hub",
        "bedrock",
        "vertexai",
        "ai21",
        "google-generativeai",
        "mistral",
        "azureopenai",
    }
)
_HTTP_COMPONENTS = frozenset(
    {
        "requests",
        "httpx",
        "urllib",
        "urllib3",
        "aiohttp",
        "grpc",
        "tornado",
        "flask",
        "django",
        "fastapi",
        "starlette",
        "aiohttp-client",
    }
)
_DB_COMPONENTS = frozenset(
    {
        "sqlalchemy",
        "psycopg",
        "psycopg2",
        "pymongo",
        "redis",
        "elasticsearch",
        "cassandra",
        "mysql-connector",
        "sqlite3",
        "mongoengine",
        "pymemcache",
        "aiopg",
        "asyncpg",
        "motor",
        "aiomysql",
    }
)


class DatadogSentinelAdapter:
    """
    Adapter that translates ddtrace spans into SentinelEvents.

    ddtrace instruments Python applications by wrapping library calls and
    submitting completed traces to a ``TraceWriter``. This adapter hooks
    into ``tracer._writer.write()`` to intercept every finished trace,
    converts each span to a categorized ``SentinelEvent`` based on its
    ``component`` tag, and buffers the events for auditing via
    ``flush_into()``.

    Span categories (determined by the ``component`` span tag):

    - **LLM** (openai, anthropic, langchain, …) → ``llm_span_finished`` / ``llm_span_error``
    - **HTTP** (requests, httpx, flask, …) → ``http_span_finished`` / ``http_span_error``
    - **DB** (sqlalchemy, redis, pymongo, …) → ``db_span_finished`` / ``db_span_error``
    - **Generic** (anything else) → ``span_finished`` / ``span_error``

    **Normalized dict schema** (what ``to_sentinel_event`` accepts):

    All dicts must have a ``"type"`` key. Additional keys are type-specific.

    +---------------------+-------------------------------+-----------+
    | type                | event_type (SentinelEvent)    | severity  |
    +=====================+===============================+===========+
    | span_finished       | span_finished                 | INFO      |
    | span_error          | span_error                    | ERROR     |
    | llm_span_finished   | llm_span_finished             | INFO      |
    | llm_span_error      | llm_span_error                | ERROR     |
    | http_span_finished  | http_span_finished            | INFO      |
    | http_span_error     | http_span_error               | ERROR     |
    | db_span_finished    | db_span_finished              | INFO      |
    | db_span_error       | db_span_error                 | ERROR     |
    | (anything else)     | unknown_datadog_event         | INFO      |
    +---------------------+-------------------------------+-----------+

    Parameters
    ----------
    run_id:
        Optional session identifier used as ``trace_id`` on every translated
        event. When ``None``, the Datadog trace ID (hex string) from the span
        is used instead.
    """

    source_system = "datadog"

    def __init__(self, run_id: str | None = None) -> None:
        _require_ddtrace()
        self._run_id = run_id
        self._buffer: list[SentinelEvent] = []
        self._lock = threading.Lock()

    def setup(self, tracer=None) -> None:
        """
        Wrap ``_writer.write`` on a ddtrace ``Tracer`` instance.

        Every completed trace submitted to the writer is intercepted: each
        span in the trace is converted to a categorized ``SentinelEvent``
        and buffered. The original ``write()`` is always called so Datadog
        Agent export is unaffected.

        Parameters
        ----------
        tracer:
            A ``ddtrace.Tracer`` instance. When ``None``, the global
            ``ddtrace.tracer`` singleton is used (reads ``DD_*`` env vars
            for Agent endpoint configuration).
        """
        if tracer is None:
            import ddtrace as _ddtrace  # type: ignore[import]

            tracer = _ddtrace.tracer

        writer = getattr(tracer, "_writer", None)
        if writer is None:
            writer = getattr(tracer, "writer", None)
        if writer is None:
            raise RuntimeError(
                "Could not find a writer on the provided ddtrace tracer. "
                "Ensure ddtrace >= 1.0 is installed."
            )

        adapter_self = self
        original_write = writer.write

        def _intercepted_write(spans):
            for span in spans or []:
                try:
                    raw = _span_to_raw(span)
                    adapter_self._buffer_event(adapter_self.to_sentinel_event(raw))
                except Exception:
                    pass  # never let adapter errors disrupt ddtrace export
            return original_write(spans)

        writer.write = _intercepted_write

    def to_sentinel_event(self, raw: dict[str, Any]) -> SentinelEvent:
        """
        Translate one Datadog span dict into a SentinelEvent.

        ``raw`` must contain a ``"type"`` key. All other keys are
        type-specific. Unknown types map to ``unknown_datadog_event``
        with severity INFO.
        """
        dispatch = {
            "span_finished": self._from_span_finished,
            "span_error": self._from_span_error,
            "llm_span_finished": self._from_llm_span_finished,
            "llm_span_error": self._from_llm_span_error,
            "http_span_finished": self._from_http_span_finished,
            "http_span_error": self._from_http_span_error,
            "db_span_finished": self._from_db_span_finished,
            "db_span_error": self._from_db_span_error,
        }
        handler = dispatch.get(raw.get("type", ""), self._from_unknown)
        return handler(raw)

    def drain(self) -> list[SentinelEvent]:
        """Return all buffered SentinelEvents and clear the buffer."""
        with self._lock:
            events = list(self._buffer)
            self._buffer.clear()
            return events

    def flush_into(self, sentinel) -> None:
        """Ingest all buffered events into a Sentinel instance, then clear."""
        sentinel.ingest(self.drain())

    # ------------------------------------------------------------------
    # Internal buffer
    # ------------------------------------------------------------------

    def _buffer_event(self, event: SentinelEvent) -> None:
        with self._lock:
            self._buffer.append(event)

    # ------------------------------------------------------------------
    # Timestamp helper
    # ------------------------------------------------------------------

    def _parse_timestamp(self, raw: dict[str, Any]) -> datetime:
        ts = raw.get("timestamp")
        if ts:
            try:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(UTC)

    # ------------------------------------------------------------------
    # Trace-ID helper: prefer run_id; fall back to Datadog trace ID
    # ------------------------------------------------------------------

    def _trace_id(self, raw: dict[str, Any]) -> str | None:
        if self._run_id:
            return self._run_id
        dd = raw.get("dd_trace_id", "")
        return dd or None

    # ------------------------------------------------------------------
    # Private translators — generic span
    # ------------------------------------------------------------------

    def _from_span_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        span_name = raw.get("span_name", "unknown")
        service = raw.get("service", "unknown")
        return SentinelEvent(
            event_id=f"dd-span-{uuid.uuid4()}",
            event_type="span_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"span '{span_name}' [{service}] finished",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    def _from_span_error(self, raw: dict[str, Any]) -> SentinelEvent:
        span_name = raw.get("span_name", "unknown")
        service = raw.get("service", "unknown")
        error_message = raw.get("error_message", "")
        return SentinelEvent(
            event_id=f"dd-span-{uuid.uuid4()}",
            event_type="span_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"span '{span_name}' [{service}] error: {error_message}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    # ------------------------------------------------------------------
    # Private translators — LLM span
    # ------------------------------------------------------------------

    def _from_llm_span_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        span_name = raw.get("span_name", "unknown")
        model = raw.get("model", "unknown")
        provider = raw.get("provider", raw.get("component", "unknown"))
        return SentinelEvent(
            event_id=f"dd-llm-{uuid.uuid4()}",
            event_type="llm_span_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"llm span '{span_name}' ({provider}) finished (model='{model}')",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes={**self._common_attrs(raw), **self._llm_attrs(raw)},
        )

    def _from_llm_span_error(self, raw: dict[str, Any]) -> SentinelEvent:
        span_name = raw.get("span_name", "unknown")
        model = raw.get("model", "unknown")
        provider = raw.get("provider", raw.get("component", "unknown"))
        error_message = raw.get("error_message", "")
        return SentinelEvent(
            event_id=f"dd-llm-{uuid.uuid4()}",
            event_type="llm_span_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"llm span '{span_name}' ({provider}) error (model='{model}'): {error_message}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes={**self._common_attrs(raw), **self._llm_attrs(raw)},
        )

    # ------------------------------------------------------------------
    # Private translators — HTTP span
    # ------------------------------------------------------------------

    def _from_http_span_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        span_name = raw.get("span_name", "unknown")
        http_url = raw.get("http_url", "")
        http_status = raw.get("http_status_code", "")
        return SentinelEvent(
            event_id=f"dd-http-{uuid.uuid4()}",
            event_type="http_span_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"http span '{span_name}' {http_url} [{http_status}] finished",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes={**self._common_attrs(raw), **self._http_attrs(raw)},
        )

    def _from_http_span_error(self, raw: dict[str, Any]) -> SentinelEvent:
        span_name = raw.get("span_name", "unknown")
        http_url = raw.get("http_url", "")
        error_message = raw.get("error_message", "")
        return SentinelEvent(
            event_id=f"dd-http-{uuid.uuid4()}",
            event_type="http_span_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"http span '{span_name}' {http_url} error: {error_message}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes={**self._common_attrs(raw), **self._http_attrs(raw)},
        )

    # ------------------------------------------------------------------
    # Private translators — DB span
    # ------------------------------------------------------------------

    def _from_db_span_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        span_name = raw.get("span_name", "unknown")
        component = raw.get("component", "unknown")
        return SentinelEvent(
            event_id=f"dd-db-{uuid.uuid4()}",
            event_type="db_span_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"db span '{span_name}' [{component}] finished",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    def _from_db_span_error(self, raw: dict[str, Any]) -> SentinelEvent:
        span_name = raw.get("span_name", "unknown")
        component = raw.get("component", "unknown")
        error_message = raw.get("error_message", "")
        return SentinelEvent(
            event_id=f"dd-db-{uuid.uuid4()}",
            event_type="db_span_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"db span '{span_name}' [{component}] error: {error_message}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    # ------------------------------------------------------------------
    # Private translator — unknown
    # ------------------------------------------------------------------

    def _from_unknown(self, raw: dict[str, Any]) -> SentinelEvent:
        original_type = raw.get("type", "unknown")
        return SentinelEvent(
            event_id=f"dd-unknown-{uuid.uuid4()}",
            event_type="unknown_datadog_event",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"unknown Datadog event type '{original_type}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"original_type": original_type},
        )

    # ------------------------------------------------------------------
    # Attribute helpers
    # ------------------------------------------------------------------

    def _common_attrs(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "span_name": raw.get("span_name", ""),
            "resource": raw.get("resource", ""),
            "service": raw.get("service", ""),
            "component": raw.get("component", ""),
            "span_kind": raw.get("span_kind", ""),
            "dd_trace_id": raw.get("dd_trace_id", ""),
            "dd_span_id": raw.get("dd_span_id", ""),
            "dd_parent_id": raw.get("dd_parent_id", ""),
            "error": bool(raw.get("error", False)),
            "error_type": raw.get("error_type", ""),
            "error_message": raw.get("error_message", ""),
            "duration_ns": int(raw.get("duration_ns", 0)),
        }

    def _llm_attrs(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": raw.get("model", "unknown"),
            "provider": raw.get("provider", raw.get("component", "unknown")),
            "usage": dict(raw.get("usage") or {}),
        }

    def _http_attrs(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "http_url": raw.get("http_url", ""),
            "http_status_code": raw.get("http_status_code", ""),
            "http_method": raw.get("http_method", ""),
        }


# ---------------------------------------------------------------------------
# Module-level helpers — ddtrace Span → normalized dict
# ---------------------------------------------------------------------------


def _ns_to_iso(ns: int | None) -> str | None:
    """Convert nanoseconds-since-epoch to an ISO 8601 string, or None."""
    if not ns:
        return None
    try:
        return datetime.fromtimestamp(ns / 1e9, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _get_tag(span, key: str, default: str = "") -> str:
    """Safely get a string tag from a ddtrace span."""
    try:
        val = span.get_tag(key)
        return str(val) if val is not None else default
    except Exception:
        return default


def _get_numeric(span, *keys: str) -> float | None:
    """
    Try ``get_metric`` then ``get_tag`` for numeric fields across ddtrace
    versions. Returns the first non-None value found, or ``None``.
    """
    for key in keys:
        for getter_name in ("get_metric", "get_tag"):
            try:
                getter = getattr(span, getter_name, None)
                if getter is None:
                    continue
                val = getter(key)
                if val is not None:
                    return float(val)
            except Exception:
                pass
    return None


def _span_to_raw(span) -> dict[str, Any]:
    """
    Convert a finished ddtrace ``Span`` to a normalized event dict.

    Categorizes the span by its ``component`` tag and sets ``"type"``
    accordingly. LLM spans include model, provider, and usage attributes;
    HTTP spans include URL, method, and status code.
    """
    component = _get_tag(span, "component")
    span_kind = _get_tag(span, "span.kind")
    is_error = bool(getattr(span, "error", 0))

    if component in _LLM_COMPONENTS:
        event_type = "llm_span_error" if is_error else "llm_span_finished"
    elif component in _HTTP_COMPONENTS:
        event_type = "http_span_error" if is_error else "http_span_finished"
    elif component in _DB_COMPONENTS:
        event_type = "db_span_error" if is_error else "db_span_finished"
    else:
        event_type = "span_error" if is_error else "span_finished"

    trace_id_int = getattr(span, "trace_id", 0) or 0
    span_id_int = getattr(span, "span_id", 0) or 0
    parent_id_int = getattr(span, "parent_id", 0) or 0

    # Timestamp: prefer start_ns (nanoseconds, ddtrace >= 1.x)
    # Fall back to start (may be nanoseconds or float seconds depending on version)
    start_ns = getattr(span, "start_ns", None)
    if start_ns is None:
        raw_start = getattr(span, "start", None)
        if raw_start is not None:
            f = float(raw_start)
            # Heuristic: values > 1e13 are already nanoseconds
            start_ns = int(f) if f > 1e13 else int(f * 1e9)

    raw: dict[str, Any] = {
        "type": event_type,
        "span_name": str(getattr(span, "name", "") or ""),
        "resource": str(getattr(span, "resource", "") or ""),
        "service": str(getattr(span, "service", "") or ""),
        "component": component,
        "span_kind": span_kind,
        "dd_trace_id": format(trace_id_int, "016x") if trace_id_int else "",
        "dd_span_id": format(span_id_int, "016x") if span_id_int else "",
        "dd_parent_id": format(parent_id_int, "016x") if parent_id_int else "",
        "error": is_error,
        "error_type": _get_tag(span, "error.type"),
        "error_message": _get_tag(span, "error.message"),
        "http_status_code": _get_tag(span, "http.status_code"),
        "http_url": _get_tag(span, "http.url"),
        "http_method": _get_tag(span, "http.method"),
        "duration_ns": int(getattr(span, "duration", 0) or 0),
        "timestamp": _ns_to_iso(start_ns),
    }

    if component in _LLM_COMPONENTS:
        model = (
            _get_tag(span, "ai.model.name")
            or _get_tag(span, "openai.request.model")
            or _get_tag(span, "langchain.request.llm.model_name")
            or "unknown"
        )
        raw["model"] = model
        raw["provider"] = component
        pt = _get_numeric(span, "llm.usage.prompt_tokens", "openai.response.usage.prompt_tokens")
        ct = _get_numeric(
            span, "llm.usage.completion_tokens", "openai.response.usage.completion_tokens"
        )
        tt = _get_numeric(span, "llm.usage.total_tokens", "openai.response.usage.total_tokens")
        raw["usage"] = {
            "prompt_tokens": int(pt) if pt is not None else 0,
            "completion_tokens": int(ct) if ct is not None else 0,
            "total_tokens": int(tt) if tt is not None else 0,
        }

    return raw
