# Langfuse adapter

Plug agentcop into any Langfuse 4.x application with two lines of setup. The
adapter registers a custom `SpanProcessor` on the Langfuse `TracerProvider`
and buffers translated `SentinelEvent` objects for every observation —
generation, span, tool, retriever, event, and guardrail — as they open and
close.

Langfuse 4.x is built entirely on OpenTelemetry. Every observation created
via `start_as_current_observation()`, `start_observation()`, `create_event()`,
or the `@observe` decorator becomes an OTel span enriched with
`langfuse.*` attributes. The adapter's `SpanProcessor` intercepts these spans
at `on_start` and `on_end` without affecting the normal Langfuse export
pipeline.

---

## Installation

```bash
pip install agentcop[langfuse]
```

---

## How it works

```
langfuse.Langfuse() / @observe decorator
      │
      │  TracerProvider emits OTel spans with langfuse.* attributes
      │    SpanProcessor.on_start  → observation_started event
      │    SpanProcessor.on_end    → *_finished / *_error event
      │      (non-Langfuse spans silently skipped)
      ▼
LangfuseSentinelAdapter._buffer  (thread-safe list)
      │
      ▼
adapter.flush_into(sentinel)
      │
      ▼
sentinel.detect_violations() → ViolationRecord list
```

The adapter translates 13 event types across observation categories:

| Category                 | type                   | `event_type`           | `severity` |
|--------------------------|------------------------|------------------------|------------|
| All (on open)            | `observation_started`  | `observation_started`  | INFO       |
| Span / Agent / Chain     | `span_finished`        | `span_finished`        | INFO       |
| Span / Agent / Chain     | `span_error`           | `span_error`           | ERROR      |
| Generation / Embedding   | `generation_finished`  | `generation_finished`  | INFO       |
| Generation / Embedding   | `generation_error`     | `generation_error`     | ERROR      |
| Tool                     | `tool_finished`        | `tool_finished`        | INFO       |
| Tool                     | `tool_error`           | `tool_error`           | ERROR      |
| Retriever                | `retriever_finished`   | `retriever_finished`   | INFO       |
| Retriever                | `retriever_error`      | `retriever_error`      | ERROR      |
| Event                    | `event_occurred`       | `event_occurred`       | INFO       |
| Guardrail                | `guardrail_finished`   | `guardrail_finished`   | INFO       |
| Guardrail                | `guardrail_error`      | `guardrail_error`      | ERROR      |

Error events are triggered when the observation's `level` is set to `"ERROR"`
or when the underlying OTel span status code is `ERROR`.

---

## Quickstart

```python
from langfuse import Langfuse
from agentcop import Sentinel
from agentcop.adapters.langfuse import LangfuseSentinelAdapter

# --- Your Langfuse client (reads LANGFUSE_* env vars) ---

langfuse = Langfuse()

# --- Audit layer ---

adapter = LangfuseSentinelAdapter(run_id="run-001")
adapter.setup(langfuse)   # registers SpanProcessor before any observations run

# --- Your pipeline (unchanged) ---

with langfuse.start_as_current_observation(name="my-pipeline") as root:
    with langfuse.start_as_current_observation(
        name="gpt-call",
        as_type="generation",
        model="gpt-4o-mini",
    ) as gen:
        # ... call your LLM ...
        gen.update(
            output="Hello!",
            usage_details={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        )

langfuse.flush()   # ensure all spans are exported before draining

# --- Inspect violations ---

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

`adapter.setup(langfuse)` only adds a `SpanProcessor`. It does not affect the
Langfuse export pipeline — all observations still appear in your Langfuse
dashboard as normal.

---

## Quickstart (`@observe` decorator)

```python
from langfuse import Langfuse, observe, get_client
from agentcop import Sentinel
from agentcop.adapters.langfuse import LangfuseSentinelAdapter

langfuse = Langfuse()
adapter = LangfuseSentinelAdapter(run_id="run-001")
adapter.setup(langfuse)

@observe(as_type="generation")
def call_llm(prompt: str) -> str:
    lf = get_client()
    lf.update_current_generation(model="gpt-4o-mini")
    return "The answer is 42."

@observe
def pipeline(query: str) -> str:
    return call_llm(f"Answer: {query}")

result = pipeline("What is 6 times 7?")

langfuse.flush()

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

---

## Writing detectors for Langfuse events

### Detect an LLM generation that failed

```python
from typing import Optional
from agentcop import SentinelEvent, ViolationRecord

def detect_generation_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "generation_error":
        return None
    return ViolationRecord(
        violation_type="llm_call_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "observation_name": event.attributes["observation_name"],
            "model": event.attributes.get("model", "unknown"),
            "error": event.attributes["status_message"],
        },
    )
```

### Detect a rate-limited LLM call

```python
RATE_LIMIT_SIGNALS = {"rate limit", "429", "quota exceeded", "too many requests"}

def detect_rate_limit(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "generation_error":
        return None
    msg = event.attributes.get("status_message", "").lower()
    if not any(sig in msg for sig in RATE_LIMIT_SIGNALS):
        return None
    return ViolationRecord(
        violation_type="llm_rate_limited",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "model": event.attributes.get("model"),
            "error": event.attributes["status_message"],
        },
    )
```

### Detect a guardrail block

```python
def detect_guardrail_block(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "guardrail_error":
        return None
    return ViolationRecord(
        violation_type="guardrail_blocked",
        severity="CRITICAL",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "guardrail": event.attributes["observation_name"],
            "reason": event.attributes["status_message"],
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
            "tool": event.attributes["observation_name"],
            "error": event.attributes["status_message"],
        },
    )
```

### Detect high token usage

```python
TOKEN_BUDGET = 4_000

def detect_token_budget_exceeded(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "generation_finished":
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

### Register detectors

```python
sentinel = Sentinel(detectors=[
    detect_generation_failure,
    detect_rate_limit,
    detect_guardrail_block,
    detect_tool_failure,
    detect_token_budget_exceeded,
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
adapter = LangfuseSentinelAdapter(run_id=str(uuid.uuid4()))
```

When `run_id` is `None`, the Langfuse trace ID (the 32-char hex string from the
OTel span context) is used instead. Either way, the Langfuse trace ID is always
stored in `event.attributes["langfuse_trace_id"]` so you can correlate
violations back to the exact trace in the Langfuse UI.

---

## Translating events manually

`to_sentinel_event(raw)` accepts a plain dict and is useful for offline
processing, replaying logged events, or testing detectors without a live
Langfuse client:

```python
event = adapter.to_sentinel_event({
    "type": "generation_error",
    "observation_name": "gpt-call",
    "model": "gpt-4o-mini",
    "status_message": "rate limit exceeded",
    "langfuse_trace_id": "abcdef1234567890abcdef1234567890",
})
```

Required key: `"type"`. All other keys are optional — missing values default
to `"unknown"`, `{}`, or empty string depending on the field.

---

## Multiple pipeline runs

```python
all_violations = []

for i, query in enumerate(queries):
    adapter = LangfuseSentinelAdapter(run_id=f"query-{i}")
    adapter.setup(langfuse)

    with langfuse.start_as_current_observation(name="pipeline") as root:
        # ... run pipeline ...
        pass

    langfuse.flush()

    sentinel = Sentinel(detectors=[detect_generation_failure, detect_guardrail_block])
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
adapter = LangfuseSentinelAdapter(run_id="ci-eval")
adapter.setup(langfuse)

with langfuse.start_as_current_observation(name="eval-run"):
    # ... your pipeline ...
    pass

langfuse.flush()

sentinel = Sentinel(detectors=[
    detect_generation_failure,
    detect_guardrail_block,
    detect_tool_failure,
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

|                  | LangGraph              | Haystack                    | Semantic Kernel                  | Langfuse                              |
|------------------|------------------------|-----------------------------|----------------------------------|---------------------------------------|
| Event delivery   | Pull (debug stream)    | Push (ProxyTracer wrapping) | Push (async filter middleware)   | Push (OTel SpanProcessor)             |
| Primary API      | `iter_events(stream)`  | `setup()` + `flush_into()`  | `setup(kernel)` + `flush_into()` | `setup(langfuse)` + `flush_into()`    |
| Correlation ID   | LangGraph task UUID    | `run_id` you provide        | `run_id` you provide             | `run_id` or Langfuse trace ID         |
| Coverage         | Graph nodes/edges      | Pipeline/component/LLM      | Function/prompt/auto-function    | All Langfuse observation types        |
| OTel native      | No                     | No                          | No                               | Yes — hooks OTel TracerProvider       |

---

## Attributes reference

### All events (`observation_started`, `*_finished`, `*_error`)

| Key                      | Type   | Description                                              |
|--------------------------|--------|----------------------------------------------------------|
| `observation_type`       | `str`  | Langfuse type: `span`, `generation`, `tool`, `agent`, … |
| `observation_name`       | `str`  | Name passed to the observation constructor               |
| `langfuse_trace_id`      | `str`  | 32-char hex trace ID from OTel span context              |
| `observation_id`         | `str`  | 16-char hex span ID                                      |
| `parent_observation_id`  | `str`  | 16-char hex parent span ID, or empty string              |
| `level`                  | `str`  | `DEBUG`, `DEFAULT`, `WARNING`, or `ERROR`                |
| `status_message`         | `str`  | Error description (populated on error events)            |
| `input`                  | `str`  | Observation input (≤500 chars, JSON string)              |
| `output`                 | `str`  | Observation output (≤500 chars, JSON string)             |
| `user_id`                | `str`  | Langfuse `user.id` propagated attribute                  |
| `session_id`             | `str`  | Langfuse `session.id` propagated attribute               |

### `observation_started` only

No additional attributes beyond the common set above. `input` is present if
set at construction time; `output` is empty (not yet populated at start).

### `generation_finished` / `generation_error` — additional attributes

| Key              | Type   | Description                                               |
|------------------|--------|-----------------------------------------------------------|
| `model`          | `str`  | Model identifier (`langfuse.observation.model.name`)      |
| `usage`          | `dict` | Token counts: `{"prompt_tokens": N, "completion_tokens": N, …}` |
| `cost`           | `dict` | Cost breakdown: `{"total_cost": 0.0023, …}`               |
| `prompt_name`    | `str`  | Langfuse prompt name (if using managed prompts)           |
| `prompt_version` | `str`  | Langfuse prompt version                                   |

---

## API reference

### `LangfuseSentinelAdapter(run_id=None)`

**Parameters**

- `run_id` (`str | None`) — Optional session identifier used as `trace_id` on
  every translated event. When `None`, the Langfuse trace ID from the OTel
  span context is used.

**Methods**

- `setup(langfuse_client=None)` — Register a `SpanProcessor` on the Langfuse
  `TracerProvider`. Call once before running any pipelines. When
  `langfuse_client` is `None`, the global singleton returned by
  `langfuse.get_client()` is used.

- `to_sentinel_event(raw: dict) -> SentinelEvent` — Translate one normalized
  event dict. Dispatches on `raw["type"]`. Unknown types map to
  `unknown_langfuse_event`. Never raises; missing keys fall back to safe
  defaults.

- `drain() -> list[SentinelEvent]` — Return and clear all buffered events.
  Thread-safe.

- `flush_into(sentinel: Sentinel) -> None` — Call `sentinel.ingest(self.drain())`.
  Ingest all buffered events and clear the buffer in one step.

**Class attribute**

- `source_system = "langfuse"` — appears on every translated `SentinelEvent`.

---

## Runtime security

`LangfuseSentinelAdapter` supports the full agentcop runtime security stack via four
optional constructor parameters. All default to `None` — existing code requires no changes.

### Constructor params

```python
LangfuseSentinelAdapter(
    run_id="run-001",
    gate=None,        # ExecutionGate
    permissions=None, # ToolPermissionLayer
    sandbox=None,     # AgentSandbox
    approvals=None,   # ApprovalBoundary
    identity=None,    # AgentIdentity
)
```

### What gets intercepted (observability adapter)

Langfuse is an observability adapter — it observes what happened rather than controlling
what executes.  When gate/permissions are provided, the gate is checked inside the
`SpanProcessor.on_start()` callback for **tool-type observations** (`langfuse.observation.type = "tool"`).

Gate decisions are logged as `gate_denied` or `permission_violation` SentinelEvents in the
adapter buffer.  The `PermissionError` is caught internally so the Langfuse OTel
infrastructure is never disrupted.  Use `adapter.drain()` to retrieve security events.

### Example

```python
from langfuse import Langfuse
from agentcop.adapters.langfuse import LangfuseSentinelAdapter
from agentcop.gate import ExecutionGate, ConditionalPolicy
from agentcop.permissions import ToolPermissionLayer, NetworkPermission

langfuse = Langfuse()

gate = ExecutionGate()
gate.register_policy("*", ConditionalPolicy(
    allow_if=lambda args: True,
    deny_reason="unknown tool",
))

adapter = LangfuseSentinelAdapter(
    run_id="run-001",
    gate=gate,
)
adapter.setup(langfuse)

# ... run your Langfuse-instrumented code ...

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```
