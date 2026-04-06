# Datadog adapter

Plug agentcop into any ddtrace-instrumented application with two lines of
setup. The adapter wraps `tracer._writer.write()` on a `ddtrace.Tracer`
instance, intercepting every completed trace exported to the Datadog Agent and
converting each span into a `SentinelEvent` for forensic auditing.

ddtrace instruments Python applications by wrapping library calls and
submitting completed traces through a `TraceWriter`. This adapter hooks into
that pipeline, categorizes each span by its `component` tag, and buffers
translated events for later inspection. The original `write()` is always
called, so all normal Datadog APM export and dashboards are unaffected.

---

## Installation

```bash
pip install agentcop[ddtrace]
```

---

## How it works

```
ddtrace.tracer (instruments your code)
      │
      │  span finishes → trace written to TraceWriter
      │    _writer.write(spans)  ← intercepted here
      │
DatadogSentinelAdapter._buffer  (thread-safe list)
      │
      ▼
adapter.flush_into(sentinel)
      │
      ▼
sentinel.detect_violations() → ViolationRecord list
```

Spans are classified by their `component` tag:

| `component` tag value                                    | Success event        | Error event         | `severity` |
|----------------------------------------------------------|----------------------|---------------------|------------|
| openai, anthropic, langchain, llamaindex, bedrock, …     | `llm_span_finished`  | `llm_span_error`    | INFO / ERROR |
| requests, httpx, flask, django, fastapi, grpc, …         | `http_span_finished` | `http_span_error`   | INFO / ERROR |
| sqlalchemy, redis, pymongo, psycopg2, elasticsearch, …   | `db_span_finished`   | `db_span_error`     | INFO / ERROR |
| *(anything else)*                                        | `span_finished`      | `span_error`        | INFO / ERROR |

Error events are triggered when `span.error` is non-zero.

---

## Quickstart

```python
import ddtrace
from agentcop import Sentinel
from agentcop.adapters.datadog import DatadogSentinelAdapter

# --- Audit layer ---

adapter = DatadogSentinelAdapter(run_id="run-001")
adapter.setup()   # wraps ddtrace.tracer._writer.write before any spans finish

# --- Your ddtrace-instrumented application code (unchanged) ---

with ddtrace.tracer.trace("web.request", service="my-agent") as span:
    # ... do work, call OpenAI, query databases ...
    pass

# --- Inspect violations ---

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

`adapter.setup()` only modifies the writer on the provided tracer instance.
It does not affect other tracers or the global ddtrace state beyond wrapping
the writer method.

---

## Quickstart (custom tracer)

```python
from ddtrace import Tracer
from agentcop import Sentinel
from agentcop.adapters.datadog import DatadogSentinelAdapter

tracer = Tracer()   # your own tracer instance
adapter = DatadogSentinelAdapter(run_id="run-001")
adapter.setup(tracer)

with tracer.trace("openai.request", service="my-agent", resource="chat.completions") as span:
    span.set_tag("component", "openai")
    span.set_tag("ai.model.name", "gpt-4o-mini")
    # ... call OpenAI ...

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

---

## Writing detectors for Datadog spans

### Detect an LLM span that failed

```python
from typing import Optional
from agentcop import SentinelEvent, ViolationRecord

def detect_llm_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "llm_span_error":
        return None
    return ViolationRecord(
        violation_type="llm_call_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "span_name": event.attributes["span_name"],
            "model": event.attributes.get("model", "unknown"),
            "error": event.attributes["error_message"],
        },
    )
```

### Detect a rate-limited LLM call

```python
RATE_LIMIT_SIGNALS = {"rate limit", "429", "quota exceeded", "too many requests"}

def detect_rate_limit(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "llm_span_error":
        return None
    msg = event.attributes.get("error_message", "").lower()
    if not any(sig in msg for sig in RATE_LIMIT_SIGNALS):
        return None
    return ViolationRecord(
        violation_type="llm_rate_limited",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "model": event.attributes.get("model"),
            "provider": event.attributes.get("provider"),
            "error": event.attributes.get("error_message"),
        },
    )
```

### Detect an HTTP 5xx response

```python
def detect_http_5xx(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type not in ("http_span_finished", "http_span_error"):
        return None
    status = str(event.attributes.get("http_status_code", ""))
    if not status.startswith("5"):
        return None
    return ViolationRecord(
        violation_type="http_5xx_response",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "span_name": event.attributes["span_name"],
            "url": event.attributes.get("http_url"),
            "status_code": status,
        },
    )
```

### Detect a database span that errored

```python
def detect_db_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "db_span_error":
        return None
    return ViolationRecord(
        violation_type="db_query_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "span_name": event.attributes["span_name"],
            "component": event.attributes.get("component"),
            "error": event.attributes["error_message"],
        },
    )
```

### Detect high LLM token usage

```python
TOKEN_BUDGET = 4_000

def detect_token_budget_exceeded(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "llm_span_finished":
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
    detect_llm_failure,
    detect_rate_limit,
    detect_http_5xx,
    detect_db_failure,
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
adapter = DatadogSentinelAdapter(run_id=str(uuid.uuid4()))
```

When `run_id` is `None`, the Datadog trace ID (the 64-bit integer from
`span.trace_id`, formatted as a 16-char hex string) is used instead. Either
way, the Datadog trace ID is also stored in `event.attributes["dd_trace_id"]`
so you can correlate violations back to the exact trace in the Datadog UI.

---

## Translating events manually

`to_sentinel_event(raw)` accepts a plain dict and is useful for offline
processing, replaying logged events, or testing detectors without a live
tracer:

```python
event = adapter.to_sentinel_event({
    "type": "llm_span_error",
    "span_name": "openai.request",
    "service": "my-agent",
    "component": "openai",
    "model": "gpt-4o-mini",
    "provider": "openai",
    "error_message": "rate limit exceeded",
    "dd_trace_id": "abcdef1234567890",
})
```

Required key: `"type"`. All other keys are optional — missing values default
to `"unknown"`, `{}`, or empty string depending on the field.

---

## Multiple pipeline runs

```python
all_violations = []

for i, query in enumerate(queries):
    adapter = DatadogSentinelAdapter(run_id=f"query-{i}")
    adapter.setup()

    with ddtrace.tracer.trace("pipeline", service="my-agent"):
        # ... run pipeline ...
        pass

    sentinel = Sentinel(detectors=[detect_llm_failure, detect_http_5xx])
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
adapter = DatadogSentinelAdapter(run_id="ci-eval")
adapter.setup()

with ddtrace.tracer.trace("eval-run", service="my-agent"):
    # ... your pipeline ...
    pass

sentinel = Sentinel(detectors=[
    detect_llm_failure,
    detect_http_5xx,
    detect_db_failure,
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

|                  | LangGraph              | LangSmith                         | Langfuse                              | Datadog                                 |
|------------------|------------------------|-----------------------------------|---------------------------------------|-----------------------------------------|
| Event delivery   | Pull (debug stream)    | Push (client method wrapping)     | Push (OTel SpanProcessor)             | Push (TraceWriter wrapping)             |
| Primary API      | `iter_events(stream)`  | `setup(client)` + `flush_into()`  | `setup(langfuse)` + `flush_into()`    | `setup([tracer])` + `flush_into()`      |
| Correlation ID   | LangGraph task UUID    | `run_id` or LangSmith trace UUID  | `run_id` or Langfuse trace ID         | `run_id` or Datadog trace ID (hex)      |
| Coverage         | Graph nodes/edges      | All run types (chain/llm/tool/…)  | All Langfuse observation types        | All ddtrace spans (any instrumentation) |
| Hook mechanism   | debug stream           | create_run / update_run wrapping  | OTel TracerProvider                   | TraceWriter.write() wrapping            |
| Framework scope  | LangGraph only         | LangSmith only                    | Langfuse only                         | Any ddtrace-instrumented library        |

---

## Attributes reference

### All events — common attributes

| Key               | Type   | Description                                                |
|-------------------|--------|------------------------------------------------------------|
| `span_name`       | `str`  | ddtrace operation name (e.g., `openai.request`)            |
| `resource`        | `str`  | Resource being accessed (e.g., endpoint, query)            |
| `service`         | `str`  | Service name                                               |
| `component`       | `str`  | Integration component tag (e.g., `openai`, `requests`)     |
| `span_kind`       | `str`  | OTel span kind: `client`, `server`, `internal`, etc.       |
| `dd_trace_id`     | `str`  | Datadog trace ID as 16-char hex string                     |
| `dd_span_id`      | `str`  | Datadog span ID as 16-char hex string                      |
| `dd_parent_id`    | `str`  | Parent span ID as 16-char hex string, or empty for roots   |
| `error`           | `bool` | `True` if `span.error` is non-zero                         |
| `error_type`      | `str`  | Exception type from `error.type` tag                       |
| `error_message`   | `str`  | Error description from `error.message` tag                 |
| `duration_ns`     | `int`  | Span duration in nanoseconds                               |

### `http_span_finished` / `http_span_error` — additional attributes

| Key                 | Type  | Description                              |
|---------------------|-------|------------------------------------------|
| `http_url`          | `str` | Request URL from `http.url` tag          |
| `http_status_code`  | `str` | HTTP status code from `http.status_code` |
| `http_method`       | `str` | HTTP method from `http.method` tag       |

### `llm_span_finished` / `llm_span_error` — additional attributes

| Key        | Type   | Description                                                           |
|------------|--------|-----------------------------------------------------------------------|
| `model`    | `str`  | Model name from `ai.model.name`, `openai.request.model`, or langchain tag |
| `provider` | `str`  | Provider name (same as `component`)                                   |
| `usage`    | `dict` | Token counts: `{"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}` |

Token counts are read from `llm.usage.*` metrics (standard) with fallback to
`openai.response.usage.*` metrics for OpenAI spans.

---

## API reference

### `DatadogSentinelAdapter(run_id=None)`

**Parameters**

- `run_id` (`str | None`) — Optional session identifier used as `trace_id` on
  every translated event. When `None`, the Datadog trace ID from the span is
  used instead.

**Methods**

- `setup(tracer=None)` — Wrap `_writer.write` on a `ddtrace.Tracer`. Call
  once before running any traced code. When `tracer` is `None`, the global
  `ddtrace.tracer` singleton is used (reads `DD_*` env vars for Agent
  endpoint). Raises `RuntimeError` if no writer is found on the tracer.

- `to_sentinel_event(raw: dict) -> SentinelEvent` — Translate one normalized
  span dict. Dispatches on `raw["type"]`. Unknown types map to
  `unknown_datadog_event`. Never raises; missing keys fall back to safe
  defaults.

- `drain() -> list[SentinelEvent]` — Return and clear all buffered events.
  Thread-safe.

- `flush_into(sentinel: Sentinel) -> None` — Call `sentinel.ingest(self.drain())`.
  Ingest all buffered events and clear the buffer in one step.

**Class attribute**

- `source_system = "datadog"` — appears on every translated `SentinelEvent`.

---

## Runtime security

`DatadogSentinelAdapter` supports the full agentcop runtime security stack via four
optional constructor parameters. All default to `None` — existing code requires no changes.

### Constructor params

```python
DatadogSentinelAdapter(
    run_id="run-001",
    gate=None,        # ExecutionGate
    permissions=None, # ToolPermissionLayer
    sandbox=None,     # AgentSandbox
    approvals=None,   # ApprovalBoundary
    identity=None,    # AgentIdentity
)
```

### What gets intercepted (observability adapter)

Datadog is an observability adapter.  When gate/permissions are provided, the gate is
checked inside `_intercepted_write()` for **LLM-type spans** (component tag in
`_LLM_COMPONENTS`: openai, anthropic, langchain, etc.).  Gate decisions are logged as
SentinelEvents; the `PermissionError` is caught so ddtrace export to the Datadog Agent
is never disrupted.

### Example

```python
import ddtrace
from agentcop.adapters.datadog import DatadogSentinelAdapter
from agentcop.gate import ExecutionGate, ConditionalPolicy
from agentcop.permissions import ToolPermissionLayer, NetworkPermission

gate = ExecutionGate()
gate.register_policy("openai.request", ConditionalPolicy(
    allow_if=lambda args: True,
    deny_reason="openai calls require explicit permission",
))

permissions = ToolPermissionLayer()
permissions.declare("default", [NetworkPermission(domains=["api.openai.com"])])

adapter = DatadogSentinelAdapter(
    run_id="run-001",
    gate=gate,
    permissions=permissions,
)
adapter.setup(ddtrace.tracer)

# ... run your ddtrace-instrumented application ...

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```
