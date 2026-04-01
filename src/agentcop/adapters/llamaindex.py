"""
LlamaIndex adapter for agentcop.

Translates LlamaIndex instrumentation events into SentinelEvents for forensic
auditing. Hooks into LlamaIndex's dispatcher-based event system, buffers
translated events during execution, and lets you drain them into a ``Sentinel``
instance for violation detection.

Supported event categories and their SentinelEvent mapping:

  Query:     query_started / query_finished / query_error
  Retrieval: retrieval_started / retrieval_finished / retrieval_error
  LLM:       llm_predict_started / llm_predict_finished / llm_predict_error
  Agent:     agent_step_started / agent_step_finished / agent_tool_call
  Embedding: embedding_started / embedding_finished

Install the optional dependency to use this adapter:

    pip install agentcop[llamaindex]

Quickstart::

    from llama_index.core import VectorStoreIndex, SimpleDirectoryReader
    from agentcop import Sentinel
    from agentcop.adapters.llamaindex import LlamaIndexSentinelAdapter

    adapter = LlamaIndexSentinelAdapter(run_id="run-001")
    adapter.setup()          # register with the LlamaIndex dispatcher

    documents = SimpleDirectoryReader("data").load_data()
    index = VectorStoreIndex.from_documents(documents)
    query_engine = index.as_query_engine()
    response = query_engine.query("What is RAG?")

    sentinel = Sentinel()
    adapter.flush_into(sentinel)
    violations = sentinel.detect_violations()
    sentinel.report()

``to_sentinel_event(raw)`` also accepts plain dicts for manual translation
or testing without a live index::

    event = adapter.to_sentinel_event({
        "type": "llm_predict_error",
        "model_name": "gpt-4o",
        "error": "rate limit exceeded",
    })
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agentcop.event import SentinelEvent


def _require_llamaindex() -> None:
    try:
        import llama_index.core  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "LlamaIndex adapter requires 'llama-index-core'. "
            "Install it with: pip install agentcop[llamaindex]"
        ) from exc


class LlamaIndexSentinelAdapter:
    """
    Adapter that translates LlamaIndex instrumentation events into SentinelEvents.

    LlamaIndex is push-based: events fire through a singleton dispatcher during
    query/agent execution. This adapter registers a handler with that dispatcher,
    buffers translated events, and provides ``drain()`` / ``flush_into()`` to
    consume them.

    **Normalized dict schema** (what ``to_sentinel_event`` accepts):

    All dicts must have a ``"type"`` key matching one of the supported event
    type strings. Additional keys are type-specific (see table below).

    +---------------------------+-------------------------------+-----------+
    | type                      | event_type (SentinelEvent)    | severity  |
    +===========================+===============================+===========+
    | query_started             | query_started                 | INFO      |
    | query_finished            | query_finished                | INFO      |
    | query_error               | query_error                   | ERROR     |
    | retrieval_started         | retrieval_started             | INFO      |
    | retrieval_finished        | retrieval_finished            | INFO      |
    | retrieval_error           | retrieval_error               | ERROR     |
    | llm_predict_started       | llm_predict_started           | INFO      |
    | llm_predict_finished      | llm_predict_finished          | INFO      |
    | llm_predict_error         | llm_predict_error             | ERROR     |
    | agent_step_started        | agent_step_started            | INFO      |
    | agent_step_finished       | agent_step_finished           | INFO      |
    | agent_tool_call           | agent_tool_call               | INFO      |
    | embedding_started         | embedding_started             | INFO      |
    | embedding_finished        | embedding_finished            | INFO      |
    | (anything else)           | unknown_llamaindex_event      | INFO      |
    +---------------------------+-------------------------------+-----------+

    Parameters
    ----------
    run_id:
        Optional run / session identifier used as ``trace_id`` on every
        translated event. Correlates all events from one query execution.
    """

    source_system = "llamaindex"

    def __init__(self, run_id: Optional[str] = None) -> None:
        _require_llamaindex()
        self._run_id = run_id
        self._buffer: List[SentinelEvent] = []
        self._lock = threading.Lock()

    def setup(self, dispatcher=None) -> None:
        """
        Register an event handler with the LlamaIndex instrumentation dispatcher.

        Call this once before running queries or agents. If ``dispatcher`` is
        None, the root dispatcher from
        ``llama_index.core.instrumentation.get_dispatcher()`` is used.

        Parameters
        ----------
        dispatcher:
            Optional dispatcher override (useful for testing with a mock).
        """
        from llama_index.core.instrumentation import (  # type: ignore[import]
            get_dispatcher as _get_dispatcher,
        )
        from llama_index.core.instrumentation.event_handlers import (  # type: ignore[import]
            BaseEventHandler as _BaseEventHandler,
        )
        from llama_index.core.instrumentation.events.query import (  # type: ignore[import]
            QueryEndEvent,
            QueryStartEvent,
        )
        from llama_index.core.instrumentation.events.retrieval import (  # type: ignore[import]
            RetrievalEndEvent,
            RetrievalStartEvent,
        )
        from llama_index.core.instrumentation.events.llm import (  # type: ignore[import]
            LLMChatEndEvent,
            LLMChatStartEvent,
            LLMPredictEndEvent,
            LLMPredictStartEvent,
        )
        from llama_index.core.instrumentation.events.agent import (  # type: ignore[import]
            AgentRunStepEndEvent,
            AgentRunStepStartEvent,
            AgentToolCallEvent,
        )
        from llama_index.core.instrumentation.events.embedding import (  # type: ignore[import]
            EmbeddingEndEvent,
            EmbeddingStartEvent,
        )

        adapter_self = self

        class _Handler(_BaseEventHandler):
            @classmethod
            def class_name(cls) -> str:
                return "AgentcopSentinelHandler"

            def handle(self, event, **kwargs) -> None:
                ts = str(getattr(event, "timestamp", None) or "")
                raw: Optional[Dict[str, Any]] = None

                if isinstance(event, QueryStartEvent):
                    raw = {
                        "type": "query_started",
                        "query_str": _extract_query_str(event),
                        "timestamp": ts,
                    }
                elif isinstance(event, QueryEndEvent):
                    raw = {
                        "type": "query_finished",
                        "query_str": _extract_query_str(event),
                        "response": _extract_response(event),
                        "timestamp": ts,
                    }
                elif isinstance(event, (RetrievalStartEvent,)):
                    raw = {
                        "type": "retrieval_started",
                        "query_str": _extract_query_str(event),
                        "timestamp": ts,
                    }
                elif isinstance(event, RetrievalEndEvent):
                    nodes = getattr(event, "nodes", None) or []
                    raw = {
                        "type": "retrieval_finished",
                        "query_str": _extract_query_str(event),
                        "num_nodes": len(nodes),
                        "timestamp": ts,
                    }
                elif isinstance(event, (LLMPredictStartEvent, LLMChatStartEvent)):
                    raw = {
                        "type": "llm_predict_started",
                        "model_name": _extract_model_name(event),
                        "query_str": _extract_query_str(event),
                        "timestamp": ts,
                    }
                elif isinstance(event, (LLMPredictEndEvent, LLMChatEndEvent)):
                    raw = {
                        "type": "llm_predict_finished",
                        "model_name": _extract_model_name(event),
                        "response": _extract_response(event),
                        "timestamp": ts,
                    }
                elif isinstance(event, AgentRunStepStartEvent):
                    raw = {
                        "type": "agent_step_started",
                        "task_id": str(getattr(event, "task_id", "") or ""),
                        "step_num": _extract_step_num(event),
                        "input": str(getattr(event, "input", "") or "")[:500],
                        "timestamp": ts,
                    }
                elif isinstance(event, AgentRunStepEndEvent):
                    step_output = getattr(event, "step_output", None)
                    raw = {
                        "type": "agent_step_finished",
                        "task_id": str(getattr(event, "task_id", "") or ""),
                        "step_num": _extract_step_num(event),
                        "output": _extract_step_output(step_output),
                        "is_last": bool(getattr(step_output, "is_last", False)),
                        "timestamp": ts,
                    }
                elif isinstance(event, AgentToolCallEvent):
                    tool = getattr(event, "tool", None)
                    raw = {
                        "type": "agent_tool_call",
                        "tool_name": getattr(tool, "name", "unknown") if tool else "unknown",
                        "tool_input": str(getattr(event, "arguments", "") or "")[:500],
                        "timestamp": ts,
                    }
                elif isinstance(event, EmbeddingStartEvent):
                    chunks = getattr(event, "model_dict", None) or {}
                    raw = {
                        "type": "embedding_started",
                        "model_name": _extract_model_name(event),
                        "num_chunks": int(getattr(event, "chunk_count", 0) or 0),
                        "timestamp": ts,
                    }
                elif isinstance(event, EmbeddingEndEvent):
                    raw = {
                        "type": "embedding_finished",
                        "model_name": _extract_model_name(event),
                        "num_chunks": int(getattr(event, "chunk_count", 0) or 0),
                        "timestamp": ts,
                    }

                if raw is not None:
                    adapter_self._buffer_event(adapter_self.to_sentinel_event(raw))

        d = dispatcher if dispatcher is not None else _get_dispatcher()
        d.add_event_handler(_Handler())

    def to_sentinel_event(self, raw: Dict[str, Any]) -> SentinelEvent:
        """
        Translate one LlamaIndex event dict into a SentinelEvent.

        ``raw`` must contain a ``"type"`` key. All other keys are
        type-specific. Unknown types are translated to
        ``unknown_llamaindex_event`` with severity INFO.
        """
        dispatch = {
            "query_started":       self._from_query_started,
            "query_finished":      self._from_query_finished,
            "query_error":         self._from_query_error,
            "retrieval_started":   self._from_retrieval_started,
            "retrieval_finished":  self._from_retrieval_finished,
            "retrieval_error":     self._from_retrieval_error,
            "llm_predict_started": self._from_llm_predict_started,
            "llm_predict_finished": self._from_llm_predict_finished,
            "llm_predict_error":   self._from_llm_predict_error,
            "agent_step_started":  self._from_agent_step_started,
            "agent_step_finished": self._from_agent_step_finished,
            "agent_tool_call":     self._from_agent_tool_call,
            "embedding_started":   self._from_embedding_started,
            "embedding_finished":  self._from_embedding_finished,
        }
        handler = dispatch.get(raw.get("type", ""), self._from_unknown)
        return handler(raw)

    def drain(self) -> List[SentinelEvent]:
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

    def _parse_timestamp(self, raw: Dict[str, Any]) -> datetime:
        ts = raw.get("timestamp")
        if ts:
            try:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Private translators — query
    # ------------------------------------------------------------------

    def _from_query_started(self, raw: Dict[str, Any]) -> SentinelEvent:
        query_str = raw.get("query_str", "")
        return SentinelEvent(
            event_id=f"llamaindex-query-{uuid.uuid4()}",
            event_type="query_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"query started: {query_str[:120]}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"query_str": query_str},
        )

    def _from_query_finished(self, raw: Dict[str, Any]) -> SentinelEvent:
        query_str = raw.get("query_str", "")
        response = raw.get("response", "")
        return SentinelEvent(
            event_id=f"llamaindex-query-{uuid.uuid4()}",
            event_type="query_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"query finished: {query_str[:80]}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"query_str": query_str, "response": response},
        )

    def _from_query_error(self, raw: Dict[str, Any]) -> SentinelEvent:
        query_str = raw.get("query_str", "")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"llamaindex-query-{uuid.uuid4()}",
            event_type="query_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"query error: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"query_str": query_str, "error": error},
        )

    # ------------------------------------------------------------------
    # Private translators — retrieval
    # ------------------------------------------------------------------

    def _from_retrieval_started(self, raw: Dict[str, Any]) -> SentinelEvent:
        query_str = raw.get("query_str", "")
        return SentinelEvent(
            event_id=f"llamaindex-retrieval-{uuid.uuid4()}",
            event_type="retrieval_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"retrieval started: {query_str[:120]}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"query_str": query_str},
        )

    def _from_retrieval_finished(self, raw: Dict[str, Any]) -> SentinelEvent:
        query_str = raw.get("query_str", "")
        num_nodes = raw.get("num_nodes", 0)
        return SentinelEvent(
            event_id=f"llamaindex-retrieval-{uuid.uuid4()}",
            event_type="retrieval_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"retrieval finished: {num_nodes} node(s) for '{query_str[:80]}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"query_str": query_str, "num_nodes": num_nodes},
        )

    def _from_retrieval_error(self, raw: Dict[str, Any]) -> SentinelEvent:
        query_str = raw.get("query_str", "")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"llamaindex-retrieval-{uuid.uuid4()}",
            event_type="retrieval_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"retrieval error: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"query_str": query_str, "error": error},
        )

    # ------------------------------------------------------------------
    # Private translators — LLM
    # ------------------------------------------------------------------

    def _from_llm_predict_started(self, raw: Dict[str, Any]) -> SentinelEvent:
        model_name = raw.get("model_name", "unknown")
        query_str = raw.get("query_str", "")
        attrs: Dict[str, Any] = {"model_name": model_name}
        if query_str:
            attrs["query_str"] = query_str
        return SentinelEvent(
            event_id=f"llamaindex-llm-{uuid.uuid4()}",
            event_type="llm_predict_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"LLM predict started (model='{model_name}')",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes=attrs,
        )

    def _from_llm_predict_finished(self, raw: Dict[str, Any]) -> SentinelEvent:
        model_name = raw.get("model_name", "unknown")
        response = raw.get("response", "")
        return SentinelEvent(
            event_id=f"llamaindex-llm-{uuid.uuid4()}",
            event_type="llm_predict_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"LLM predict finished (model='{model_name}')",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"model_name": model_name, "response": response},
        )

    def _from_llm_predict_error(self, raw: Dict[str, Any]) -> SentinelEvent:
        model_name = raw.get("model_name", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"llamaindex-llm-{uuid.uuid4()}",
            event_type="llm_predict_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"LLM predict error (model='{model_name}'): {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"model_name": model_name, "error": error},
        )

    # ------------------------------------------------------------------
    # Private translators — agent
    # ------------------------------------------------------------------

    def _from_agent_step_started(self, raw: Dict[str, Any]) -> SentinelEvent:
        task_id = raw.get("task_id", "")
        step_num = raw.get("step_num", 0)
        input_str = raw.get("input", "")
        return SentinelEvent(
            event_id=f"llamaindex-agent-{uuid.uuid4()}",
            event_type="agent_step_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"agent step {step_num} started (task={task_id})",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"task_id": task_id, "step_num": step_num, "input": input_str},
        )

    def _from_agent_step_finished(self, raw: Dict[str, Any]) -> SentinelEvent:
        task_id = raw.get("task_id", "")
        step_num = raw.get("step_num", 0)
        output = raw.get("output", "")
        is_last = raw.get("is_last", False)
        return SentinelEvent(
            event_id=f"llamaindex-agent-{uuid.uuid4()}",
            event_type="agent_step_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"agent step {step_num} finished (task={task_id}, is_last={is_last})",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "task_id": task_id,
                "step_num": step_num,
                "output": output,
                "is_last": is_last,
            },
        )

    def _from_agent_tool_call(self, raw: Dict[str, Any]) -> SentinelEvent:
        tool_name = raw.get("tool_name", "unknown")
        tool_input = raw.get("tool_input", "")
        return SentinelEvent(
            event_id=f"llamaindex-agent-{uuid.uuid4()}",
            event_type="agent_tool_call",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"agent called tool '{tool_name}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"tool_name": tool_name, "tool_input": tool_input},
        )

    # ------------------------------------------------------------------
    # Private translators — embedding
    # ------------------------------------------------------------------

    def _from_embedding_started(self, raw: Dict[str, Any]) -> SentinelEvent:
        model_name = raw.get("model_name", "unknown")
        num_chunks = raw.get("num_chunks", 0)
        return SentinelEvent(
            event_id=f"llamaindex-embed-{uuid.uuid4()}",
            event_type="embedding_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"embedding started (model='{model_name}', chunks={num_chunks})",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"model_name": model_name, "num_chunks": num_chunks},
        )

    def _from_embedding_finished(self, raw: Dict[str, Any]) -> SentinelEvent:
        model_name = raw.get("model_name", "unknown")
        num_chunks = raw.get("num_chunks", 0)
        return SentinelEvent(
            event_id=f"llamaindex-embed-{uuid.uuid4()}",
            event_type="embedding_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"embedding finished (model='{model_name}', chunks={num_chunks})",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"model_name": model_name, "num_chunks": num_chunks},
        )

    # ------------------------------------------------------------------
    # Private translator — unknown
    # ------------------------------------------------------------------

    def _from_unknown(self, raw: Dict[str, Any]) -> SentinelEvent:
        original_type = raw.get("type", "unknown")
        return SentinelEvent(
            event_id=f"llamaindex-unknown-{uuid.uuid4()}",
            event_type="unknown_llamaindex_event",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"unknown LlamaIndex event type '{original_type}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"original_type": original_type},
        )


# ---------------------------------------------------------------------------
# Module-level helpers used by setup() handler
# ---------------------------------------------------------------------------

def _extract_query_str(event) -> str:
    """Best-effort query string extraction from LlamaIndex event objects."""
    # QueryStartEvent / QueryEndEvent use .query (QueryBundle)
    q = getattr(event, "query", None)
    if q is not None:
        return str(getattr(q, "query_str", q))[:500]
    # RetrievalStartEvent / RetrievalEndEvent use .str_or_query_bundle
    sq = getattr(event, "str_or_query_bundle", None)
    if sq is not None:
        if hasattr(sq, "query_str"):
            return str(sq.query_str)[:500]
        return str(sq)[:500]
    return ""


def _extract_response(event) -> str:
    """Best-effort response string extraction."""
    r = getattr(event, "response", None)
    if r is None:
        return ""
    # LLMChatEndEvent: response is a ChatResponse with .message.content
    content = getattr(r, "message", None)
    if content is not None:
        return str(getattr(content, "content", content))[:500]
    # QueryEndEvent: response may be a Response with .response attribute
    inner = getattr(r, "response", None)
    if inner is not None:
        return str(inner)[:500]
    return str(r)[:500]


def _extract_model_name(event) -> str:
    """Best-effort model name extraction."""
    md = getattr(event, "model_dict", None) or {}
    name = md.get("model") or md.get("model_name") or md.get("model_type")
    if name:
        return str(name)
    # LLMPredictStartEvent / LLMPredictEndEvent don't always carry model_dict
    return "unknown"


def _extract_step_num(event) -> int:
    """Extract step number from an AgentRunStep* event."""
    step = getattr(event, "step", None)
    if step is not None:
        return int(getattr(step, "step_id", 0) or 0)
    return 0


def _extract_step_output(step_output) -> str:
    """Extract output string from a TaskStepOutput."""
    if step_output is None:
        return ""
    output = getattr(step_output, "output", None)
    if output is not None:
        resp = getattr(output, "response", None)
        if resp is not None:
            return str(resp)[:500]
        return str(output)[:500]
    return ""
