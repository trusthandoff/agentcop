"""
Haystack adapter for agentcop.

Translates Haystack 2.x pipeline and component span events into SentinelEvents
for forensic auditing. Hooks into Haystack's ``ProxyTracer`` by replacing
``tracer.provided_tracer`` with a wrapping tracer that buffers events on every
pipeline and component run.

Supported event categories and their SentinelEvent mapping:

  Pipeline:  pipeline_started / pipeline_finished / pipeline_error
  Component: component_started / component_finished / component_error
  LLM:       llm_run_started / llm_run_finished / llm_run_error
  Retriever: retriever_run_started / retriever_run_finished
  Embedder:  embedder_run_started / embedder_run_finished

Install the optional dependency to use this adapter:

    pip install agentcop[haystack]

Quickstart::

    from haystack import Pipeline
    from haystack.components.generators import OpenAIGenerator
    from haystack.components.builders import PromptBuilder
    from agentcop import Sentinel
    from agentcop.adapters.haystack import HaystackSentinelAdapter

    adapter = HaystackSentinelAdapter(run_id="run-001")
    adapter.setup()          # replace haystack.tracing.tracer.provided_tracer

    pipe = Pipeline()
    pipe.add_component("prompt_builder", PromptBuilder(template="Answer: {{query}}"))
    pipe.add_component("llm", OpenAIGenerator(model="gpt-4o-mini"))
    pipe.connect("prompt_builder.prompt", "llm.prompt")

    result = pipe.run({"prompt_builder": {"query": "What is Haystack?"}})

    sentinel = Sentinel()
    adapter.flush_into(sentinel)
    violations = sentinel.detect_violations()
    sentinel.report()

``to_sentinel_event(raw)`` also accepts plain dicts for manual translation
or testing without a live pipeline::

    event = adapter.to_sentinel_event({
        "type": "llm_run_error",
        "component_name": "llm",
        "model": "gpt-4o-mini",
        "error": "rate limit exceeded",
    })
"""

from __future__ import annotations

import contextlib
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from agentcop.adapters._runtime import check_tool_call, record_trust_node
from agentcop.event import SentinelEvent


def _require_haystack() -> None:
    try:
        import haystack  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Haystack adapter requires 'haystack-ai'. "
            "Install it with: pip install agentcop[haystack]"
        ) from exc


class _SpanProxy:
    """
    Thin wrapper around a real Haystack span that records every tag set
    during a component run so they can be inspected after the ``with`` block.

    Forwards all calls to the underlying span (if any), preserving any OTel
    or Datadog tracer that was already registered.
    """

    def __init__(self, real=None) -> None:
        self._real = real
        self._tags: dict[str, Any] = {}

    def set_tag(self, key: str, value: Any) -> None:
        self._tags[key] = value
        if self._real is not None:
            with contextlib.suppress(Exception):
                self._real.set_tag(key, value)

    def set_content_tag(self, key: str, value: Any) -> None:
        self._tags[key] = value
        if self._real is not None:
            with contextlib.suppress(Exception):
                self._real.set_content_tag(key, value)

    def raw_span(self) -> Any:
        if self._real is not None:
            try:
                return self._real.raw_span()
            except Exception:
                pass
        return None


class HaystackSentinelAdapter:
    """
    Adapter that translates Haystack 2.x pipeline and component events into
    SentinelEvents.

    Haystack instruments pipeline and component execution through a
    ``ProxyTracer`` (``haystack.tracing.tracer``). This adapter wraps
    whatever ``provided_tracer`` is currently configured, forwards all calls
    to it, and additionally buffers translated SentinelEvents for each span.

    **Normalized dict schema** (what ``to_sentinel_event`` accepts):

    All dicts must have a ``"type"`` key matching one of the supported event
    type strings. Additional keys are type-specific (see table below).

    +-----------------------------+-------------------------------+-----------+
    | type                        | event_type (SentinelEvent)    | severity  |
    +=============================+===============================+===========+
    | pipeline_started            | pipeline_started              | INFO      |
    | pipeline_finished           | pipeline_finished             | INFO      |
    | pipeline_error              | pipeline_error                | ERROR     |
    | component_started           | component_started             | INFO      |
    | component_finished          | component_finished            | INFO      |
    | component_error             | component_error               | ERROR     |
    | llm_run_started             | llm_run_started               | INFO      |
    | llm_run_finished            | llm_run_finished              | INFO      |
    | llm_run_error               | llm_run_error                 | ERROR     |
    | retriever_run_started       | retriever_run_started         | INFO      |
    | retriever_run_finished      | retriever_run_finished        | INFO      |
    | embedder_run_started        | embedder_run_started          | INFO      |
    | embedder_run_finished       | embedder_run_finished         | INFO      |
    | (anything else)             | unknown_haystack_event        | INFO      |
    +-----------------------------+-------------------------------+-----------+

    Parameters
    ----------
    run_id:
        Optional run / session identifier used as ``trace_id`` on every
        translated event. Correlates all events from one pipeline execution.
    """

    source_system = "haystack"

    def __init__(
        self,
        run_id: str | None = None,
        *,
        gate=None,
        permissions=None,
        sandbox=None,
        approvals=None,
        identity=None,
        trust=None,
        attestor=None,
        hierarchy=None,
        trust_interop=None,
    ) -> None:
        _require_haystack()
        self._run_id = run_id
        self._gate = gate
        self._permissions = permissions
        self._sandbox = sandbox
        self._approvals = approvals
        self._identity = identity
        self._trust = trust
        self._attestor = attestor
        self._hierarchy = hierarchy
        self._trust_interop = trust_interop
        self._buffer: list[SentinelEvent] = []
        self._lock = threading.Lock()

    def setup(self, proxy_tracer=None) -> None:
        """
        Install a wrapping tracer into Haystack's ``ProxyTracer``.

        Captures the current ``provided_tracer`` (if any), then replaces it
        with a thin wrapper that emits SentinelEvents for every pipeline and
        component span while forwarding all calls to the original tracer.

        Call this once before running any pipelines. If ``proxy_tracer`` is
        None, ``haystack.tracing.tracer`` (the global singleton) is used.

        Parameters
        ----------
        proxy_tracer:
            Optional ``ProxyTracer`` override (useful for testing with a mock).
        """
        from haystack import tracing as _ht  # type: ignore[import]

        adapter_self = self
        target = proxy_tracer if proxy_tracer is not None else _ht.tracer

        # Preserve whatever inner tracer is currently registered so we can
        # forward span calls to it (e.g., an OTel or Datadog tracer).
        existing_inner = getattr(target, "provided_tracer", None)

        class _WrappingTracer:
            def current_span(self):
                if existing_inner is not None:
                    try:
                        return existing_inner.current_span()
                    except Exception:
                        pass
                return None

            @contextlib.contextmanager
            def trace(self, operation_name, tags=None, parent_span=None, **kwargs):
                init_tags = dict(tags or {})

                # --- start event ---
                raw_start = _op_to_raw(operation_name, init_tags, "start")
                if raw_start is not None:
                    if adapter_self._gate or adapter_self._permissions:
                        comp_name = init_tags.get("haystack.component.name", operation_name)
                        check_tool_call(
                            adapter_self,
                            comp_name,
                            init_tags,
                            context={"operation": operation_name},
                        )
                    adapter_self._buffer_event(adapter_self.to_sentinel_event(raw_start))

                # Delegate to the previously registered tracer (if any).
                if existing_inner is not None:
                    inner_cm = existing_inner.trace(
                        operation_name, tags=tags, parent_span=parent_span
                    )
                else:
                    inner_cm = contextlib.nullcontext(None)

                proxy = _SpanProxy(None)
                try:
                    if adapter_self._sandbox is not None:
                        with adapter_self._sandbox:
                            with inner_cm as real_span:
                                proxy = _SpanProxy(real_span)
                                yield proxy
                    else:
                        with inner_cm as real_span:
                            proxy = _SpanProxy(real_span)
                            yield proxy

                    # --- end event (normal exit) ---
                    raw_end = _op_to_raw(operation_name, init_tags, "end", span_tags=proxy._tags)
                    if raw_end is not None:
                        adapter_self._buffer_event(adapter_self.to_sentinel_event(raw_end))
                    comp_name = init_tags.get("haystack.component.name", operation_name)
                    record_trust_node(adapter_self, agent_id=comp_name, tool_calls=[comp_name])
                except Exception as exc:
                    # --- error event ---
                    raw_err = _op_to_raw(operation_name, init_tags, "error", error=str(exc))
                    if raw_err is not None:
                        adapter_self._buffer_event(adapter_self.to_sentinel_event(raw_err))
                    raise

        target.provided_tracer = _WrappingTracer()

    def to_sentinel_event(self, raw: dict[str, Any]) -> SentinelEvent:
        """
        Translate one Haystack event dict into a SentinelEvent.

        ``raw`` must contain a ``"type"`` key. All other keys are
        type-specific. Unknown types are translated to
        ``unknown_haystack_event`` with severity INFO.
        """
        dispatch = {
            "pipeline_started": self._from_pipeline_started,
            "pipeline_finished": self._from_pipeline_finished,
            "pipeline_error": self._from_pipeline_error,
            "component_started": self._from_component_started,
            "component_finished": self._from_component_finished,
            "component_error": self._from_component_error,
            "llm_run_started": self._from_llm_run_started,
            "llm_run_finished": self._from_llm_run_finished,
            "llm_run_error": self._from_llm_run_error,
            "retriever_run_started": self._from_retriever_run_started,
            "retriever_run_finished": self._from_retriever_run_finished,
            "embedder_run_started": self._from_embedder_run_started,
            "embedder_run_finished": self._from_embedder_run_finished,
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
    # Private translators — pipeline
    # ------------------------------------------------------------------

    def _from_pipeline_started(self, raw: dict[str, Any]) -> SentinelEvent:
        pipeline_name = raw.get("pipeline_name", "unknown")
        return SentinelEvent(
            event_id=f"haystack-pipeline-{uuid.uuid4()}",
            event_type="pipeline_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"pipeline '{pipeline_name}' started",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"pipeline_name": pipeline_name},
        )

    def _from_pipeline_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        pipeline_name = raw.get("pipeline_name", "unknown")
        output_keys = raw.get("output_keys") or []
        return SentinelEvent(
            event_id=f"haystack-pipeline-{uuid.uuid4()}",
            event_type="pipeline_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"pipeline '{pipeline_name}' finished",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"pipeline_name": pipeline_name, "output_keys": list(output_keys)},
        )

    def _from_pipeline_error(self, raw: dict[str, Any]) -> SentinelEvent:
        pipeline_name = raw.get("pipeline_name", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"haystack-pipeline-{uuid.uuid4()}",
            event_type="pipeline_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"pipeline '{pipeline_name}' error: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"pipeline_name": pipeline_name, "error": error},
        )

    # ------------------------------------------------------------------
    # Private translators — generic component
    # ------------------------------------------------------------------

    def _from_component_started(self, raw: dict[str, Any]) -> SentinelEvent:
        component_name = raw.get("component_name", "unknown")
        component_type = raw.get("component_type", "unknown")
        return SentinelEvent(
            event_id=f"haystack-component-{uuid.uuid4()}",
            event_type="component_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"component '{component_name}' ({component_type}) started",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"component_name": component_name, "component_type": component_type},
        )

    def _from_component_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        component_name = raw.get("component_name", "unknown")
        component_type = raw.get("component_type", "unknown")
        output_keys = raw.get("output_keys") or []
        return SentinelEvent(
            event_id=f"haystack-component-{uuid.uuid4()}",
            event_type="component_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"component '{component_name}' ({component_type}) finished",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "component_name": component_name,
                "component_type": component_type,
                "output_keys": list(output_keys),
            },
        )

    def _from_component_error(self, raw: dict[str, Any]) -> SentinelEvent:
        component_name = raw.get("component_name", "unknown")
        component_type = raw.get("component_type", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"haystack-component-{uuid.uuid4()}",
            event_type="component_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"component '{component_name}' ({component_type}) error: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "component_name": component_name,
                "component_type": component_type,
                "error": error,
            },
        )

    # ------------------------------------------------------------------
    # Private translators — LLM
    # ------------------------------------------------------------------

    def _from_llm_run_started(self, raw: dict[str, Any]) -> SentinelEvent:
        component_name = raw.get("component_name", "unknown")
        model = raw.get("model", "unknown")
        return SentinelEvent(
            event_id=f"haystack-llm-{uuid.uuid4()}",
            event_type="llm_run_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"LLM '{component_name}' started (model='{model}')",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"component_name": component_name, "model": model},
        )

    def _from_llm_run_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        component_name = raw.get("component_name", "unknown")
        model = raw.get("model", "unknown")
        reply = raw.get("reply", "")
        return SentinelEvent(
            event_id=f"haystack-llm-{uuid.uuid4()}",
            event_type="llm_run_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"LLM '{component_name}' finished (model='{model}')",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"component_name": component_name, "model": model, "reply": reply},
        )

    def _from_llm_run_error(self, raw: dict[str, Any]) -> SentinelEvent:
        component_name = raw.get("component_name", "unknown")
        model = raw.get("model", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"haystack-llm-{uuid.uuid4()}",
            event_type="llm_run_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"LLM '{component_name}' error (model='{model}'): {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"component_name": component_name, "model": model, "error": error},
        )

    # ------------------------------------------------------------------
    # Private translators — retriever
    # ------------------------------------------------------------------

    def _from_retriever_run_started(self, raw: dict[str, Any]) -> SentinelEvent:
        component_name = raw.get("component_name", "unknown")
        query = raw.get("query", "")
        return SentinelEvent(
            event_id=f"haystack-retriever-{uuid.uuid4()}",
            event_type="retriever_run_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"retriever '{component_name}' started: {query[:120]}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"component_name": component_name, "query": query},
        )

    def _from_retriever_run_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        component_name = raw.get("component_name", "unknown")
        num_documents = raw.get("num_documents", 0)
        return SentinelEvent(
            event_id=f"haystack-retriever-{uuid.uuid4()}",
            event_type="retriever_run_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"retriever '{component_name}' returned {num_documents} document(s)",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"component_name": component_name, "num_documents": num_documents},
        )

    # ------------------------------------------------------------------
    # Private translators — embedder
    # ------------------------------------------------------------------

    def _from_embedder_run_started(self, raw: dict[str, Any]) -> SentinelEvent:
        component_name = raw.get("component_name", "unknown")
        model = raw.get("model", "unknown")
        return SentinelEvent(
            event_id=f"haystack-embedder-{uuid.uuid4()}",
            event_type="embedder_run_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"embedder '{component_name}' started (model='{model}')",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"component_name": component_name, "model": model},
        )

    def _from_embedder_run_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        component_name = raw.get("component_name", "unknown")
        model = raw.get("model", "unknown")
        return SentinelEvent(
            event_id=f"haystack-embedder-{uuid.uuid4()}",
            event_type="embedder_run_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"embedder '{component_name}' finished (model='{model}')",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"component_name": component_name, "model": model},
        )

    # ------------------------------------------------------------------
    # Private translator — unknown
    # ------------------------------------------------------------------

    def _from_unknown(self, raw: dict[str, Any]) -> SentinelEvent:
        original_type = raw.get("type", "unknown")
        return SentinelEvent(
            event_id=f"haystack-unknown-{uuid.uuid4()}",
            event_type="unknown_haystack_event",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"unknown Haystack event type '{original_type}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"original_type": original_type},
        )


# ---------------------------------------------------------------------------
# Module-level helpers used by setup()'s _WrappingTracer
# ---------------------------------------------------------------------------


def _op_to_raw(
    operation_name: str,
    init_tags: dict[str, Any],
    phase: str,
    span_tags: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any] | None:
    """
    Convert a Haystack operation name + span tags into a normalized event dict,
    or ``None`` if the operation should not produce a SentinelEvent.

    Parameters
    ----------
    operation_name:
        The Haystack span operation name (e.g. ``"haystack.pipeline.run"``).
    init_tags:
        Tags passed at ``tracer.trace()`` call time.
    phase:
        ``"start"``, ``"end"``, or ``"error"``.
    span_tags:
        Tags set on the span during execution (via ``span.set_tag()``).
    error:
        Error message, only relevant when ``phase == "error"``.
    """
    span_tags = span_tags or {}
    all_tags = {**init_tags, **span_tags}

    pipeline_name = str(all_tags.get("haystack.pipeline.name", "") or "unknown")
    component_name = str(all_tags.get("haystack.component.name", "") or "unknown")
    component_type_full = str(all_tags.get("haystack.component.type", "") or "")
    component_type_short = component_type_full.split(".")[-1] if component_type_full else "unknown"

    if operation_name == "haystack.pipeline.run":
        if phase == "start":
            return {"type": "pipeline_started", "pipeline_name": pipeline_name}
        if phase == "end":
            output_data = span_tags.get("haystack.pipeline.output_data") or {}
            output_keys = sorted(output_data.keys()) if isinstance(output_data, dict) else []
            return {
                "type": "pipeline_finished",
                "pipeline_name": pipeline_name,
                "output_keys": output_keys,
            }
        if phase == "error":
            return {
                "type": "pipeline_error",
                "pipeline_name": pipeline_name,
                "error": error,
            }

    if operation_name == "haystack.component.run":
        category = _component_category(component_type_full)

        if category == "llm":
            model = _extract_model(all_tags)
            if phase == "start":
                return {
                    "type": "llm_run_started",
                    "component_name": component_name,
                    "model": model,
                }
            if phase == "end":
                replies = span_tags.get("haystack.component.output.replies") or []
                reply = str(replies[0])[:500] if replies else ""
                return {
                    "type": "llm_run_finished",
                    "component_name": component_name,
                    "model": model,
                    "reply": reply,
                }
            if phase == "error":
                return {
                    "type": "llm_run_error",
                    "component_name": component_name,
                    "model": model,
                    "error": error,
                }

        if category == "retriever":
            query = str(span_tags.get("haystack.component.input.query", "") or "")[:500]
            if phase == "start":
                return {
                    "type": "retriever_run_started",
                    "component_name": component_name,
                    "query": query,
                }
            if phase == "end":
                docs = span_tags.get("haystack.component.output.documents") or []
                num_documents = len(docs) if isinstance(docs, (list, tuple)) else 0
                return {
                    "type": "retriever_run_finished",
                    "component_name": component_name,
                    "num_documents": num_documents,
                }

        if category == "embedder":
            model = _extract_model(all_tags)
            if phase == "start":
                return {
                    "type": "embedder_run_started",
                    "component_name": component_name,
                    "model": model,
                }
            if phase == "end":
                return {
                    "type": "embedder_run_finished",
                    "component_name": component_name,
                    "model": model,
                }

        # Generic component (PromptBuilder, Router, etc.)
        if phase == "start":
            return {
                "type": "component_started",
                "component_name": component_name,
                "component_type": component_type_short,
            }
        if phase == "end":
            output_data = span_tags.get("haystack.component.output") or {}
            output_keys = sorted(output_data.keys()) if isinstance(output_data, dict) else []
            return {
                "type": "component_finished",
                "component_name": component_name,
                "component_type": component_type_short,
                "output_keys": output_keys,
            }
        if phase == "error":
            return {
                "type": "component_error",
                "component_name": component_name,
                "component_type": component_type_short,
                "error": error,
            }

    # Unknown operation name — don't emit a SentinelEvent
    return None


def _component_category(component_type: str) -> str:
    """Classify a Haystack component type string into a broad category."""
    if not component_type:
        return "component"
    short = component_type.split(".")[-1] if "." in component_type else component_type
    if "Generator" in short:
        return "llm"
    if "Retriever" in short:
        return "retriever"
    if "Embedder" in short:
        return "embedder"
    return "component"


def _extract_model(tags: dict[str, Any]) -> str:
    """Best-effort model name extraction from Haystack span tags."""
    meta_list = tags.get("haystack.component.output.meta") or []
    if meta_list and isinstance(meta_list, list) and isinstance(meta_list[0], dict):
        name = meta_list[0].get("model") or meta_list[0].get("model_name")
        if name:
            return str(name)
    return str(tags.get("haystack.llm.model_name", "") or tags.get("model", "") or "unknown")
