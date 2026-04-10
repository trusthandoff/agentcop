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

---

## Runtime security

`LangGraphSentinelAdapter` supports the full agentcop runtime security stack via four
optional constructor parameters. All default to `None` — existing code requires no changes.

### Constructor params

```python
LangGraphSentinelAdapter(
    thread_id="run-abc",
    gate=None,        # ExecutionGate     — policy-based allow/deny per node call
    permissions=None, # ToolPermissionLayer — capability scope per agent
    sandbox=None,     # AgentSandbox      — path/domain/syscall enforcement
    approvals=None,   # ApprovalBoundary  — human-in-the-loop for high-risk nodes
    identity=None,    # AgentIdentity     — trust_score auto-tunes gate strictness
)
```

### What gets intercepted

The gate fires inside `iter_events()` for every **`task`** (node start) event before it is
yielded.  If the gate or permissions layer denies the call, a `PermissionError` is raised
and a `gate_denied` or `permission_violation` `SentinelEvent` is buffered.  Security events
can be drained with `adapter.drain()` or flushed via `adapter.flush_into(sentinel)`.

### Example

```python
from agentcop.adapters.langgraph import LangGraphSentinelAdapter
from agentcop.gate import ExecutionGate, ConditionalPolicy
from agentcop.permissions import ToolPermissionLayer, NetworkPermission
from agentcop.sandbox import AgentSandbox
from agentcop.approvals import ApprovalBoundary

gate = ExecutionGate()
gate.register_policy("internet_search", ConditionalPolicy(
    allow_if=lambda args: True,
    deny_reason="internet_search is rate-limited to 10/min",
))

permissions = ToolPermissionLayer()
permissions.declare("langgraph-agent", [
    NetworkPermission(domains=["api.openai.com"]),
])

sandbox = AgentSandbox(allowed_paths=["/tmp/*"], allowed_domains=["api.openai.com"])
approvals = ApprovalBoundary(requires_approval_above=75)

adapter = LangGraphSentinelAdapter(
    thread_id="run-abc",
    gate=gate,
    permissions=permissions,
    sandbox=sandbox,
    approvals=approvals,
)

sentinel = Sentinel()
try:
    sentinel.ingest(adapter.iter_events(
        graph.stream(input, config, stream_mode="debug")
    ))
except PermissionError as e:
    print(f"Node blocked: {e}")

# Drain runtime-security events into sentinel
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```

### AgentIdentity trust_score

When `identity` is supplied, its `trust_score` (0–100) is forwarded to the gate as
`context["trust_score"]`.  Use this in `ConditionalPolicy` predicates:

```python
gate.register_policy("*", ConditionalPolicy(
    allow_if=lambda args, ctx=None: (ctx or {}).get("trust_score", 0) >= 50,
    deny_reason="agent trust score too low",
))
```

---

## Reliability Tracking

Track execution-path consistency, tool variance, and retry patterns across
LangGraph runs using `wrap_for_reliability()` — no changes to your graph code required.

```python
from agentcop import ReliabilityStore
from agentcop import wrap_for_reliability
from agentcop.adapters.langgraph import LangGraphSentinelAdapter
from agentcop import Sentinel

store = ReliabilityStore("agentcop.db")
adapter = LangGraphSentinelAdapter(thread_id="run-abc")

# Wraps to_sentinel_event; infers run boundaries from task/task_result events
wrapped = wrap_for_reliability(adapter, agent_id="my-graph", store=store)

sentinel = Sentinel()
sentinel.ingest(
    wrapped.iter_events(graph.stream({"input": "..."}, config, stream_mode="debug"))
)
violations = sentinel.detect_violations()

# After several runs, inspect reliability
report = store.get_report("my-graph", window_hours=24)
print(report.reliability_tier)   # STABLE | VARIABLE | UNSTABLE | CRITICAL
print(report.reliability_score)  # 0-100
```

Or instrument individual tool calls inside a node using `ReliabilityTracer`:

```python
from agentcop import ReliabilityTracer, ReliabilityStore

store = ReliabilityStore("agentcop.db")

def my_node(state):
    with ReliabilityTracer("my-graph", store=store) as tracer:
        result = call_tool(state["input"])
        tracer.record_tool_call("call_tool", args=state, result=result)
        tracer.record_branch("my_node")
        tracer.record_tokens(input=150, output=300, model="gpt-4o")
    return {"output": result}
```

See [docs/guides/reliability.md](../guides/reliability.md) for the full guide.

---

## TrustChain Integration

Attach a cryptographic trust chain to every node execution. All four params
default to `None` — no changes required to existing code.

### Constructor params

```python
LangGraphSentinelAdapter(
    thread_id="run-abc",
    trust=None,        # TrustChainBuilder — SHA-256-linked execution chain
    attestor=None,     # NodeAttestor      — Ed25519 signatures per node
    hierarchy=None,    # AgentHierarchy    — supervisor/worker delegation rules
    trust_interop=None,# TrustInterop      — portable cross-runtime claim export
)
```

### What gets recorded

`record_trust_node()` is called inside `_from_task_result()` for every
successful `task_result` event (i.e. every completed graph node). The node's
name becomes `agent_id`; `tool_calls` is set to `[node_name]`.

### Example

```python
from agentcop.adapters.langgraph import LangGraphSentinelAdapter
from agentcop.trust import TrustChainBuilder, NodeAttestor, AgentHierarchy
from agentcop import Sentinel

private_pem, public_pem = NodeAttestor.generate_key_pair()

hierarchy = AgentHierarchy()
hierarchy.define(
    supervisor="orchestrator",
    workers=["planner", "executor"],
    can_delegate=True,
    max_depth=3,
    final_decision_authority="orchestrator",
)

adapter = LangGraphSentinelAdapter(
    thread_id="run-abc",
    trust=TrustChainBuilder(agent_id="my-graph"),
    attestor=NodeAttestor(private_key_pem=private_pem),
    hierarchy=hierarchy,
)

sentinel = Sentinel()
sentinel.ingest(adapter.iter_events(
    graph.stream(input, config, stream_mode="debug")
))

# After the run — inspect the trust chain
result = adapter._trust.verify_chain()
print(result.verified)    # True
print(result.broken_at)   # None (no tampering)
```

See [docs/guides/trust-chain.md](../guides/trust-chain.md) for the full guide.
