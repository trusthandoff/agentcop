# LangSmith adapter

Plug agentcop into any LangSmith-traced application with two lines of setup.
The adapter wraps `client.create_run` and `client.update_run` on a
`langsmith.Client` instance, intercepting every run as it starts and completes
to produce `SentinelEvent` objects for forensic auditing.

LangSmith has no global "on run complete" hook at the SDK level. The adapter
solves this by wrapping the two methods on the specific client instance you
provide. An in-flight registry (keyed by run ID) correlates each `create_run`
(start) with its matching `update_run` (end), then translates the combined
snapshot into a typed end event. The original methods are always forwarded, so
all normal LangSmith upload behavior is unaffected.

---

## Installation

```bash
pip install agentcop[langsmith]
```

---

## How it works

```
client.create_run(...)      → run_started event
      │
      │  in-flight registry (keyed by run ID)
      │
client.update_run(run_id, ...) → *_finished / *_error event
      │
      ▼
LangSmithSentinelAdapter._buffer  (thread-safe list)
      │
      ▼
adapter.flush_into(sentinel)
      │
      ▼
sentinel.detect_violations() → ViolationRecord list
```

The adapter translates 11 event types across run categories:

| Run type                  | Success event        | Error event         | `severity` |
|---------------------------|----------------------|---------------------|------------|
| All (on start)            | `run_started`        | —                   | INFO       |
| chain / prompt / parser   | `chain_finished`     | `chain_error`       | INFO / ERROR |
| llm                       | `llm_finished`       | `llm_error`         | INFO / ERROR |
| tool                      | `tool_finished`      | `tool_error`        | INFO / ERROR |
| retriever                 | `retriever_finished` | `retriever_error`   | INFO / ERROR |
| embedding                 | `embedding_finished` | `embedding_error`   | INFO / ERROR |

Error events are triggered when the `error` argument to `update_run` is
non-empty or non-None.

---

## Quickstart

```python
from langsmith import Client, trace
from agentcop import Sentinel
from agentcop.adapters.langsmith import LangSmithSentinelAdapter

# --- Your LangSmith client (reads LANGCHAIN_API_KEY env var) ---

client = Client()

# --- Audit layer ---

adapter = LangSmithSentinelAdapter(run_id="run-001")
adapter.setup(client)   # wraps create_run / update_run before any runs fire

# --- Your pipeline (unchanged) ---

with trace("my-chain", client=client, inputs={"question": "What is RAG?"}) as run:
    with trace("gpt-call", run_type="llm", client=client,
               inputs={"prompt": "..."}) as llm_run:
        llm_run.add_metadata({
            "ls_model_name": "gpt-4o-mini",
            "ls_provider": "openai",
        })

# --- Inspect violations ---

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

`adapter.setup(client)` only wraps the two methods on the provided client. It
does not affect other clients or the global LangSmith state.

---

## Quickstart (`@traceable` decorator)

```python
from langsmith import Client, traceable
from agentcop import Sentinel
from agentcop.adapters.langsmith import LangSmithSentinelAdapter

client = Client()
adapter = LangSmithSentinelAdapter(run_id="run-001")
adapter.setup(client)

@traceable(run_type="llm", client=client)
def call_llm(prompt: str) -> str:
    return "The answer is 42."

@traceable(run_type="chain", client=client)
def pipeline(query: str) -> str:
    return call_llm(f"Answer: {query}")

result = pipeline("What is 6 times 7?")

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

---

## Writing detectors for LangSmith events

### Detect a chain run that failed

```python
from typing import Optional
from agentcop import SentinelEvent, ViolationRecord

def detect_chain_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "chain_error":
        return None
    return ViolationRecord(
        violation_type="chain_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "run_name": event.attributes["run_name"],
            "error": event.attributes["error"],
        },
    )
```

### Detect a rate-limited LLM call

```python
RATE_LIMIT_SIGNALS = {"rate limit", "429", "quota exceeded", "too many requests"}

def detect_rate_limit(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "llm_error":
        return None
    msg = event.attributes.get("error", "").lower()
    if not any(sig in msg for sig in RATE_LIMIT_SIGNALS):
        return None
    return ViolationRecord(
        violation_type="llm_rate_limited",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "model": event.attributes.get("model"),
            "error": event.attributes.get("error"),
        },
    )
```

### Detect a tool call that errored

```python
def detect_tool_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "tool_error":
        return None
    return ViolationRecord(
        violation_type="tool_execution_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "tool": event.attributes["run_name"],
            "error": event.attributes["error"],
        },
    )
```

### Detect high token usage

```python
TOKEN_BUDGET = 4_000

def detect_token_budget_exceeded(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "llm_finished":
        return None
    usage = event.attributes.get("usage") or {}
    total = usage.get("total_tokens", 0)
    if total <= TOKEN_BUDGET:
        return None
    return ViolationRecord(
        violation_type="token_budget_exceeded",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "model": event.attributes.get("model"),
            "total_tokens": total,
            "budget": TOKEN_BUDGET,
        },
    )
```

### Detect a retriever that returned no documents

```python
import json

def detect_empty_retrieval(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "retriever_finished":
        return None
    outputs_str = event.attributes.get("outputs", "")
    try:
        outputs = json.loads(outputs_str) if outputs_str else {}
    except (json.JSONDecodeError, TypeError):
        return None
    docs = outputs.get("documents") or outputs.get("docs") or []
    if docs:
        return None
    return ViolationRecord(
        violation_type="empty_retrieval",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={"retriever": event.attributes["run_name"]},
    )
```

### Register detectors

```python
sentinel = Sentinel(detectors=[
    detect_chain_failure,
    detect_rate_limit,
    detect_tool_failure,
    detect_token_budget_exceeded,
    detect_empty_retrieval,
])
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```

---

## `run_id` and trace correlation

When `run_id` is set it is used as `trace_id` on every `SentinelEvent`,
consistent with other adapters:

```python
import uuid
adapter = LangSmithSentinelAdapter(run_id=str(uuid.uuid4()))
```

When `run_id` is `None`, the LangSmith trace ID (the root run's UUID, passed
as `trace_id` to `create_run`) is used instead. Either way, the LangSmith
trace ID is stored in `event.attributes["ls_trace_id"]` so you can correlate
violations back to the exact trace in the LangSmith UI.

---

## Translating events manually

`to_sentinel_event(raw)` accepts a plain dict and is useful for offline
processing, replaying logged events, or testing detectors without a live
LangSmith client:

```python
event = adapter.to_sentinel_event({
    "type": "llm_error",
    "run_name": "gpt-call",
    "model": "gpt-4o-mini",
    "error": "rate limit exceeded",
    "ls_trace_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
})
```

Required key: `"type"`. All other keys are optional — missing values default
to `"unknown"`, `{}`, or empty string depending on the field.

---

## Multiple pipeline runs

```python
all_violations = []

for i, query in enumerate(queries):
    adapter = LangSmithSentinelAdapter(run_id=f"query-{i}")
    adapter.setup(client)

    with trace("pipeline", client=client, inputs={"query": query}):
        # ... run pipeline ...
        pass

    sentinel = Sentinel(detectors=[detect_chain_failure, detect_rate_limit])
    adapter.flush_into(sentinel)
    all_violations.extend(sentinel.detect_violations())

if all_violations:
    print(f"{len(all_violations)} violation(s) across {len(queries)} runs")
    for v in all_violations:
        print(f"  [{v.severity}] {v.violation_type} trace={v.trace_id}")
```

---

## Assertion-style auditing in CI

```python
adapter = LangSmithSentinelAdapter(run_id="ci-eval")
adapter.setup(client)

with trace("eval-run", client=client, inputs={"query": test_query}):
    # ... your pipeline ...
    pass

sentinel = Sentinel(detectors=[
    detect_chain_failure,
    detect_tool_failure,
    detect_rate_limit,
])
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()

if violations:
    for v in violations:
        print(f"[{v.severity}] {v.violation_type}: {v.detail}")
    raise RuntimeError(f"Pipeline run failed audit — {len(violations)} violation(s)")
```

---

## Differences from other adapters

|                  | LangGraph              | Haystack                    | Semantic Kernel                  | Langfuse                              | LangSmith                              |
|------------------|------------------------|-----------------------------|----------------------------------|---------------------------------------|----------------------------------------|
| Event delivery   | Pull (debug stream)    | Push (ProxyTracer wrapping) | Push (async filter middleware)   | Push (OTel SpanProcessor)             | Push (client method wrapping)          |
| Primary API      | `iter_events(stream)`  | `setup()` + `flush_into()`  | `setup(kernel)` + `flush_into()` | `setup(langfuse)` + `flush_into()`    | `setup(client)` + `flush_into()`       |
| Correlation ID   | LangGraph task UUID    | `run_id` you provide        | `run_id` you provide             | `run_id` or Langfuse trace ID         | `run_id` or LangSmith trace UUID       |
| Coverage         | Graph nodes/edges      | Pipeline/component/LLM      | Function/prompt/auto-function    | All Langfuse observation types        | All run types (chain/llm/tool/…)       |
| Hook mechanism   | debug stream           | tracer replacement          | kernel filter middleware         | OTel TracerProvider                   | create_run / update_run wrapping       |

---

## Attributes reference

### `run_started`

| Key                | Type   | Description                                         |
|--------------------|--------|-----------------------------------------------------|
| `run_id`           | `str`  | LangSmith run UUID                                  |
| `run_name`         | `str`  | Name passed to `create_run` / `@traceable`          |
| `run_type`         | `str`  | `chain`, `llm`, `tool`, `retriever`, `embedding`, … |
| `ls_trace_id`      | `str`  | Root run UUID (LangSmith trace ID)                  |
| `parent_run_id`    | `str`  | Parent run UUID, or empty string for root runs      |
| `tags`             | `list` | Tags attached to the run                            |
| `metadata`         | `dict` | Extra metadata dict from `extra["metadata"]`        |
| `inputs`           | `str`  | Run inputs as JSON string (≤500 chars)              |

### All end events (`*_finished`, `*_error`) — common attributes

| Key                | Type   | Description                                         |
|--------------------|--------|-----------------------------------------------------|
| `run_id`           | `str`  | LangSmith run UUID                                  |
| `run_name`         | `str`  | Name of the run                                     |
| `run_type`         | `str`  | LangSmith run type                                  |
| `ls_trace_id`      | `str`  | Root run UUID                                       |
| `parent_run_id`    | `str`  | Parent run UUID, or empty string                    |
| `tags`             | `list` | Tags attached to the run                            |
| `metadata`         | `dict` | Merged metadata from create + update                |
| `inputs`           | `str`  | Run inputs as JSON string (≤500 chars)              |
| `outputs`          | `str`  | Run outputs as JSON string (≤500 chars)             |
| `error`            | `str`  | Error string (populated on `*_error` events)        |

### `llm_finished` / `llm_error` — additional attributes

| Key        | Type   | Description                                              |
|------------|--------|----------------------------------------------------------|
| `model`    | `str`  | Model name from `extra["metadata"]["ls_model_name"]`     |
| `provider` | `str`  | Provider from `extra["metadata"]["ls_provider"]`         |
| `usage`    | `dict` | Token counts from `extra["metadata"]["usage_metadata"]`  |

---

## API reference

### `LangSmithSentinelAdapter(run_id=None)`

**Parameters**

- `run_id` (`str | None`) — Optional session identifier used as `trace_id` on
  every translated event. When `None`, the LangSmith trace ID (root run UUID)
  from the run is used instead.

**Methods**

- `setup(client=None)` — Wrap `create_run` and `update_run` on a
  `langsmith.Client`. Call once before running any pipelines. When `client` is
  `None`, a default `Client()` is constructed (reads `LANGCHAIN_API_KEY` /
  `LANGCHAIN_ENDPOINT` from the environment).

- `to_sentinel_event(raw: dict) -> SentinelEvent` — Translate one normalized
  event dict. Dispatches on `raw["type"]`. Unknown types map to
  `unknown_langsmith_event`. Never raises; missing keys fall back to safe
  defaults.

- `drain() -> list[SentinelEvent]` — Return and clear all buffered events.
  Thread-safe.

- `flush_into(sentinel: Sentinel) -> None` — Call `sentinel.ingest(self.drain())`.
  Ingest all buffered events and clear the buffer in one step.

**Class attribute**

- `source_system = "langsmith"` — appears on every translated `SentinelEvent`.

---

## Runtime security

`LangSmithSentinelAdapter` supports the full agentcop runtime security stack via four
optional constructor parameters. All default to `None` — existing code requires no changes.

### Constructor params

```python
LangSmithSentinelAdapter(
    run_id="run-001",
    gate=None,        # ExecutionGate
    permissions=None, # ToolPermissionLayer
    sandbox=None,     # AgentSandbox
    approvals=None,   # ApprovalBoundary
    identity=None,    # AgentIdentity
)
```

### What gets intercepted (observability adapter)

LangSmith is an observability adapter.  When gate/permissions are provided, the gate is
checked inside `_intercepted_create()` for **tool-type runs** (`run_type = "tool"`).
Gate decisions are logged as SentinelEvents; the `PermissionError` is caught so LangSmith
export is never disrupted.

### Example

```python
from langsmith import Client
from agentcop.adapters.langsmith import LangSmithSentinelAdapter
from agentcop.gate import ExecutionGate, ConditionalPolicy
from agentcop.permissions import ToolPermissionLayer, ExecutePermission

client = Client()

gate = ExecutionGate()
gate.register_policy("shell", ConditionalPolicy(
    allow_if=lambda args: False,
    deny_reason="shell execution is prohibited",
))

permissions = ToolPermissionLayer()
permissions.declare("default", [ExecutePermission(commands=["ls", "cat"])])

adapter = LangSmithSentinelAdapter(
    run_id="run-001",
    gate=gate,
    permissions=permissions,
)
adapter.setup(client)

# ... run your LangSmith-traced code ...

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```

---

## Reliability Tracking

Combine agentcop's LangSmith run tracing with reliability scoring. Use
`wrap_for_reliability()` on the LangSmith adapter, or record runs explicitly
with `ReliabilityTracer` inside your traced callbacks.

```python
from agentcop import ReliabilityStore
from agentcop import wrap_for_reliability
from agentcop.adapters.langsmith import LangSmithSentinelAdapter
from langsmith import Client

store = ReliabilityStore("agentcop.db")
client = Client()

adapter = LangSmithSentinelAdapter(run_id="run-001")
wrapped = wrap_for_reliability(adapter, agent_id="my-langsmith-agent", store=store)
wrapped.setup(client)

# ... run your LangSmith-traced code ...

report = store.get_report("my-langsmith-agent", window_hours=24)
print(report.reliability_tier)
print(report.path_entropy)    # how varied are the execution paths?
print(report.tool_variance)   # how consistently are tools being used?
```

Or use `ReliabilityTracer` directly inside a LangSmith-traced function:

```python
from agentcop import ReliabilityTracer, ReliabilityStore
from langsmith import traceable

store = ReliabilityStore("agentcop.db")

@traceable
def my_agent(input: str) -> str:
    with ReliabilityTracer("my-agent", store=store, input_data=input) as tracer:
        result = call_tool(input)
        tracer.record_tool_call("call_tool", args={"input": input}, result=result)
        tracer.record_tokens(input=100, output=200, model="gpt-4o")
    return result
```

See [docs/guides/reliability.md](../guides/reliability.md) for the full guide.

---

## TrustChain Integration

Wire a `TrustObserver` into the LangSmith adapter to export trust metrics
alongside run data. All three params default to `None` — no changes required.

### Constructor params

```python
LangSmithSentinelAdapter(
    run_id="run-001",
    trust_observer=None, # TrustObserver — OTel/LangSmith/Datadog/Prometheus export
    hierarchy=None,      # AgentHierarchy — delegation rules (stored for manual use)
    trust_interop=None,  # TrustInterop  — portable claim export
)
```

### What gets recorded

`trust_observer.record_verified_chain()` is called inside
`_intercepted_update()` after a successful run completes (i.e. `update_run` is
called without an `error` argument). This increments the
`agentcop_trust_verified_chains_total` Prometheus counter and fires any
configured webhooks.

### Example

```python
from langsmith import Client
from agentcop.adapters.langsmith import LangSmithSentinelAdapter
from agentcop.trust import TrustObserver
from agentcop import Sentinel

observer = TrustObserver(webhook_url="https://hooks.example.com/trust")

client = Client()
adapter = LangSmithSentinelAdapter(
    run_id="run-001",
    trust_observer=observer,
)
adapter.setup(client)

# ... run your LangSmith-traced code ...

sentinel = Sentinel()
adapter.flush_into(sentinel)

print(observer.to_prometheus_metrics())
# agentcop_trust_verified_chains_total 3
# agentcop_trust_delegation_violations_total 0
# agentcop_trust_boundary_violations_total 0

# Export chain to LangSmith run format
ls_run = observer.to_langsmith_run(chain)
```

See [docs/guides/trust-chain.md](../guides/trust-chain.md) for the full guide.
