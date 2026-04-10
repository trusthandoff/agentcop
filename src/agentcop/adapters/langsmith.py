"""
LangSmith adapter for agentcop.

Translates LangSmith run events — chain, LLM, tool, retriever, and embedding
runs — into SentinelEvents for forensic auditing.

LangSmith has no global "on run complete" hook at the SDK level. This adapter
intercepts runs by wrapping ``client.create_run`` and ``client.update_run``
on a ``langsmith.Client`` instance. An in-flight registry correlates each
``create_run`` (start) with its matching ``update_run`` (end) by run ID, then
translates the combined data into a typed end event and buffers it.

Every ``create_run`` call additionally fires a ``run_started`` event, giving
you visibility into runs that are still in-progress at drain time.

Supported event types and their SentinelEvent mapping:

  All run types (on start): run_started
  Chain/Prompt/Parser:      chain_finished / chain_error
  LLM:                      llm_finished / llm_error
  Tool:                     tool_finished / tool_error
  Retriever:                retriever_finished / retriever_error
  Embedding:                embedding_finished / embedding_error

Install the optional dependency to use this adapter:

    pip install agentcop[langsmith]

Quickstart::

    from langsmith import Client, trace
    from agentcop import Sentinel
    from agentcop.adapters.langsmith import LangSmithSentinelAdapter

    client = Client()                        # reads LANGCHAIN_API_KEY env var
    adapter = LangSmithSentinelAdapter(run_id="run-001")
    adapter.setup(client)                    # wraps create_run / update_run

    with trace("my-chain", client=client, inputs={"question": "What is RAG?"}) as run:
        with trace("llm-call", run_type="llm", client=client,
                   inputs={"prompt": "..."}) as llm_run:
            llm_run.add_metadata({"ls_model_name": "gpt-4o-mini", "ls_provider": "openai"})
        # end events fire automatically on context-manager exit

    sentinel = Sentinel()
    adapter.flush_into(sentinel)
    violations = sentinel.detect_violations()
    sentinel.report()

``to_sentinel_event(raw)`` also accepts plain dicts for manual translation
or testing without a live client::

    event = adapter.to_sentinel_event({
        "type": "llm_error",
        "run_name": "gpt-call",
        "model": "gpt-4o-mini",
        "error": "rate limit exceeded",
    })
"""

from __future__ import annotations

import contextlib
import json
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from agentcop.adapters._runtime import check_tool_call
from agentcop.event import SentinelEvent


def _require_langsmith() -> None:
    try:
        import langsmith  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "LangSmith adapter requires 'langsmith'. "
            "Install it with: pip install agentcop[langsmith]"
        ) from exc


# run_type → event category
_CHAIN_TYPES = frozenset({"chain", "prompt", "parser"})
_LLM_TYPES = frozenset({"llm"})
_TOOL_TYPES = frozenset({"tool"})
_RETRIEVER_TYPES = frozenset({"retriever"})
_EMBEDDING_TYPES = frozenset({"embedding"})


class LangSmithSentinelAdapter:
    """
    Adapter that translates LangSmith run events into SentinelEvents.

    LangSmith traces every ``@traceable``-decorated function and ``trace()``
    context manager by calling ``client.create_run()`` (start) and
    ``client.update_run()`` (end) on the provided ``Client`` instance. This
    adapter wraps those two methods to intercept all run traffic, correlates
    start and end by run ID, and buffers translated ``SentinelEvent`` objects.

    **Normalized dict schema** (what ``to_sentinel_event`` accepts):

    All dicts must have a ``"type"`` key. Additional keys are type-specific.

    +---------------------+-------------------------------+-----------+
    | type                | event_type (SentinelEvent)    | severity  |
    +=====================+===============================+===========+
    | run_started         | run_started                   | INFO      |
    | chain_finished      | chain_finished                | INFO      |
    | chain_error         | chain_error                   | ERROR     |
    | llm_finished        | llm_finished                  | INFO      |
    | llm_error           | llm_error                     | ERROR     |
    | tool_finished       | tool_finished                 | INFO      |
    | tool_error          | tool_error                    | ERROR     |
    | retriever_finished  | retriever_finished            | INFO      |
    | retriever_error     | retriever_error               | ERROR     |
    | embedding_finished  | embedding_finished            | INFO      |
    | embedding_error     | embedding_error               | ERROR     |
    | (anything else)     | unknown_langsmith_event       | INFO      |
    +---------------------+-------------------------------+-----------+

    Parameters
    ----------
    run_id:
        Optional session identifier used as ``trace_id`` on every translated
        event. When ``None``, the LangSmith trace ID (root run UUID) from
        the run is used instead.
    """

    source_system = "langsmith"

    def __init__(
        self,
        run_id: str | None = None,
        *,
        gate=None,
        permissions=None,
        sandbox=None,
        approvals=None,
        identity=None,
        trust_observer=None,
        hierarchy=None,
        trust_interop=None,
    ) -> None:
        _require_langsmith()
        self._run_id = run_id
        self._gate = gate
        self._permissions = permissions
        self._sandbox = sandbox
        self._approvals = approvals
        self._identity = identity
        self._trust_observer = trust_observer
        self._hierarchy = hierarchy
        self._trust_interop = trust_interop
        self._buffer: list[SentinelEvent] = []
        self._lock = threading.Lock()
        # Maps run_id str → start-time snapshot for correlation with update_run
        self._inflight: dict[str, dict[str, Any]] = {}

    def setup(self, client=None) -> None:
        """
        Wrap ``create_run`` and ``update_run`` on a LangSmith ``Client``.

        All runs traced through this client will be intercepted:

        - ``create_run`` fires a ``run_started`` event and records the run
          in an in-flight registry.
        - ``update_run`` looks up the in-flight entry, combines start- and
          end-time data, emits the appropriate ``*_finished`` or ``*_error``
          event, and removes the entry from the registry.

        The original ``create_run`` / ``update_run`` methods are still called
        so LangSmith export is unaffected.

        Parameters
        ----------
        client:
            A ``langsmith.Client`` instance. When ``None``, a default client
            is constructed (reads ``LANGCHAIN_API_KEY`` / ``LANGCHAIN_ENDPOINT``
            from the environment).
        """
        if client is None:
            from langsmith import Client as _Client  # type: ignore[import]

            client = _Client()

        adapter_self = self
        original_create = client.create_run
        original_update = client.update_run

        def _intercepted_create(*args, **kwargs):
            # Extract core fields — RunTree.post() passes everything as kwargs
            name = (args[0] if len(args) > 0 else None) or kwargs.get("name", "unknown")
            inputs = (args[1] if len(args) > 1 else None) or kwargs.get("inputs") or {}
            run_type = (args[2] if len(args) > 2 else None) or kwargs.get("run_type", "chain")
            # Log gate decision for tool runs.
            if run_type in _TOOL_TYPES and (adapter_self._gate or adapter_self._permissions):
                with contextlib.suppress(PermissionError):
                    check_tool_call(
                        adapter_self,
                        str(name),
                        dict(inputs) if isinstance(inputs, dict) else {},
                        context={"run_type": run_type},
                    )  # already buffered as gate_denied / permission_violation on suppress

            run_id = str(kwargs.get("id") or "")
            trace_id = str(kwargs.get("trace_id") or run_id)
            parent_run_id = str(kwargs.get("parent_run_id") or "")
            tags = list(kwargs.get("tags") or [])
            extra = kwargs.get("extra") or {}
            metadata = extra.get("metadata", {}) if isinstance(extra, dict) else {}

            inputs_str = _safe_json(inputs)

            # Record in-flight for correlation with update_run
            if run_id:
                with adapter_self._lock:
                    adapter_self._inflight[run_id] = {
                        "name": name,
                        "run_type": run_type,
                        "ls_trace_id": trace_id,
                        "parent_run_id": parent_run_id,
                        "tags": tags,
                        "metadata": metadata,
                        "inputs": inputs_str,
                        "extra": extra,
                    }

            raw = {
                "type": "run_started",
                "run_id": run_id,
                "run_name": name,
                "run_type": run_type,
                "ls_trace_id": trace_id,
                "parent_run_id": parent_run_id,
                "tags": tags,
                "metadata": metadata,
                "inputs": inputs_str,
            }
            adapter_self._buffer_event(adapter_self.to_sentinel_event(raw))
            return original_create(*args, **kwargs)

        def _intercepted_update(*args, **kwargs):
            run_id = str(args[0] if len(args) > 0 else kwargs.get("run_id", ""))

            with adapter_self._lock:
                inflight = adapter_self._inflight.pop(run_id, None)

            if inflight is not None:
                outputs = kwargs.get("outputs") or {}
                error = kwargs.get("error")
                # Merge any extra metadata sent with the update
                update_extra = kwargs.get("extra") or {}
                update_meta = (
                    update_extra.get("metadata", {}) if isinstance(update_extra, dict) else {}
                )
                metadata = {**inflight.get("metadata", {}), **update_meta}

                run_type = inflight["run_type"]
                is_err = bool(error)
                model = metadata.get("ls_model_name", "unknown") or "unknown"
                provider = metadata.get("ls_provider", "unknown") or "unknown"
                usage = metadata.get("usage_metadata") or {}

                raw: dict[str, Any] = {
                    "run_id": run_id,
                    "run_name": inflight["name"],
                    "run_type": run_type,
                    "ls_trace_id": inflight["ls_trace_id"],
                    "parent_run_id": inflight["parent_run_id"],
                    "tags": inflight["tags"],
                    "metadata": metadata,
                    "inputs": inflight["inputs"],
                    "outputs": _safe_json(outputs),
                    "error": str(error) if error else "",
                    "model": model,
                    "provider": provider,
                    "usage": usage,
                }

                if run_type in _LLM_TYPES:
                    raw["type"] = "llm_error" if is_err else "llm_finished"
                elif run_type in _TOOL_TYPES:
                    raw["type"] = "tool_error" if is_err else "tool_finished"
                elif run_type in _RETRIEVER_TYPES:
                    raw["type"] = "retriever_error" if is_err else "retriever_finished"
                elif run_type in _EMBEDDING_TYPES:
                    raw["type"] = "embedding_error" if is_err else "embedding_finished"
                else:  # chain, prompt, parser, or unknown
                    raw["type"] = "chain_error" if is_err else "chain_finished"

                adapter_self._buffer_event(adapter_self.to_sentinel_event(raw))
                if not is_err and adapter_self._trust_observer is not None:
                    with contextlib.suppress(Exception):
                        adapter_self._trust_observer.record_verified_chain()

            return original_update(*args, **kwargs)

        client.create_run = _intercepted_create
        client.update_run = _intercepted_update

    def to_sentinel_event(self, raw: dict[str, Any]) -> SentinelEvent:
        """
        Translate one LangSmith event dict into a SentinelEvent.

        ``raw`` must contain a ``"type"`` key. All other keys are
        type-specific. Unknown types map to ``unknown_langsmith_event``
        with severity INFO.
        """
        dispatch = {
            "run_started": self._from_run_started,
            "chain_finished": self._from_chain_finished,
            "chain_error": self._from_chain_error,
            "llm_finished": self._from_llm_finished,
            "llm_error": self._from_llm_error,
            "tool_finished": self._from_tool_finished,
            "tool_error": self._from_tool_error,
            "retriever_finished": self._from_retriever_finished,
            "retriever_error": self._from_retriever_error,
            "embedding_finished": self._from_embedding_finished,
            "embedding_error": self._from_embedding_error,
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
    # Trace-ID: prefer run_id; fall back to LangSmith trace/run ID
    # ------------------------------------------------------------------

    def _trace_id(self, raw: dict[str, Any]) -> str | None:
        if self._run_id:
            return self._run_id
        ls = raw.get("ls_trace_id", "")
        return ls or None

    # ------------------------------------------------------------------
    # Private translator — run_started
    # ------------------------------------------------------------------

    def _from_run_started(self, raw: dict[str, Any]) -> SentinelEvent:
        run_name = raw.get("run_name", "unknown")
        run_type = raw.get("run_type", "chain")
        return SentinelEvent(
            event_id=f"ls-run-{uuid.uuid4()}",
            event_type="run_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"{run_type} run '{run_name}' started",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._start_attrs(raw),
        )

    # ------------------------------------------------------------------
    # Private translators — chain (chain / prompt / parser)
    # ------------------------------------------------------------------

    def _from_chain_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        run_name = raw.get("run_name", "unknown")
        run_type = raw.get("run_type", "chain")
        return SentinelEvent(
            event_id=f"ls-chain-{uuid.uuid4()}",
            event_type="chain_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"{run_type} run '{run_name}' finished",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._end_attrs(raw),
        )

    def _from_chain_error(self, raw: dict[str, Any]) -> SentinelEvent:
        run_name = raw.get("run_name", "unknown")
        run_type = raw.get("run_type", "chain")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"ls-chain-{uuid.uuid4()}",
            event_type="chain_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"{run_type} run '{run_name}' error: {error}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._end_attrs(raw),
        )

    # ------------------------------------------------------------------
    # Private translators — LLM
    # ------------------------------------------------------------------

    def _from_llm_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        run_name = raw.get("run_name", "unknown")
        model = raw.get("model", "unknown")
        return SentinelEvent(
            event_id=f"ls-llm-{uuid.uuid4()}",
            event_type="llm_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"llm run '{run_name}' finished (model='{model}')",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes={**self._end_attrs(raw), **self._llm_attrs(raw)},
        )

    def _from_llm_error(self, raw: dict[str, Any]) -> SentinelEvent:
        run_name = raw.get("run_name", "unknown")
        model = raw.get("model", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"ls-llm-{uuid.uuid4()}",
            event_type="llm_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"llm run '{run_name}' error (model='{model}'): {error}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes={**self._end_attrs(raw), **self._llm_attrs(raw)},
        )

    # ------------------------------------------------------------------
    # Private translators — tool
    # ------------------------------------------------------------------

    def _from_tool_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        run_name = raw.get("run_name", "unknown")
        return SentinelEvent(
            event_id=f"ls-tool-{uuid.uuid4()}",
            event_type="tool_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"tool run '{run_name}' finished",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._end_attrs(raw),
        )

    def _from_tool_error(self, raw: dict[str, Any]) -> SentinelEvent:
        run_name = raw.get("run_name", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"ls-tool-{uuid.uuid4()}",
            event_type="tool_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"tool run '{run_name}' error: {error}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._end_attrs(raw),
        )

    # ------------------------------------------------------------------
    # Private translators — retriever
    # ------------------------------------------------------------------

    def _from_retriever_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        run_name = raw.get("run_name", "unknown")
        return SentinelEvent(
            event_id=f"ls-retriever-{uuid.uuid4()}",
            event_type="retriever_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"retriever run '{run_name}' finished",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._end_attrs(raw),
        )

    def _from_retriever_error(self, raw: dict[str, Any]) -> SentinelEvent:
        run_name = raw.get("run_name", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"ls-retriever-{uuid.uuid4()}",
            event_type="retriever_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"retriever run '{run_name}' error: {error}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._end_attrs(raw),
        )

    # ------------------------------------------------------------------
    # Private translators — embedding
    # ------------------------------------------------------------------

    def _from_embedding_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        run_name = raw.get("run_name", "unknown")
        return SentinelEvent(
            event_id=f"ls-embedding-{uuid.uuid4()}",
            event_type="embedding_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"embedding run '{run_name}' finished",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._end_attrs(raw),
        )

    def _from_embedding_error(self, raw: dict[str, Any]) -> SentinelEvent:
        run_name = raw.get("run_name", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"ls-embedding-{uuid.uuid4()}",
            event_type="embedding_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"embedding run '{run_name}' error: {error}",
            source_system=self.source_system,
            trace_id=self._trace_id(raw),
            attributes=self._end_attrs(raw),
        )

    # ------------------------------------------------------------------
    # Private translator — unknown
    # ------------------------------------------------------------------

    def _from_unknown(self, raw: dict[str, Any]) -> SentinelEvent:
        original_type = raw.get("type", "unknown")
        return SentinelEvent(
            event_id=f"ls-unknown-{uuid.uuid4()}",
            event_type="unknown_langsmith_event",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"unknown LangSmith event type '{original_type}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"original_type": original_type},
        )

    # ------------------------------------------------------------------
    # Attribute helpers
    # ------------------------------------------------------------------

    def _start_attrs(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": raw.get("run_id", ""),
            "run_name": raw.get("run_name", "unknown"),
            "run_type": raw.get("run_type", "chain"),
            "ls_trace_id": raw.get("ls_trace_id", ""),
            "parent_run_id": raw.get("parent_run_id", ""),
            "tags": list(raw.get("tags") or []),
            "metadata": dict(raw.get("metadata") or {}),
            "inputs": raw.get("inputs", ""),
        }

    def _end_attrs(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": raw.get("run_id", ""),
            "run_name": raw.get("run_name", "unknown"),
            "run_type": raw.get("run_type", "chain"),
            "ls_trace_id": raw.get("ls_trace_id", ""),
            "parent_run_id": raw.get("parent_run_id", ""),
            "tags": list(raw.get("tags") or []),
            "metadata": dict(raw.get("metadata") or {}),
            "inputs": raw.get("inputs", ""),
            "outputs": raw.get("outputs", ""),
            "error": raw.get("error", ""),
        }

    def _llm_attrs(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": raw.get("model", "unknown"),
            "provider": raw.get("provider", "unknown"),
            "usage": dict(raw.get("usage") or {}),
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _safe_json(obj: Any) -> str:
    """Serialize ``obj`` to a JSON string truncated to 500 chars."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj[:500]
    try:
        return json.dumps(obj, default=str)[:500]
    except (TypeError, ValueError):
        return str(obj)[:500]
