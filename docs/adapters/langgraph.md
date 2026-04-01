# LangGraph adapter

Plug agentcop into any LangGraph graph with zero changes to your graph code.
The adapter reads LangGraph's debug event stream — every node start, node
result, and checkpoint save — and translates each into a `SentinelEvent` that
your violation detectors can inspect.

---

## Installation

```bash
pip install agentcop[langgraph]
```

This installs `agentcop` and `langgraph>=0.2` together. If you already have
`langgraph` installed, the adapter will use it automatically.

---

## How it works

LangGraph's `graph.stream(..., stream_mode="debug")` emits a structured event
for every step of execution. The adapter translates each dict into a
`SentinelEvent` with a consistent `event_type`, structured `attributes`, and
a `trace_id` tied to the graph run's `thread_id`.

```
graph.stream(input, config, stream_mode="debug")
      │
      │  {"type": "task", "step": 1, "payload": {"name": "planner", ...}}
      │  {"type": "task_result", "step": 1, "payload": {"name": "planner", ...}}
      │  {"type": "checkpoint", "step": 1, "payload": {...}}
      ▼
LangGraphSentinelAdapter.iter_events()
      │
      │  SentinelEvent(event_type="node_start",  attributes={"node": "planner", ...})
      │  SentinelEvent(event_type="node_end",    attributes={"node": "planner", ...})
      │  SentinelEvent(event_type="checkpoint_saved", attributes={"step": 1, ...})
      ▼
Sentinel.ingest() → detect_violations() → ViolationRecord list
```

**Event type mapping:**

| LangGraph debug event | `event_type`              | `severity` |
|-----------------------|---------------------------|------------|
| `task`                | `node_start`              | `INFO`     |
| `task_result`         | `node_end`                | `INFO`     |
| `task_result` (error) | `node_error`              | `ERROR`    |
| `checkpoint`          | `checkpoint_saved`        | `INFO`     |
| anything else         | `unknown_langgraph_event` | `INFO`     |

---

## Quickstart

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
import operator

from agentcop import Sentinel
from agentcop.adapters.langgraph import LangGraphSentinelAdapter


# --- Your graph (unchanged) ---

class AgentState(TypedDict):
    messages: Annotated[list, operator.add]

def planner(state: AgentState) -> AgentState:
    return {"messages": ["plan: fetch weather"]}

def executor(state: AgentState) -> AgentState:
    return {"messages": ["done: 72°F, sunny"]}

builder = StateGraph(AgentState)
builder.add_node("planner", planner)
builder.add_node("executor", executor)
builder.set_entry_point("planner")
builder.add_edge("planner", "executor")
builder.add_edge("executor", END)
graph = builder.compile()


# --- Audit layer ---

config = {"configurable": {"thread_id": "run-001"}}
adapter = LangGraphSentinelAdapter(thread_id="run-001")
sentinel = Sentinel()

sentinel.ingest(
    adapter.iter_events(
        graph.stream({"messages": []}, config, stream_mode="debug")
    )
)

violations = sentinel.detect_violations()
sentinel.report()
```

The four built-in detectors (`packet_rejected`, `capability_stale`,
`token_overlap_used`, `ai_generated_payload`) will not fire on LangGraph
events — those event types don't come from LangGraph. To detect LangGraph-
specific problems, write custom detectors as shown below.

---

## Writing detectors for LangGraph events

Detectors are plain functions: `(SentinelEvent) -> ViolationRecord | None`.
Return a `ViolationRecord` when a violation is found, `None` otherwise.

### Detect node execution failures

```python
from typing import Optional
from agentcop import SentinelEvent, ViolationRecord

def detect_node_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "node_error":
        return None
    return ViolationRecord(
        violation_type="node_execution_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "node": event.attributes["node"],
            "step": event.attributes["step"],
            "error": event.attributes["error"],
        },
    )
```

### Detect a specific node being called

Useful for auditing that a gating node (e.g. `human_approval`) ran before
a high-risk node (e.g. `code_executor`).

```python
def detect_unapproved_execution(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "node_start":
        return None
    if event.attributes.get("node") != "code_executor":
        return None
    # Check that "human_approval" appeared in triggers
    triggers = event.attributes.get("triggers", [])
    if not any("human_approval" in t for t in triggers):
        return ViolationRecord(
            violation_type="unapproved_code_execution",
            severity="CRITICAL",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={
                "node": "code_executor",
                "triggers": triggers,
                "step": event.attributes["step"],
            },
        )
```

### Detect excessive graph depth

```python
MAX_STEPS = 20

def detect_runaway_graph(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "node_start":
        return None
    step = event.attributes.get("step", 0)
    if step > MAX_STEPS:
        return ViolationRecord(
            violation_type="excessive_graph_depth",
            severity="WARN",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={
                "step": step,
                "max_allowed": MAX_STEPS,
                "node": event.attributes["node"],
            },
        )
```

### Register detectors alongside built-ins

```python
sentinel = Sentinel()
sentinel.register_detector(detect_node_failure)
sentinel.register_detector(detect_unapproved_execution)
sentinel.register_detector(detect_runaway_graph)
```

Or replace the defaults entirely if you only want LangGraph-specific detection:

```python
sentinel = Sentinel(detectors=[
    detect_node_failure,
    detect_unapproved_execution,
    detect_runaway_graph,
])
```

---

## Thread ID and trace correlation

Pass the same `thread_id` to the adapter that you pass to the graph's
`configurable`. This becomes `trace_id` on every `SentinelEvent`, so all
events from one graph run are correlated:

```python
run_id = "run-abc-123"
config = {"configurable": {"thread_id": run_id}}
adapter = LangGraphSentinelAdapter(thread_id=run_id)
```

Checkpoint events also extract `thread_id` from
`payload.config.configurable.thread_id` directly, so correlation works even
if you don't pass `thread_id` to the adapter — as long as the graph is
configured with a `thread_id`.

---

## Batch post-run auditing

You don't have to audit in real time. Collect all raw debug events first,
then audit the whole run at once:

```python
raw_events = list(graph.stream(input, config, stream_mode="debug"))

adapter = LangGraphSentinelAdapter(thread_id="run-001")
sentinel = Sentinel(detectors=[detect_node_failure, detect_runaway_graph])
sentinel.ingest(adapter.iter_events(raw_events))

violations = sentinel.detect_violations()
if violations:
    sentinel.report()
    raise RuntimeError(f"{len(violations)} violation(s) detected — halting pipeline")
```

This pattern is useful in CI, evaluation pipelines, or any context where you
want to assert that a graph run was clean before consuming its output.

---

## Auditing multi-turn conversations

For graphs with a checkpointer (multi-turn state persistence), each turn
produces its own stream of debug events. Audit each turn independently or
accumulate across turns:

```python
from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()
graph = builder.compile(checkpointer=checkpointer)

config = {"configurable": {"thread_id": "session-42"}}
adapter = LangGraphSentinelAdapter(thread_id="session-42")

all_violations = []

for user_input in ["What's the weather?", "Book a flight there."]:
    sentinel = Sentinel(detectors=[detect_node_failure])
    sentinel.ingest(
        adapter.iter_events(
            graph.stream({"messages": [user_input]}, config, stream_mode="debug")
        )
    )
    turn_violations = sentinel.detect_violations()
    all_violations.extend(turn_violations)

print(f"Total violations across {len(all_violations)} turns")
```

---

## Combining with OTel export

If you have `opentelemetry-sdk` installed, pipe violations out as OTel log
records alongside the adapter:

```python
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
from opentelemetry.sdk._logs.export.in_memory_span_exporter import InMemoryLogExporter

from agentcop import Sentinel
from agentcop.adapters.langgraph import LangGraphSentinelAdapter
from agentcop.otel import OtelSentinelExporter

provider = LoggerProvider()
exporter = InMemoryLogExporter()
provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
otel_exporter = OtelSentinelExporter(logger_provider=provider)

adapter = LangGraphSentinelAdapter(thread_id="run-001")
sentinel = Sentinel(detectors=[detect_node_failure])

sentinel.ingest(
    adapter.iter_events(
        graph.stream(input, config, stream_mode="debug")
    )
)

violations = sentinel.detect_violations()
otel_exporter.export(
    sentinel.detect_violations()   # violations are SentinelEvents in disguise
)
```

> **Note:** `OtelSentinelExporter.export()` takes a sequence of `SentinelEvent`,
> not `ViolationRecord`. To export violations as OTel records, convert them or
> export the raw `SentinelEvent` objects from `sentinel._events` directly.

---

## Attributes reference

### `node_start` (from `task`)

| Key        | Type           | Description                                              |
|------------|----------------|----------------------------------------------------------|
| `node`     | `str`          | Node name as registered in the graph                     |
| `task_id`  | `str`          | LangGraph's internal task UUID                           |
| `step`     | `int`          | Execution step counter (0-indexed)                       |
| `triggers` | `list[str]`    | What triggered this node (e.g. `["__start__", "planner"]`) |

### `node_end` (from `task_result`, no error)

| Key       | Type  | Description                              |
|-----------|-------|------------------------------------------|
| `node`    | `str` | Node name                                |
| `task_id` | `str` | LangGraph's internal task UUID           |
| `step`    | `int` | Execution step counter                   |

### `node_error` (from `task_result`, error set)

| Key          | Type           | Description                              |
|--------------|----------------|------------------------------------------|
| `node`       | `str`          | Node name                                |
| `task_id`    | `str`          | LangGraph's internal task UUID           |
| `step`       | `int`          | Execution step counter                   |
| `error`      | `str`          | The error message string                 |
| `interrupts` | `list` (opt.)  | Present only if LangGraph interrupts fired |

### `checkpoint_saved` (from `checkpoint`)

| Key             | Type        | Description                                              |
|-----------------|-------------|----------------------------------------------------------|
| `checkpoint_id` | `str`       | LangGraph checkpoint UUID                                |
| `thread_id`     | `str\|None` | Thread / run ID from `configurable`                      |
| `step`          | `int`       | Step at which the checkpoint was saved                   |
| `source`        | `str`       | `"input"`, `"loop"`, or `"update"` — LangGraph's label  |
| `next`          | `list[str]` | Node names scheduled to run after this checkpoint        |

---

## API reference

### `LangGraphSentinelAdapter(thread_id=None)`

**Parameters**

- `thread_id` (`str | None`) — Default trace/run ID used as `trace_id` on all
  translated events when the raw event does not carry one. Typically set to the
  same value as `config["configurable"]["thread_id"]`.

**Methods**

- `to_sentinel_event(raw: dict) -> SentinelEvent` — Translate one LangGraph
  debug event dict. Handles missing keys gracefully; never raises.

- `iter_events(stream: Iterable[dict]) -> Iterator[SentinelEvent]` — Yield a
  `SentinelEvent` for each item in a debug stream. Accepts any iterable,
  including generators from `graph.stream()`.

**Class attribute**

- `source_system = "langgraph"` — appears on every translated `SentinelEvent`.
