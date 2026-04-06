"""
Langfuse adapter for agentcop.

Translates Langfuse 4.x observation spans — generations, spans, tools,
retrievers, events, and guardrails — into SentinelEvents for forensic
auditing.

Langfuse 4.x is built entirely on top of OpenTelemetry. Every observation
(``start_as_current_observation``, ``@observe``, etc.) becomes an OTel span
with Langfuse-specific attributes injected. This adapter registers a custom
``SpanProcessor`` on the Langfuse ``TracerProvider``, fires
``observation_started`` when a span opens, and the appropriate
``*_finished`` / ``*_error`` event when it closes.

Supported event categories and their SentinelEvent mapping:

  All types:    observation_started
  Span/Agent/Chain/Evaluator: span_finished / span_error
  Generation/Embedding:       generation_finished / generation_error
  Tool:                       tool_finished / tool_error
  Retriever:                  retriever_finished / retriever_error
  Event:                      event_occurred
  Guardrail:                  guardrail_finished / guardrail_error

Install the optional dependency to use this adapter:

    pip install agentcop[langfuse]

Quickstart::

    from langfuse import Langfuse
    from agentcop import Sentinel
    from agentcop.adapters.langfuse import LangfuseSentinelAdapter

    langfuse = Langfuse()                    # reads LANGFUSE_* env vars
    adapter = LangfuseSentinelAdapter(run_id="run-001")
    adapter.setup(langfuse)                  # registers SpanProcessor

    with langfuse.start_as_current_observation(name="my-pipeline") as root:
        with langfuse.start_as_current_observation(
            name="gpt-call",
            as_type="generation",
            model="gpt-4o-mini",
        ) as gen:
            gen.update(output="Hello!", usage_details={"prompt_tokens": 5, "completion_tokens": 3})

    langfuse.flush()

    sentinel = Sentinel()
    adapter.flush_into(sentinel)
    violations = sentinel.detect_violations()
    sentinel.report()

``to_sentinel_event(raw)`` also accepts plain dicts for manual translation
or testing without a live Langfuse client::

    event = adapter.to_sentinel_event({
        "type": "generation_error",
        "observation_name": "gpt-call",
        "model": "gpt-4o-mini",
        "status_message": "rate limit exceeded",
    })
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from agentcop.adapters._runtime import check_tool_call, fire_security_event
from agentcop.event import SentinelEvent


def _require_langfuse() -> None:
    try:
        import langfuse  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Langfuse adapter requires 'langfuse'. Install it with: pip install agentcop[langfuse]"
        ) from exc


# Observation type → event category mapping
_SPAN_TYPES = frozenset({"span", "agent", "chain", "evaluator"})
_GENERATION_TYPES = frozenset({"generation", "embedding"})
_TOOL_TYPES = frozenset({"tool"})
_RETRIEVER_TYPES = frozenset({"retriever"})
_EVENT_TYPES = frozenset({"event"})
_GUARDRAIL_TYPES = frozenset({"guardrail"})


class LangfuseSentinelAdapter:
    """
    Adapter that translates Langfuse 4.x observation spans into SentinelEvents.

    Langfuse 4.x instruments all observations through a ``TracerProvider``
    backed by OpenTelemetry. This adapter registers a ``SpanProcessor`` that
    fires on every span start and end. All Langfuse-specific attributes
    (model, usage, level, input, output, …) are read directly from the
    completed OTel ``ReadableSpan`` at ``on_end`` time.

    **Normalized dict schema** (what ``to_sentinel_event`` accepts):

    All dicts must have a ``"type"`` key matching one of the supported event
    type strings. Additional keys are type-specific (see table below).

    +-------------------------+-------------------------------+-----------+
    | type                    | event_type (SentinelEvent)    | severity  |
    +=========================+===============================+===========+
    | observation_started     | observation_started           | INFO      |
    | span_finished           | span_finished                 | INFO      |
    | span_error              | span_error                    | ERROR     |
    | generation_finished     | generation_finished           | INFO      |
    | generation_error        | generation_error              | ERROR     |
    | tool_finished           | tool_finished                 | INFO      |
    | tool_error              | tool_error                    | ERROR     |
    | retriever_finished      | retriever_finished            | INFO      |
    | retriever_error         | retriever_error               | ERROR     |
    | event_occurred          | event_occurred                | INFO      |
    | guardrail_finished      | guardrail_finished            | INFO      |
    | guardrail_error         | guardrail_error               | ERROR     |
    | (anything else)         | unknown_langfuse_event        | INFO      |
    +-------------------------+-------------------------------+-----------+

    Parameters
    ----------
    run_id:
        Optional run / session identifier used as ``trace_id`` on every
        translated event. When omitted the Langfuse trace ID (32-char hex)
        extracted from the span context is used instead.
    """

    source_system = "langfuse"

    def __init__(
        self,
        run_id: str | None = None,
        *,
        gate=None,
        permissions=None,
        sandbox=None,
        approvals=None,
        identity=None,
    ) -> None:
        _require_langfuse()
        self._run_id = run_id
        self._gate = gate
        self._permissions = permissions
        self._sandbox = sandbox
        self._approvals = approvals
        self._identity = identity
        self._buffer: list[SentinelEvent] = []
        self._lock = threading.Lock()

    def setup(self, langfuse_client=None) -> None:
        """
        Register a ``SpanProcessor`` observer on the Langfuse tracer provider.

        The processor fires ``observation_started`` when any Langfuse span
        opens and the appropriate ``*_finished`` / ``*_error`` event when it
        closes. Non-Langfuse OTel spans (e.g. from other instrumentation
        libraries) are silently skipped.

        Parameters
        ----------
        langfuse_client:
            A ``langfuse.Langfuse`` instance. When ``None``, the global
            singleton returned by ``langfuse.get_client()`` is used. Pass an
            explicit client when working with multiple Langfuse projects or
            in tests.
        """
        if langfuse_client is not None:
            client = langfuse_client
        else:
            from langfuse import get_client  # type: ignore[import]

            client = get_client()

        from opentelemetry.sdk.trace import SpanProcessor  # type: ignore[import]

        adapter_self = self

        class _LangfuseObserver(SpanProcessor):
            def on_start(self, span, parent_context=None):
                attrs = getattr(span, "attributes", None) or {}
                obs_type = attrs.get("langfuse.observation.type")
                if not obs_type:
                    return
                # Log gate decisions for tool observations.
                if obs_type in _TOOL_TYPES and (
                    adapter_self._gate or adapter_self._permissions
                ):
                    span_name = getattr(span, "name", "unknown")
                    try:
                        check_tool_call(adapter_self, span_name, dict(attrs))
                    except PermissionError:
                        pass  # already buffered as gate_denied / permission_violation
                raw = _span_to_raw_start(span)
                if raw is not None:
                    adapter_self._buffer_event(adapter_self.to_sentinel_event(raw))

            def on_end(self, span):
                attrs = getattr(span, "attributes", None) or {}
                if not attrs.get("langfuse.observation.type"):
                    return
                raw = _span_to_raw_end(span)
                if raw is not None:
                    adapter_self._buffer_event(adapter_self.to_sentinel_event(raw))

            def shutdown(self):
                pass

            def force_flush(self, timeout_millis=30000):
                pass

        # Langfuse 4.x stores the TracerProvider on client._resources.tracer_provider
        tracer_provider = getattr(getattr(client, "_resources", None), "tracer_provider", None)
        if tracer_provider is None:
            # Fallback for alternate client structures
            tracer_provider = getattr(client, "tracer_provider", None)
        if tracer_provider is not None:
            tracer_provider.add_span_processor(_LangfuseObserver())

    def to_sentinel_event(self, raw: dict[str, Any]) -> SentinelEvent:
        """
        Translate one Langfuse event dict into a SentinelEvent.

        ``raw`` must contain a ``"type"`` key. All other keys are
        type-specific. Unknown types map to ``unknown_langfuse_event``
        with severity INFO.
        """
        dispatch = {
            "observation_started": self._from_observation_started,
            "span_finished": self._from_span_finished,
            "span_error": self._from_span_error,
            "generation_finished": self._from_generation_finished,
            "generation_error": self._from_generation_error,
            "tool_finished": self._from_tool_finished,
            "tool_error": self._from_tool_error,
            "retriever_finished": self._from_retriever_finished,
            "retriever_error": self._from_retriever_error,
            "event_occurred": self._from_event_occurred,
            "guardrail_finished": self._from_guardrail_finished,
            "guardrail_error": self._from_guardrail_error,
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
    # Trace-ID helper: prefer run_id; fall back to Langfuse trace ID
    # ------------------------------------------------------------------

    def _trace_id(self, raw: dict[str, Any]) -> str | None:
        if self._run_id:
            return self._run_id
        lf = raw.get("langfuse_trace_id", "")
        return lf or None

    # ------------------------------------------------------------------
    # Private translators — observation_started
    # ------------------------------------------------------------------

    def _from_observation_started(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_type = raw.get("observation_type", "unknown")
        obs_name = raw.get("observation_name", "unknown")
        return SentinelEvent(
            event_id=f"langfuse-obs-{uuid.uuid4()}",
            event_type="observation_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"{obs_type} '{obs_name}' started",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes={
                "observation_type": obs_type,
                "observation_name": obs_name,
                "langfuse_trace_id": raw.get("langfuse_trace_id", ""),
                "observation_id": raw.get("observation_id", ""),
                "parent_observation_id": raw.get("parent_observation_id", ""),
                "user_id": raw.get("user_id", ""),
                "session_id": raw.get("session_id", ""),
            },
        )

    # ------------------------------------------------------------------
    # Private translators — span (span / agent / chain / evaluator)
    # ------------------------------------------------------------------

    def _from_span_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_type = raw.get("observation_type", "span")
        obs_name = raw.get("observation_name", "unknown")
        return SentinelEvent(
            event_id=f"langfuse-span-{uuid.uuid4()}",
            event_type="span_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"{obs_type} '{obs_name}' finished",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    def _from_span_error(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_type = raw.get("observation_type", "span")
        obs_name = raw.get("observation_name", "unknown")
        status_message = raw.get("status_message", "")
        return SentinelEvent(
            event_id=f"langfuse-span-{uuid.uuid4()}",
            event_type="span_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"{obs_type} '{obs_name}' error: {status_message}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    # ------------------------------------------------------------------
    # Private translators — generation (generation / embedding)
    # ------------------------------------------------------------------

    def _from_generation_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_type = raw.get("observation_type", "generation")
        obs_name = raw.get("observation_name", "unknown")
        model = raw.get("model", "unknown")
        return SentinelEvent(
            event_id=f"langfuse-gen-{uuid.uuid4()}",
            event_type="generation_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"{obs_type} '{obs_name}' finished (model='{model}')",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes={**self._common_attrs(raw), **self._generation_attrs(raw)},
        )

    def _from_generation_error(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_type = raw.get("observation_type", "generation")
        obs_name = raw.get("observation_name", "unknown")
        model = raw.get("model", "unknown")
        status_message = raw.get("status_message", "")
        return SentinelEvent(
            event_id=f"langfuse-gen-{uuid.uuid4()}",
            event_type="generation_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"{obs_type} '{obs_name}' error (model='{model}'): {status_message}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes={**self._common_attrs(raw), **self._generation_attrs(raw)},
        )

    # ------------------------------------------------------------------
    # Private translators — tool
    # ------------------------------------------------------------------

    def _from_tool_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_name = raw.get("observation_name", "unknown")
        return SentinelEvent(
            event_id=f"langfuse-tool-{uuid.uuid4()}",
            event_type="tool_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"tool '{obs_name}' finished",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    def _from_tool_error(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_name = raw.get("observation_name", "unknown")
        status_message = raw.get("status_message", "")
        return SentinelEvent(
            event_id=f"langfuse-tool-{uuid.uuid4()}",
            event_type="tool_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"tool '{obs_name}' error: {status_message}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    # ------------------------------------------------------------------
    # Private translators — retriever
    # ------------------------------------------------------------------

    def _from_retriever_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_name = raw.get("observation_name", "unknown")
        return SentinelEvent(
            event_id=f"langfuse-retriever-{uuid.uuid4()}",
            event_type="retriever_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"retriever '{obs_name}' finished",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    def _from_retriever_error(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_name = raw.get("observation_name", "unknown")
        status_message = raw.get("status_message", "")
        return SentinelEvent(
            event_id=f"langfuse-retriever-{uuid.uuid4()}",
            event_type="retriever_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"retriever '{obs_name}' error: {status_message}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    # ------------------------------------------------------------------
    # Private translator — event_occurred
    # ------------------------------------------------------------------

    def _from_event_occurred(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_name = raw.get("observation_name", "unknown")
        return SentinelEvent(
            event_id=f"langfuse-event-{uuid.uuid4()}",
            event_type="event_occurred",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"event '{obs_name}' occurred",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    # ------------------------------------------------------------------
    # Private translators — guardrail
    # ------------------------------------------------------------------

    def _from_guardrail_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_name = raw.get("observation_name", "unknown")
        return SentinelEvent(
            event_id=f"langfuse-guardrail-{uuid.uuid4()}",
            event_type="guardrail_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"guardrail '{obs_name}' finished",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._common_attrs(raw),
        )

    def _from_guardrail_error(self, raw: dict[str, Any]) -> SentinelEvent:
        obs_name = raw.get("observation_name", "unknown")
        status_message = raw.get("status_message", "")
        return SentinelEvent(
            event_id=f"langfuse-guardrail-{uuid.uuid4()}",
            event_type="guardrail_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"guardrail '{obs_name}' error: {status_message}",
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
            event_id=f"langfuse-unknown-{uuid.uuid4()}",
            event_type="unknown_langfuse_event",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"unknown Langfuse event type '{original_type}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"original_type": original_type},
        )

    # ------------------------------------------------------------------
    # Attribute helpers
    # ------------------------------------------------------------------

    def _common_attrs(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "observation_type": raw.get("observation_type", "unknown"),
            "observation_name": raw.get("observation_name", "unknown"),
            "langfuse_trace_id": raw.get("langfuse_trace_id", ""),
            "observation_id": raw.get("observation_id", ""),
            "parent_observation_id": raw.get("parent_observation_id", ""),
            "level": raw.get("level", "DEFAULT"),
            "status_message": raw.get("status_message", ""),
            "input": raw.get("input", ""),
            "output": raw.get("output", ""),
            "user_id": raw.get("user_id", ""),
            "session_id": raw.get("session_id", ""),
        }

    def _generation_attrs(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": raw.get("model", "unknown"),
            "usage": raw.get("usage") or {},
            "cost": raw.get("cost") or {},
            "prompt_name": raw.get("prompt_name", ""),
            "prompt_version": raw.get("prompt_version", ""),
        }


# ---------------------------------------------------------------------------
# Module-level helpers — OTel span → normalized dict
# ---------------------------------------------------------------------------


def _safe_json_load(s: Any) -> Any:
    """Parse a JSON string into a Python object; return the raw value on failure."""
    if not isinstance(s, str):
        return s
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


def _ns_to_iso(ns: int | None) -> str | None:
    """Convert nanoseconds-since-epoch to an ISO 8601 string, or None."""
    if not ns:
        return None
    try:
        return datetime.fromtimestamp(ns / 1e9, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _is_error(span) -> bool:
    """
    Return True when the span represents an error observation.

    Checks ``langfuse.observation.level == "ERROR"`` first, then falls back
    to inspecting the OTel ``StatusCode`` (value 2 == ERROR in the Python SDK).
    """
    attrs = getattr(span, "attributes", None) or {}
    if attrs.get("langfuse.observation.level") == "ERROR":
        return True
    status = getattr(span, "_status", None) or getattr(span, "status", None)
    if status is not None:
        code = getattr(status, "status_code", None)
        if code is not None:
            # StatusCode.ERROR has value 2 in opentelemetry-sdk
            code_val = getattr(code, "value", code)
            if code_val == 2:
                return True
    return False


def _extract_ids(span) -> tuple[str, str, str]:
    """Return (langfuse_trace_id, observation_id, parent_observation_id) as hex strings."""
    ctx = getattr(span, "_context", None) or getattr(span, "context", None)
    trace_id_int = getattr(ctx, "trace_id", 0) if ctx else 0
    span_id_int = getattr(ctx, "span_id", 0) if ctx else 0

    parent = getattr(span, "parent", None)
    parent_id_int = getattr(parent, "span_id", 0) if parent else 0

    lf_trace_id = format(trace_id_int, "032x") if trace_id_int else ""
    obs_id = format(span_id_int, "016x") if span_id_int else ""
    parent_obs_id = format(parent_id_int, "016x") if parent_id_int else ""
    return lf_trace_id, obs_id, parent_obs_id


def _span_to_raw_start(span) -> dict[str, Any] | None:
    """
    Convert an OTel span (received in ``SpanProcessor.on_start``) to a
    normalized ``observation_started`` dict, or ``None`` if the span has no
    Langfuse observation type.

    At ``on_start`` time most attributes are already set by the Langfuse SDK
    constructor, but ``output``, ``usage_details``, etc. are not yet
    populated — those are only available at ``on_end``.
    """
    attrs = getattr(span, "attributes", None) or {}
    obs_type = attrs.get("langfuse.observation.type")
    if not obs_type:
        return None

    lf_trace_id, obs_id, parent_obs_id = _extract_ids(span)
    start_ns = getattr(span, "_start_time", None) or getattr(span, "start_time", None)

    return {
        "type": "observation_started",
        "observation_type": obs_type,
        "observation_name": getattr(span, "name", None) or "unknown",
        "langfuse_trace_id": lf_trace_id,
        "observation_id": obs_id,
        "parent_observation_id": parent_obs_id,
        "user_id": str(attrs.get("user.id", "") or ""),
        "session_id": str(attrs.get("session.id", "") or ""),
        "timestamp": _ns_to_iso(start_ns),
    }


def _span_to_raw_end(span) -> dict[str, Any] | None:
    """
    Convert a completed OTel ``ReadableSpan`` (received in
    ``SpanProcessor.on_end``) to a normalized event dict.

    All Langfuse attributes are available at this point: input, output,
    model, usage_details, level, status_message, etc.
    """
    attrs = getattr(span, "attributes", None) or {}
    obs_type = attrs.get("langfuse.observation.type")
    if not obs_type:
        return None

    lf_trace_id, obs_id, parent_obs_id = _extract_ids(span)
    end_ns = getattr(span, "_end_time", None) or getattr(span, "end_time", None)
    is_err = _is_error(span)

    base: dict[str, Any] = {
        "observation_type": obs_type,
        "observation_name": getattr(span, "name", None) or "unknown",
        "langfuse_trace_id": lf_trace_id,
        "observation_id": obs_id,
        "parent_observation_id": parent_obs_id,
        "level": str(attrs.get("langfuse.observation.level", "") or "DEFAULT"),
        "status_message": str(attrs.get("langfuse.observation.status_message", "") or ""),
        "input": str(attrs.get("langfuse.observation.input", "") or "")[:500],
        "output": str(attrs.get("langfuse.observation.output", "") or "")[:500],
        "user_id": str(attrs.get("user.id", "") or ""),
        "session_id": str(attrs.get("session.id", "") or ""),
        "timestamp": _ns_to_iso(end_ns),
    }

    if obs_type in _SPAN_TYPES:
        base["type"] = "span_error" if is_err else "span_finished"

    elif obs_type in _GENERATION_TYPES:
        base["type"] = "generation_error" if is_err else "generation_finished"
        base["model"] = str(attrs.get("langfuse.observation.model.name", "") or "unknown")
        usage_raw = attrs.get("langfuse.observation.usage_details")
        base["usage"] = _safe_json_load(usage_raw) if usage_raw else {}
        cost_raw = attrs.get("langfuse.observation.cost_details")
        base["cost"] = _safe_json_load(cost_raw) if cost_raw else {}
        base["prompt_name"] = str(attrs.get("langfuse.observation.prompt.name", "") or "")
        base["prompt_version"] = str(attrs.get("langfuse.observation.prompt.version", "") or "")

    elif obs_type in _TOOL_TYPES:
        base["type"] = "tool_error" if is_err else "tool_finished"

    elif obs_type in _RETRIEVER_TYPES:
        base["type"] = "retriever_error" if is_err else "retriever_finished"

    elif obs_type in _EVENT_TYPES:
        base["type"] = "event_occurred"

    elif obs_type in _GUARDRAIL_TYPES:
        base["type"] = "guardrail_error" if is_err else "guardrail_finished"

    else:
        base["type"] = "unknown_langfuse_event"

    return base
