"""
Instrumentation helpers for adding reliability tracing to agent executions.

``ReliabilityTracer`` is a context manager that records a single execution as
an ``AgentRun`` and optionally persists it to a ``ReliabilityStore``.

``wrap_for_reliability`` intercepts an adapter's ``to_sentinel_event`` method
to capture runs generically from the event stream.
"""

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

from .models import AgentRun, ToolCall


def _hash(obj: Any) -> str:
    """Stable SHA-256 hex digest of any JSON-serialisable value."""
    serialised = json.dumps(obj, sort_keys=True, default=str)
    return hashlib.sha256(serialised.encode()).hexdigest()


# Token cost per million tokens (input_cost, output_cost) in USD — heuristics only.
_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.0),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5-turbo": (0.5, 1.5),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-opus": (15.0, 75.0),
    "claude-3-haiku": (0.25, 1.25),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (0.8, 4.0),
}


def _estimate_cost(model: str | None, input_tokens: int, output_tokens: int) -> float:
    """Return a rough USD cost estimate, or 0.0 when model is unknown."""
    if not model:
        return 0.0
    model_lower = model.lower()
    for prefix, (in_cost, out_cost) in _MODEL_COSTS.items():
        if model_lower.startswith(prefix):
            return input_tokens * in_cost / 1_000_000 + output_tokens * out_cost / 1_000_000
    return 0.0


class ReliabilityTracer:
    """
    Context manager that records a single agent execution as an ``AgentRun``.

    On ``__exit__`` the run is built from accumulated observations and
    optionally persisted to a :class:`~agentcop.reliability.store.ReliabilityStore`::

        store = ReliabilityStore()
        with ReliabilityTracer("my-agent", input_data=task, store=store) as tracer:
            tracer.record_tool_call("file_read", {"path": "/etc/hosts"}, content)
            tracer.record_branch("chose_path_A")
            tracer.record_tokens(input=100, output=250, model="gpt-4o")
            result = agent.run(task)
            tracer.set_output(result)

    The completed :class:`~agentcop.reliability.models.AgentRun` is available
    as ``tracer.run`` after the context exits, regardless of whether a store
    was provided.

    Exceptions raised inside the ``with`` block set ``success=False`` on the
    run but are **not** suppressed — they propagate normally.
    """

    def __init__(
        self,
        agent_id: str,
        *,
        input_data: Any = None,
        store: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self._input_hash = _hash(input_data) if input_data is not None else _hash("")
        self._store = store
        self._metadata: dict[str, Any] = dict(metadata) if metadata else {}

        self._tool_calls: list[ToolCall] = []
        self._execution_path: list[str] = []
        self._input_tokens = 0
        self._output_tokens = 0
        self._model: str | None = None
        self._retry_count = 0
        self._output_data: Any = None
        self._success = True
        self._start_time: float = 0.0
        self.run: AgentRun | None = None

    # ── Context manager ────────────────────────────────────────────────────

    def __enter__(self) -> "ReliabilityTracer":
        self._start_time = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            self._success = False
        self.run = self._build_run()
        if self._store is not None:
            self._store.record_run(self.agent_id, self.run)
        return None  # never suppress exceptions

    # ── Recording API ──────────────────────────────────────────────────────

    def record_tool_call(
        self,
        tool_name: str,
        args: Any,
        result: Any,
        *,
        duration_ms: int = 0,
        success: bool = True,
        retry_count: int = 0,
    ) -> None:
        """Record a tool invocation. Args and result are hashed for privacy."""
        self._tool_calls.append(
            ToolCall(
                tool_name=tool_name,
                args_hash=_hash(args),
                result_hash=_hash(result),
                duration_ms=duration_ms,
                success=success,
                retry_count=retry_count,
            )
        )

    def record_branch(self, step_name: str) -> None:
        """Record a step or branch decision in the execution path."""
        self._execution_path.append(step_name)

    def record_tokens(
        self,
        *,
        input: int = 0,
        output: int = 0,
        model: str | None = None,
    ) -> None:
        """Accumulate token counts. May be called multiple times — values add up."""
        self._input_tokens += input
        self._output_tokens += output
        if model is not None:
            self._model = model

    def set_output(self, output_data: Any) -> None:
        """Record the final output for hashing. Optional."""
        self._output_data = output_data

    def increment_retries(self, count: int = 1) -> None:
        """Increment the run-level retry counter."""
        self._retry_count += count

    # ── Internal ───────────────────────────────────────────────────────────

    def _build_run(self) -> AgentRun:
        elapsed_ms = int((time.monotonic() - self._start_time) * 1000)
        metadata = dict(self._metadata)
        if self._model:
            metadata["model"] = self._model
        return AgentRun(
            agent_id=self.agent_id,
            timestamp=datetime.now(UTC),
            input_hash=self._input_hash,
            output_hash=_hash(self._output_data),
            tool_calls=list(self._tool_calls),
            execution_path=list(self._execution_path),
            duration_ms=elapsed_ms,
            success=self._success,
            retry_count=self._retry_count,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            total_tokens=self._input_tokens + self._output_tokens,
            estimated_cost_usd=_estimate_cost(
                self._model, self._input_tokens, self._output_tokens
            ),
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Generic adapter wrapper
# ---------------------------------------------------------------------------

_RUN_START_TYPES = frozenset(
    {
        "node_start",
        "crew_kickoff_started",
        "agent_execution_started",
    }
)
_RUN_END_TYPES = frozenset(
    {
        "node_end",
        "crew_kickoff_completed",
        "agent_execution_completed",
    }
)
_TOOL_START_TYPES = frozenset({"tool_start", "tool_usage_started"})
_TOOL_END_TYPES = frozenset({"tool_end", "tool_usage_finished", "function_call_result"})
_ERROR_SUFFIX = "_error"


def wrap_for_reliability(
    adapter: Any,
    agent_id: str,
    *,
    store: Any = None,
) -> Any:
    """
    Wrap a SentinelAdapter to capture reliability data from the event stream.

    Intercepts ``adapter.to_sentinel_event`` and records tool calls, branch
    steps, and error states into a :class:`ReliabilityTracer` that is stored
    as ``adapter._reliability_tracer``.

    A new tracer is created on run-start events and flushed on run-end or
    error events.  Works with any adapter that implements ``to_sentinel_event``
    — no framework-specific assumptions.

    Returns the mutated adapter (same object).
    """
    original_method = adapter.to_sentinel_event
    state: dict[str, Any] = {"tracer": None, "pending_tool": None}

    def _instrumented(raw: Any) -> Any:
        event = original_method(raw)
        et = event.event_type

        if et in _RUN_START_TYPES:
            tracer = ReliabilityTracer(agent_id, store=store)
            tracer._start_time = time.monotonic()
            state["tracer"] = tracer
            state["pending_tool"] = None

        tracer: ReliabilityTracer | None = state["tracer"]

        if tracer is not None:
            if et in _TOOL_START_TYPES:
                state["pending_tool"] = event.attributes.get(
                    "tool", event.attributes.get("node", "unknown")
                )
            elif et in _TOOL_END_TYPES or et.endswith(_ERROR_SUFFIX):
                tool_name = state.get("pending_tool") or event.attributes.get("tool", "unknown")
                if et in _TOOL_END_TYPES:
                    tracer.record_tool_call(
                        tool_name,
                        event.attributes.get("inputs"),
                        event.attributes.get("output"),
                    )
                elif et.endswith(_ERROR_SUFFIX) and state.get("pending_tool"):
                    tracer.record_tool_call(
                        tool_name,
                        event.attributes.get("inputs"),
                        event.attributes.get("error"),
                        success=False,
                    )
                state["pending_tool"] = None

                if et in _RUN_END_TYPES or (et.endswith(_ERROR_SUFFIX) and tool_name == "run"):
                    if et.endswith(_ERROR_SUFFIX):
                        tracer._success = False
                    tracer.run = tracer._build_run()
                    if store is not None:
                        store.record_run(agent_id, tracer.run)
                    adapter._reliability_tracer = tracer
                    state["tracer"] = None
            elif et in _RUN_END_TYPES:
                tracer.run = tracer._build_run()
                if store is not None:
                    store.record_run(agent_id, tracer.run)
                adapter._reliability_tracer = tracer
                state["tracer"] = None
            else:
                node = event.attributes.get("node")
                if node:
                    tracer.record_branch(str(node))

        return event

    adapter.to_sentinel_event = _instrumented
    adapter._reliability_tracer = None
    return adapter
