# CrewAI adapter

Plug agentcop into any CrewAI crew with two lines of setup. The adapter hooks
into CrewAI's singleton event bus, buffers translated events during the crew
run, and lets you drain them into a `Sentinel` instance for violation detection.

Unlike the LangGraph adapter (which reads a pull-based debug stream), CrewAI is
push-based: events arrive via callbacks during execution. The adapter registers
handlers before kickoff, then you call `flush_into(sentinel)` after.

---

## Installation

```bash
pip install agentcop[crewai]
```

---

## How it works

```
crewai_event_bus  (singleton, fires during crew.kickoff())
      │
      │  AgentExecutionStartedEvent(agent=...) → handler
      │  TaskCompletedEvent(output=...)        → handler
      │  ToolUsageErrorEvent(tool=..., ...)    → handler
      │  ...
      ▼
CrewAISentinelAdapter._buffer  (thread-safe list)
      │
      ▼
adapter.flush_into(sentinel)
      │
      ▼
sentinel.detect_violations() → ViolationRecord list
```

The adapter translates 12 CrewAI event types across four categories:

| Category | CrewAI event              | `event_type`              | `severity` |
|----------|---------------------------|---------------------------|------------|
| Crew     | `CrewKickoffStartedEvent` | `crew_kickoff_started`    | INFO       |
| Crew     | `CrewKickoffCompletedEvent` | `crew_kickoff_completed` | INFO       |
| Crew     | `CrewKickoffFailedEvent`  | `crew_kickoff_failed`     | ERROR      |
| Agent    | `AgentExecutionStartedEvent` | `agent_execution_started` | INFO    |
| Agent    | `AgentExecutionCompletedEvent` | `agent_execution_completed` | INFO  |
| Agent    | `AgentExecutionErrorEvent` | `agent_execution_error`  | ERROR      |
| Task     | `TaskStartedEvent`        | `task_started`            | INFO       |
| Task     | `TaskCompletedEvent`      | `task_completed`          | INFO       |
| Task     | `TaskFailedEvent`         | `task_failed`             | ERROR      |
| Tool     | `ToolUsageStartedEvent`   | `tool_usage_started`      | INFO       |
| Tool     | `ToolUsageFinishedEvent`  | `tool_usage_finished`     | INFO       |
| Tool     | `ToolUsageErrorEvent`     | `tool_usage_error`        | ERROR      |

---

## Quickstart

```python
from crewai import Agent, Crew, Task
from agentcop import Sentinel
from agentcop.adapters.crewai import CrewAISentinelAdapter

# --- Your crew (unchanged) ---

researcher = Agent(
    role="Researcher",
    goal="Find the latest AI safety developments",
    backstory="An expert researcher with access to the internet.",
)

writer = Agent(
    role="Writer",
    goal="Write a clear summary of AI safety findings",
    backstory="A technical writer who makes complex topics accessible.",
)

research_task = Task(
    description="Search for the top 3 AI safety developments this month.",
    expected_output="A bullet-point summary with sources.",
    agent=researcher,
)

write_task = Task(
    description="Write a 200-word article based on the research findings.",
    expected_output="A complete article ready for publication.",
    agent=writer,
)

crew = Crew(agents=[researcher, writer], tasks=[research_task, write_task])


# --- Audit layer ---

adapter = CrewAISentinelAdapter(run_id="run-001")
adapter.setup()          # register handlers before kickoff

result = crew.kickoff()  # events buffered during execution

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

That's all. No changes to the crew, agents, or tasks.

---

## Writing detectors for CrewAI events

### Detect any agent execution failure

```python
from typing import Optional
from agentcop import SentinelEvent, ViolationRecord

def detect_agent_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "agent_execution_error":
        return None
    return ViolationRecord(
        violation_type="agent_execution_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "agent_role": event.attributes["agent_role"],
            "error": event.attributes["error"],
        },
    )
```

### Detect task failure

```python
def detect_task_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "task_failed":
        return None
    return ViolationRecord(
        violation_type="task_execution_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "task": event.attributes["task_description"],
            "error": event.attributes["error"],
        },
    )
```

### Detect a blocked or rate-limited tool

```python
RATE_LIMIT_SIGNALS = {"429", "rate limit", "quota exceeded", "too many requests"}

def detect_tool_rate_limit(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "tool_usage_error":
        return None
    error = event.attributes.get("error", "").lower()
    if not any(sig in error for sig in RATE_LIMIT_SIGNALS):
        return None
    return ViolationRecord(
        violation_type="tool_rate_limited",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "tool": event.attributes["tool_name"],
            "agent": event.attributes["agent_role"],
            "error": event.attributes["error"],
        },
    )
```

### Detect a specific tool being called by an unexpected agent

```python
RESTRICTED_TOOLS = {"CodeInterpreterTool", "ShellCommandTool"}

def detect_unauthorized_tool_use(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "tool_usage_started":
        return None
    tool = event.attributes.get("tool_name", "")
    agent = event.attributes.get("agent_role", "")
    if tool in RESTRICTED_TOOLS and agent != "Code Executor":
        return ViolationRecord(
            violation_type="unauthorized_tool_use",
            severity="CRITICAL",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={"tool": tool, "agent": agent},
        )
```

### Register detectors

```python
sentinel = Sentinel(detectors=[
    detect_agent_failure,
    detect_task_failure,
    detect_tool_rate_limit,
    detect_unauthorized_tool_use,
])
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```

---

## `run_id` and trace correlation

Pass a `run_id` to correlate all events from one crew execution:

```python
import uuid
run_id = str(uuid.uuid4())
adapter = CrewAISentinelAdapter(run_id=run_id)
```

Every `SentinelEvent` produced during the run will carry `trace_id=run_id`. When
you inspect a `ViolationRecord`, `violation.trace_id` traces back to the same run.

---

## Translating events manually

`to_sentinel_event(raw)` accepts a plain dict and is useful for offline
processing, replaying logged events, or testing detectors without running a crew:

```python
event = adapter.to_sentinel_event({
    "type": "tool_usage_error",
    "tool_name": "SerperDevTool",
    "agent_role": "Researcher",
    "error": "rate limit exceeded",
    "timestamp": "2026-04-01T12:00:00Z",
})
```

Required key: `"type"`. All other keys are optional — missing values default
to `"unknown"` or empty string.

---

## Multi-crew workflows

For pipelines that run multiple crews in sequence, use one adapter per crew and
accumulate violations across runs:

```python
all_violations = []

for crew, run_id in [(research_crew, "run-research"), (writing_crew, "run-writing")]:
    adapter = CrewAISentinelAdapter(run_id=run_id)
    adapter.setup()
    crew.kickoff()
    sentinel = Sentinel(detectors=[detect_agent_failure, detect_task_failure])
    adapter.flush_into(sentinel)
    all_violations.extend(sentinel.detect_violations())

if all_violations:
    print(f"{len(all_violations)} violation(s) detected across pipeline")
    for v in all_violations:
        print(f"  [{v.severity}] {v.violation_type} trace={v.trace_id}")
```

---

## Assertion-style auditing

Raise on violations to hard-fail a pipeline in CI or evaluation:

```python
adapter = CrewAISentinelAdapter(run_id="ci-run")
adapter.setup()
crew.kickoff()

sentinel = Sentinel(detectors=[detect_agent_failure, detect_task_failure])
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()

if violations:
    for v in violations:
        print(f"[{v.severity}] {v.violation_type}: {v.detail}")
    raise RuntimeError(f"Crew run failed audit — {len(violations)} violation(s)")
```

---

## Differences from the LangGraph adapter

| | LangGraph | CrewAI |
|---|---|---|
| Event delivery | Pull (debug stream iterator) | Push (event bus callbacks) |
| Primary API | `iter_events(stream)` | `setup()` + `flush_into()` |
| Correlation ID | LangGraph task UUID | `run_id` you provide |
| Raw event format | `dict` from `stream_mode="debug"` | `dict` via `to_sentinel_event()` |
| Event bus | None needed | `crewai.utilities.events.crewai_event_bus` |

---

## Attributes reference

### Crew events (`crew_kickoff_*`)

| Key         | Type  | Present in           | Description           |
|-------------|-------|----------------------|-----------------------|
| `crew_name` | `str` | all crew events      | Crew name or str repr |
| `output`    | `str` | `completed` only     | Final crew output (≤500 chars) |
| `error`     | `str` | `failed` only        | Failure reason        |

### Agent events (`agent_execution_*`)

| Key          | Type  | Present in           | Description               |
|--------------|-------|----------------------|---------------------------|
| `agent_role` | `str` | all agent events     | Agent's `role` string     |
| `output`     | `str` | `completed` only     | Agent output (≤500 chars) |
| `error`      | `str` | `error` only         | Error message             |

### Task events (`task_*`)

| Key                | Type  | Present in                   | Description                 |
|--------------------|-------|------------------------------|-----------------------------|
| `task_description` | `str` | all task events              | Task description (≤200 chars) |
| `agent_role`       | `str` | `started` only (if assigned) | Assigned agent's role       |
| `output`           | `str` | `completed` only             | Task output (≤500 chars)    |
| `error`            | `str` | `failed` only                | Error message               |

### Tool events (`tool_usage_*`)

| Key          | Type   | Present in           | Description                        |
|--------------|--------|----------------------|------------------------------------|
| `tool_name`  | `str`  | all tool events      | Tool's `name` attribute            |
| `agent_role` | `str`  | all tool events      | Calling agent's role               |
| `from_cache` | `bool` | `finished` only      | Whether result came from cache     |
| `error`      | `str`  | `error` only         | Error message                      |

---

## API reference

### `CrewAISentinelAdapter(run_id=None)`

**Parameters**

- `run_id` (`str | None`) — Optional run identifier used as `trace_id` on
  every translated event. Recommended: pass a UUID per crew execution.

**Methods**

- `setup(event_bus=None)` — Register handlers with the CrewAI event bus.
  Call this once before `crew.kickoff()`. Pass a mock bus for testing.

- `to_sentinel_event(raw: dict) -> SentinelEvent` — Translate one event dict.
  Dispatches on `raw["type"]`. Unknown types map to `unknown_crewai_event`.
  Never raises; missing keys fall back to safe defaults.

- `drain() -> list[SentinelEvent]` — Return and clear all buffered events.
  Thread-safe.

- `flush_into(sentinel: Sentinel) -> None` — Call `sentinel.ingest(self.drain())`.
  Ingest all buffered events and clear the buffer in one step.

**Class attribute**

- `source_system = "crewai"` — appears on every translated `SentinelEvent`.

---

## Runtime security

`CrewAISentinelAdapter` supports the full agentcop runtime security stack via four optional
constructor parameters. All default to `None` — existing code requires no changes.

### Constructor params

```python
CrewAISentinelAdapter(
    run_id="run-001",
    gate=None,        # ExecutionGate
    permissions=None, # ToolPermissionLayer
    sandbox=None,     # AgentSandbox
    approvals=None,   # ApprovalBoundary
    identity=None,    # AgentIdentity
)
```

### What gets intercepted

The gate fires inside the **`ToolUsageStartedEvent`** handler, before the translated event
is buffered.  The agent's role string is used as `agent_id` for the `ToolPermissionLayer`
lookup.  If denied, `PermissionError` is raised and a `gate_denied` or
`permission_violation` SentinelEvent is buffered.

### Example

```python
from agentcop.adapters.crewai import CrewAISentinelAdapter
from agentcop.gate import ExecutionGate, DenyPolicy
from agentcop.permissions import ToolPermissionLayer, ExecutePermission
from agentcop.approvals import ApprovalBoundary

gate = ExecutionGate()
gate.register_policy("shell_exec", DenyPolicy(reason="shell execution is prohibited"))

permissions = ToolPermissionLayer()
permissions.declare("Researcher", [ExecutePermission(commands=[])])  # deny all execution

approvals = ApprovalBoundary(requires_approval_above=70)

adapter = CrewAISentinelAdapter(
    run_id="run-001",
    gate=gate,
    permissions=permissions,
    approvals=approvals,
)
adapter.setup()

crew.kickoff()

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```
