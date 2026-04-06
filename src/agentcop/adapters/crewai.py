"""
CrewAI adapter for agentcop.

Translates CrewAI execution events into SentinelEvents for forensic auditing.
Hooks into CrewAI's singleton event bus, buffers translated events during a
crew run, and exposes them for ingestion into a Sentinel instance.

Supported event categories and their SentinelEvent mapping:

  Crew:   crew_kickoff_started / completed / failed
  Agent:  agent_execution_started / completed / error
  Task:   task_started / completed / failed
  Tool:   tool_usage_started / finished / error

Install the optional dependency to use this adapter:

    pip install agentcop[crewai]

Quickstart::

    from agentcop import Sentinel
    from agentcop.adapters.crewai import CrewAISentinelAdapter

    adapter = CrewAISentinelAdapter(run_id="run-001")
    adapter.setup()          # register with the CrewAI event bus

    crew.kickoff()           # run your crew normally

    sentinel = Sentinel()
    adapter.flush_into(sentinel)
    violations = sentinel.detect_violations()
    sentinel.report()

``to_sentinel_event(raw)`` also accepts plain dicts for manual translation
or testing without a live crew::

    event = adapter.to_sentinel_event({
        "type": "agent_execution_error",
        "agent_role": "Researcher",
        "error": "API timeout",
    })
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from agentcop.adapters._runtime import check_tool_call
from agentcop.event import SentinelEvent


def _require_crewai() -> None:
    try:
        import crewai  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "CrewAI adapter requires 'crewai'. Install it with: pip install agentcop[crewai]"
        ) from exc


class CrewAISentinelAdapter:
    """
    Adapter that translates CrewAI execution events into SentinelEvents.

    Unlike the LangGraph adapter (which works with a pull-based debug stream),
    CrewAI is push-based: events arrive via a singleton event bus during
    execution. This adapter registers handlers with that bus, buffers translated
    events, and provides ``drain()`` / ``flush_into()`` to consume them.

    **Normalized dict schema** (what ``to_sentinel_event`` accepts):

    All dicts must have a ``"type"`` key matching one of the supported event
    type strings. Additional keys are type-specific (see table below).

    +---------------------------------+-------------------------------+-----------+
    | type                            | event_type (SentinelEvent)    | severity  |
    +=================================+===============================+===========+
    | crew_kickoff_started            | crew_kickoff_started          | INFO      |
    | crew_kickoff_completed          | crew_kickoff_completed        | INFO      |
    | crew_kickoff_failed             | crew_kickoff_failed           | ERROR     |
    | agent_execution_started         | agent_execution_started       | INFO      |
    | agent_execution_completed       | agent_execution_completed     | INFO      |
    | agent_execution_error           | agent_execution_error         | ERROR     |
    | task_started                    | task_started                  | INFO      |
    | task_completed                  | task_completed                | INFO      |
    | task_failed                     | task_failed                   | ERROR     |
    | tool_usage_started              | tool_usage_started            | INFO      |
    | tool_usage_finished             | tool_usage_finished           | INFO      |
    | tool_usage_error                | tool_usage_error              | ERROR     |
    | (anything else)                 | unknown_crewai_event          | INFO      |
    +---------------------------------+-------------------------------+-----------+

    Parameters
    ----------
    run_id:
        Optional run / session identifier used as ``trace_id`` on every
        translated event. Correlates all events from one crew execution.
    """

    source_system = "crewai"

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
        _require_crewai()
        self._run_id = run_id
        self._gate = gate
        self._permissions = permissions
        self._sandbox = sandbox
        self._approvals = approvals
        self._identity = identity
        self._buffer: list[SentinelEvent] = []
        self._lock = threading.Lock()

    def setup(self, event_bus=None) -> None:
        """
        Register event handlers with the CrewAI event bus.

        Call this once before ``crew.kickoff()``. If ``event_bus`` is None,
        the global ``crewai.utilities.events.crewai_event_bus`` singleton is
        used automatically.

        Parameters
        ----------
        event_bus:
            Optional event bus override (useful for testing with a mock bus).
        """
        from crewai.utilities.events import (  # type: ignore[import]
            AgentExecutionCompletedEvent,
            AgentExecutionErrorEvent,
            AgentExecutionStartedEvent,
            CrewKickoffCompletedEvent,
            CrewKickoffFailedEvent,
            CrewKickoffStartedEvent,
            TaskCompletedEvent,
            TaskFailedEvent,
            TaskStartedEvent,
            ToolUsageErrorEvent,
            ToolUsageFinishedEvent,
            ToolUsageStartedEvent,
        )
        from crewai.utilities.events import (
            crewai_event_bus as _default_bus,
        )

        bus = event_bus if event_bus is not None else _default_bus

        @bus.on(CrewKickoffStartedEvent)
        def _on_crew_kickoff_started(source, event):
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "crew_kickoff_started",
                        "timestamp": _ts(event),
                        "crew_name": getattr(event, "crew_name", None) or _name(source),
                    }
                )
            )

        @bus.on(CrewKickoffCompletedEvent)
        def _on_crew_kickoff_completed(source, event):
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "crew_kickoff_completed",
                        "timestamp": _ts(event),
                        "crew_name": getattr(event, "crew_name", None) or _name(source),
                        "output": str(getattr(event, "output", "") or "")[:500],
                    }
                )
            )

        @bus.on(CrewKickoffFailedEvent)
        def _on_crew_kickoff_failed(source, event):
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "crew_kickoff_failed",
                        "timestamp": _ts(event),
                        "crew_name": getattr(event, "crew_name", None) or _name(source),
                        "error": str(getattr(event, "error", "") or ""),
                    }
                )
            )

        @bus.on(AgentExecutionStartedEvent)
        def _on_agent_started(source, event):
            agent = getattr(event, "agent", None) or source
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "agent_execution_started",
                        "timestamp": _ts(event),
                        "agent_role": getattr(agent, "role", str(agent)),
                    }
                )
            )

        @bus.on(AgentExecutionCompletedEvent)
        def _on_agent_completed(source, event):
            agent = getattr(event, "agent", None) or source
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "agent_execution_completed",
                        "timestamp": _ts(event),
                        "agent_role": getattr(agent, "role", str(agent)),
                        "output": str(getattr(event, "output", "") or "")[:500],
                    }
                )
            )

        @bus.on(AgentExecutionErrorEvent)
        def _on_agent_error(source, event):
            agent = getattr(event, "agent", None) or source
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "agent_execution_error",
                        "timestamp": _ts(event),
                        "agent_role": getattr(agent, "role", str(agent)),
                        "error": str(getattr(event, "error", "") or ""),
                    }
                )
            )

        @bus.on(TaskStartedEvent)
        def _on_task_started(source, event):
            task = getattr(event, "task", None) or source
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "task_started",
                        "timestamp": _ts(event),
                        "task_description": _desc(task),
                        "agent_role": _agent_role(task),
                    }
                )
            )

        @bus.on(TaskCompletedEvent)
        def _on_task_completed(source, event):
            task = getattr(event, "task", None) or source
            output = getattr(event, "output", None)
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "task_completed",
                        "timestamp": _ts(event),
                        "task_description": _desc(task),
                        "output": str(getattr(output, "raw", output) or "")[:500],
                    }
                )
            )

        @bus.on(TaskFailedEvent)
        def _on_task_failed(source, event):
            task = getattr(event, "task", None) or source
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "task_failed",
                        "timestamp": _ts(event),
                        "task_description": _desc(task),
                        "error": str(getattr(event, "error", "") or ""),
                    }
                )
            )

        @bus.on(ToolUsageStartedEvent)
        def _on_tool_started(source, event):
            tool = getattr(event, "tool", None)
            tool_name = getattr(tool, "name", str(tool)) if tool else "unknown"
            agent_role = _name(source)
            if self._gate or self._permissions:
                check_tool_call(
                    self,
                    tool_name,
                    {},
                    context={"agent_role": agent_role},
                    agent_id=agent_role,
                )
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "tool_usage_started",
                        "timestamp": _ts(event),
                        "tool_name": tool_name,
                        "agent_role": agent_role,
                    }
                )
            )

        @bus.on(ToolUsageFinishedEvent)
        def _on_tool_finished(source, event):
            tool = getattr(event, "tool", None)
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "tool_usage_finished",
                        "timestamp": _ts(event),
                        "tool_name": getattr(tool, "name", str(tool)) if tool else "unknown",
                        "agent_role": _name(source),
                        "from_cache": bool(getattr(event, "from_cache", False)),
                    }
                )
            )

        @bus.on(ToolUsageErrorEvent)
        def _on_tool_error(source, event):
            tool = getattr(event, "tool", None)
            self._buffer_event(
                self.to_sentinel_event(
                    {
                        "type": "tool_usage_error",
                        "timestamp": _ts(event),
                        "tool_name": getattr(tool, "name", str(tool)) if tool else "unknown",
                        "agent_role": _name(source),
                        "error": str(getattr(event, "error", "") or ""),
                    }
                )
            )

    def to_sentinel_event(self, raw: dict[str, Any]) -> SentinelEvent:
        """
        Translate one CrewAI event dict into a SentinelEvent.

        ``raw`` must contain a ``"type"`` key. All other keys are
        type-specific. Unknown types are translated to
        ``unknown_crewai_event`` with severity INFO.
        """
        dispatch = {
            "crew_kickoff_started": self._from_crew_kickoff_started,
            "crew_kickoff_completed": self._from_crew_kickoff_completed,
            "crew_kickoff_failed": self._from_crew_kickoff_failed,
            "agent_execution_started": self._from_agent_execution_started,
            "agent_execution_completed": self._from_agent_execution_completed,
            "agent_execution_error": self._from_agent_execution_error,
            "task_started": self._from_task_started,
            "task_completed": self._from_task_completed,
            "task_failed": self._from_task_failed,
            "tool_usage_started": self._from_tool_usage_started,
            "tool_usage_finished": self._from_tool_usage_finished,
            "tool_usage_error": self._from_tool_usage_error,
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
    # Private translators
    # ------------------------------------------------------------------

    def _from_crew_kickoff_started(self, raw: dict[str, Any]) -> SentinelEvent:
        crew_name = raw.get("crew_name", "unknown")
        return SentinelEvent(
            event_id=f"crewai-crew-{uuid.uuid4()}",
            event_type="crew_kickoff_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"crew '{crew_name}' kickoff started",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"crew_name": crew_name},
        )

    def _from_crew_kickoff_completed(self, raw: dict[str, Any]) -> SentinelEvent:
        crew_name = raw.get("crew_name", "unknown")
        return SentinelEvent(
            event_id=f"crewai-crew-{uuid.uuid4()}",
            event_type="crew_kickoff_completed",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"crew '{crew_name}' kickoff completed",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "crew_name": crew_name,
                "output": raw.get("output", ""),
            },
        )

    def _from_crew_kickoff_failed(self, raw: dict[str, Any]) -> SentinelEvent:
        crew_name = raw.get("crew_name", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"crewai-crew-{uuid.uuid4()}",
            event_type="crew_kickoff_failed",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"crew '{crew_name}' kickoff failed: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"crew_name": crew_name, "error": error},
        )

    def _from_agent_execution_started(self, raw: dict[str, Any]) -> SentinelEvent:
        agent_role = raw.get("agent_role", "unknown")
        return SentinelEvent(
            event_id=f"crewai-agent-{uuid.uuid4()}",
            event_type="agent_execution_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"agent '{agent_role}' execution started",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"agent_role": agent_role},
        )

    def _from_agent_execution_completed(self, raw: dict[str, Any]) -> SentinelEvent:
        agent_role = raw.get("agent_role", "unknown")
        return SentinelEvent(
            event_id=f"crewai-agent-{uuid.uuid4()}",
            event_type="agent_execution_completed",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"agent '{agent_role}' execution completed",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "agent_role": agent_role,
                "output": raw.get("output", ""),
            },
        )

    def _from_agent_execution_error(self, raw: dict[str, Any]) -> SentinelEvent:
        agent_role = raw.get("agent_role", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"crewai-agent-{uuid.uuid4()}",
            event_type="agent_execution_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"agent '{agent_role}' execution error: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"agent_role": agent_role, "error": error},
        )

    def _from_task_started(self, raw: dict[str, Any]) -> SentinelEvent:
        task_description = raw.get("task_description", "unknown")
        agent_role = raw.get("agent_role", "")
        attrs: dict[str, Any] = {"task_description": task_description}
        if agent_role:
            attrs["agent_role"] = agent_role
        return SentinelEvent(
            event_id=f"crewai-task-{uuid.uuid4()}",
            event_type="task_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"task started: {task_description[:80]}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes=attrs,
        )

    def _from_task_completed(self, raw: dict[str, Any]) -> SentinelEvent:
        task_description = raw.get("task_description", "unknown")
        return SentinelEvent(
            event_id=f"crewai-task-{uuid.uuid4()}",
            event_type="task_completed",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"task completed: {task_description[:80]}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "task_description": task_description,
                "output": raw.get("output", ""),
            },
        )

    def _from_task_failed(self, raw: dict[str, Any]) -> SentinelEvent:
        task_description = raw.get("task_description", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"crewai-task-{uuid.uuid4()}",
            event_type="task_failed",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"task failed: {task_description[:80]}: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"task_description": task_description, "error": error},
        )

    def _from_tool_usage_started(self, raw: dict[str, Any]) -> SentinelEvent:
        tool_name = raw.get("tool_name", "unknown")
        agent_role = raw.get("agent_role", "unknown")
        return SentinelEvent(
            event_id=f"crewai-tool-{uuid.uuid4()}",
            event_type="tool_usage_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"tool '{tool_name}' called by agent '{agent_role}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"tool_name": tool_name, "agent_role": agent_role},
        )

    def _from_tool_usage_finished(self, raw: dict[str, Any]) -> SentinelEvent:
        tool_name = raw.get("tool_name", "unknown")
        agent_role = raw.get("agent_role", "unknown")
        return SentinelEvent(
            event_id=f"crewai-tool-{uuid.uuid4()}",
            event_type="tool_usage_finished",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"tool '{tool_name}' finished for agent '{agent_role}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "tool_name": tool_name,
                "agent_role": agent_role,
                "from_cache": raw.get("from_cache", False),
            },
        )

    def _from_tool_usage_error(self, raw: dict[str, Any]) -> SentinelEvent:
        tool_name = raw.get("tool_name", "unknown")
        agent_role = raw.get("agent_role", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"crewai-tool-{uuid.uuid4()}",
            event_type="tool_usage_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"tool '{tool_name}' error for agent '{agent_role}': {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"tool_name": tool_name, "agent_role": agent_role, "error": error},
        )

    def _from_unknown(self, raw: dict[str, Any]) -> SentinelEvent:
        original_type = raw.get("type", "unknown")
        return SentinelEvent(
            event_id=f"crewai-unknown-{uuid.uuid4()}",
            event_type="unknown_crewai_event",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"unknown CrewAI event type '{original_type}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"original_type": original_type},
        )


# ---------------------------------------------------------------------------
# Module-level helpers used by setup() handlers
# ---------------------------------------------------------------------------


def _ts(event) -> str | None:
    """Extract ISO timestamp string from a CrewAI event object."""
    ts = getattr(event, "timestamp", None)
    return str(ts) if ts is not None else None


def _name(obj) -> str:
    """Best-effort display name from a CrewAI source object."""
    return getattr(obj, "name", None) or getattr(obj, "role", None) or str(obj)


def _desc(task) -> str:
    """Task description, truncated to 200 characters."""
    return str(getattr(task, "description", None) or str(task))[:200]


def _agent_role(task) -> str:
    """Agent role assigned to a task, or empty string if unassigned."""
    agent = getattr(task, "agent", None)
    if agent:
        return getattr(agent, "role", str(agent))
    return ""
