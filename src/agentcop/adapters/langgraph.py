"""
LangGraph adapter for agentcop.

Translates LangGraph debug stream events into SentinelEvents for forensic
auditing. Consumes the output of ``graph.stream(..., stream_mode="debug")``,
which emits three structured event types per graph step:

- ``task``        — a node is scheduled to execute
- ``task_result`` — a node finished (or raised an error)
- ``checkpoint``  — state was checkpointed after a step

Install the optional dependency to use this adapter:

    pip install agentcop[langgraph]

Example::

    from agentcop import Sentinel
    from agentcop.adapters.langgraph import LangGraphSentinelAdapter

    adapter = LangGraphSentinelAdapter(thread_id="run-abc")
    sentinel = Sentinel()

    for event in graph.stream(input, config, stream_mode="debug"):
        sentinel.ingest([adapter.to_sentinel_event(event)])

    violations = sentinel.detect_violations()
    sentinel.report()

Or use ``iter_events`` for a cleaner one-liner::

    sentinel.ingest(adapter.iter_events(
        graph.stream(input, config, stream_mode="debug")
    ))
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, Optional

from agentcop.event import SentinelEvent


def _require_langgraph() -> None:
    try:
        import langgraph  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "LangGraph adapter requires 'langgraph'. "
            "Install it with: pip install agentcop[langgraph]"
        ) from exc


class LangGraphSentinelAdapter:
    """
    Adapter that translates LangGraph debug stream events into SentinelEvents.

    Raw event schema (from ``stream_mode="debug"``)::

        {
            "type":      "task" | "task_result" | "checkpoint",
            "timestamp": "2024-01-01T00:00:00+00:00",   # ISO-8601
            "step":      int,                             # execution step
            "payload":   dict,                            # type-specific data
        }

    SentinelEvent mapping:

    +---------------+-------------------------+-----------+
    | raw type      | event_type              | severity  |
    +===============+=========================+===========+
    | task          | node_start              | INFO      |
    | task_result   | node_end                | INFO      |
    | task_result   | node_error (error set)  | ERROR     |
    | checkpoint    | checkpoint_saved        | INFO      |
    | (other)       | unknown_langgraph_event | INFO      |
    +---------------+-------------------------+-----------+

    Parameters
    ----------
    thread_id:
        Default thread / run ID used as ``trace_id`` when the raw event
        does not carry one. Pass the LangGraph ``configurable.thread_id``
        for the current run to correlate all events from one execution.
    """

    source_system = "langgraph"

    def __init__(self, thread_id: Optional[str] = None) -> None:
        _require_langgraph()
        self._thread_id = thread_id

    def to_sentinel_event(self, raw: Dict[str, Any]) -> SentinelEvent:
        """Translate one LangGraph debug stream event dict into a SentinelEvent."""
        event_type = raw.get("type", "")
        if event_type == "task":
            return self._from_task(raw)
        if event_type == "task_result":
            return self._from_task_result(raw)
        if event_type == "checkpoint":
            return self._from_checkpoint(raw)
        return self._from_unknown(raw)

    def iter_events(
        self, stream: Iterable[Dict[str, Any]]
    ) -> Iterator[SentinelEvent]:
        """Yield a SentinelEvent for every event in a LangGraph debug stream."""
        for raw in stream:
            yield self.to_sentinel_event(raw)

    # ------------------------------------------------------------------
    # Private translators
    # ------------------------------------------------------------------

    def _parse_timestamp(self, raw: Dict[str, Any]) -> datetime:
        ts = raw.get("timestamp")
        if ts:
            try:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    def _resolve_thread_id(self, *candidates: Optional[str]) -> Optional[str]:
        """Return the first non-empty candidate, falling back to the default."""
        for c in candidates:
            if c:
                return c
        return self._thread_id

    def _from_task(self, raw: Dict[str, Any]) -> SentinelEvent:
        payload = raw.get("payload") or {}
        step = raw.get("step", 0)
        node_name = payload.get("name", "unknown")
        task_id = payload.get("id") or str(uuid.uuid4())
        triggers = payload.get("triggers") or []
        metadata = payload.get("metadata") or {}

        return SentinelEvent(
            event_id=f"lg-task-{task_id}",
            event_type="node_start",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"node '{node_name}' started (step {step})",
            source_system=self.source_system,
            trace_id=self._resolve_thread_id(metadata.get("thread_id")),
            attributes={
                "node": node_name,
                "task_id": task_id,
                "step": step,
                "triggers": triggers,
            },
        )

    def _from_task_result(self, raw: Dict[str, Any]) -> SentinelEvent:
        payload = raw.get("payload") or {}
        step = raw.get("step", 0)
        node_name = payload.get("name", "unknown")
        task_id = payload.get("id") or str(uuid.uuid4())
        error = payload.get("error")
        interrupts = payload.get("interrupts") or []
        metadata = payload.get("metadata") or {}

        if error:
            sentinel_event_type = "node_error"
            severity = "ERROR"
            body = f"node '{node_name}' errored (step {step}): {error}"
        else:
            sentinel_event_type = "node_end"
            severity = "INFO"
            body = f"node '{node_name}' finished (step {step})"

        attrs: Dict[str, Any] = {
            "node": node_name,
            "task_id": task_id,
            "step": step,
        }
        if error:
            attrs["error"] = error
        if interrupts:
            attrs["interrupts"] = interrupts

        return SentinelEvent(
            event_id=f"lg-result-{task_id}",
            event_type=sentinel_event_type,
            timestamp=self._parse_timestamp(raw),
            severity=severity,
            body=body,
            source_system=self.source_system,
            trace_id=self._resolve_thread_id(metadata.get("thread_id")),
            attributes=attrs,
        )

    def _from_checkpoint(self, raw: Dict[str, Any]) -> SentinelEvent:
        payload = raw.get("payload") or {}
        step = raw.get("step", 0)
        config = payload.get("config") or {}
        configurable = config.get("configurable") or {}
        checkpoint_id = configurable.get("checkpoint_id") or str(uuid.uuid4())
        thread_id = configurable.get("thread_id")
        metadata = payload.get("metadata") or {}
        source = metadata.get("source", "unknown")
        next_nodes = payload.get("next") or []

        return SentinelEvent(
            event_id=f"lg-checkpoint-{checkpoint_id}",
            event_type="checkpoint_saved",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"checkpoint saved at step {step} (source={source})",
            source_system=self.source_system,
            trace_id=self._resolve_thread_id(thread_id),
            attributes={
                "checkpoint_id": checkpoint_id,
                "thread_id": self._resolve_thread_id(thread_id),
                "step": step,
                "source": source,
                "next": next_nodes,
            },
        )

    def _from_unknown(self, raw: Dict[str, Any]) -> SentinelEvent:
        original_type = raw.get("type", "unknown")
        step = raw.get("step", 0)
        return SentinelEvent(
            event_id=f"lg-unknown-{uuid.uuid4()}",
            event_type="unknown_langgraph_event",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"unknown LangGraph event type '{original_type}' at step {step}",
            source_system=self.source_system,
            trace_id=self._thread_id,
            attributes={"original_type": original_type, "step": step},
        )
