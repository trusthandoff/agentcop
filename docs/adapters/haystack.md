# Haystack adapter

Plug agentcop into any Haystack 2.x pipeline with two lines of setup. The
adapter hooks into Haystack's `ProxyTracer` by replacing `provided_tracer` with
a thin wrapper that intercepts every pipeline and component span, translates
them into `SentinelEvent` objects, and buffers them for inspection.

Haystack is push-based via its tracing API: spans fire through
`haystack.tracing.tracer` during `pipeline.run()`. The adapter wraps whatever
tracer is already registered, forwards all calls through, and additionally
buffers translated events. Call `flush_into(sentinel)` after the pipeline run.

---

## Installation

```bash
pip install agentcop[haystack]
```

---

## How it works

```
haystack.tracing.tracer  (ProxyTracer, singleton)
      │
      │  ProxyTracer.trace("haystack.pipeline.run", ...)
      │  ProxyTracer.trace("haystack.component.run", ...)
      │    span.set_tag("haystack.component.output.replies", [...])
      │    ...
      ▼
HaystackSentinelAdapter._WrappingTracer  (installed as provided_tracer)
      │  forwards all calls to the previous provided_tracer (OTel, etc.)
      │  additionally emits start / end / error SentinelEvents
      ▼
adapter._buffer  (thread-safe list)
      │
      ▼
adapter.flush_into(sentinel)
      │
      ▼
sentinel.detect_violations() → ViolationRecord list
```

The adapter translates 13 event types across five categories:

| Category  | type                      | `event_type`              | `severity` |
|-----------|---------------------------|---------------------------|------------|
| Pipeline  | `pipeline_started`        | `pipeline_started`        | INFO       |
| Pipeline  | `pipeline_finished`       | `pipeline_finished`       | INFO       |
| Pipeline  | `pipeline_error`          | `pipeline_error`          | ERROR      |
| Component | `component_started`       | `component_started`       | INFO       |
| Component | `component_finished`      | `component_finished`      | INFO       |
| Component | `component_error`         | `component_error`         | ERROR      |
| LLM       | `llm_run_started`         | `llm_run_started`         | INFO       |
| LLM       | `llm_run_finished`        | `llm_run_finished`        | INFO       |
| LLM       | `llm_run_error`           | `llm_run_error`           | ERROR      |
| Retriever | `retriever_run_started`   | `retriever_run_started`   | INFO       |
| Retriever | `retriever_run_finished`  | `retriever_run_finished`  | INFO       |
| Embedder  | `embedder_run_started`    | `embedder_run_started`    | INFO       |
| Embedder  | `embedder_run_finished`   | `embedder_run_finished`   | INFO       |

Component types are auto-classified: any class with `Generator` in the name →
LLM, `Retriever` → retriever, `Embedder` → embedder, anything else → generic
component.

---

## Quickstart

```python
from haystack import Pipeline
from haystack.components.generators import OpenAIGenerator
from haystack.components.builders import PromptBuilder
from agentcop import Sentinel
from agentcop.adapters.haystack import HaystackSentinelAdapter

# --- Your pipeline (unchanged) ---

pipe = Pipeline()
pipe.add_component("prompt_builder", PromptBuilder(template="Answer: {{query}}"))
pipe.add_component("llm", OpenAIGenerator(model="gpt-4o-mini"))
pipe.connect("prompt_builder.prompt", "llm.prompt")

# --- Audit layer ---

adapter = HaystackSentinelAdapter(run_id="run-001")
adapter.setup()          # install wrapping tracer before the first run

result = pipe.run({"prompt_builder": {"query": "What is Haystack?"}})

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

`adapter.setup()` replaces `haystack.tracing.tracer.provided_tracer`. Any
previously registered tracer (OTel, Datadog) is preserved and all span calls
are forwarded to it.

---

## Quickstart (RAG pipeline)

```python
from haystack import Pipeline
from haystack.components.retrievers.in_memory import InMemoryBM25Retriever
from haystack.components.generators import OpenAIGenerator
from haystack.document_stores.in_memory import InMemoryDocumentStore
from agentcop import Sentinel
from agentcop.adapters.haystack import HaystackSentinelAdapter

document_store = InMemoryDocumentStore()
# ... populate document_store ...

pipe = Pipeline()
pipe.add_component("retriever", InMemoryBM25Retriever(document_store=document_store))
pipe.add_component("llm", OpenAIGenerator(model="gpt-4o-mini"))
pipe.connect("retriever.documents", "llm.documents")

adapter = HaystackSentinelAdapter(run_id="rag-run-001")
adapter.setup()

result = pipe.run({"retriever": {"query": "What is RAG?"}})

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

---

## Writing detectors for Haystack events

### Detect an LLM that failed

```python
from typing import Optional
from agentcop import SentinelEvent, ViolationRecord

def detect_llm_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "llm_run_error":
        return None
    return ViolationRecord(
        violation_type="llm_call_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "component_name": event.attributes["component_name"],
            "model": event.attributes["model"],
            "error": event.attributes["error"],
        },
    )
```

### Detect an LLM rate limit

```python
RATE_LIMIT_SIGNALS = {"429", "rate limit", "quota exceeded", "too many requests"}

def detect_llm_rate_limit(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "llm_run_error":
        return None
    error = event.attributes.get("error", "").lower()
    if not any(sig in error for sig in RATE_LIMIT_SIGNALS):
        return None
    return ViolationRecord(
        violation_type="llm_rate_limited",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "model": event.attributes["model"],
            "error": event.attributes["error"],
        },
    )
```

### Detect empty retrieval

```python
def detect_empty_retrieval(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "retriever_run_finished":
        return None
    if event.attributes.get("num_documents", -1) != 0:
        return None
    return ViolationRecord(
        violation_type="empty_retrieval",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={"component_name": event.attributes.get("component_name")},
    )
```

### Detect a pipeline failure

```python
def detect_pipeline_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "pipeline_error":
        return None
    return ViolationRecord(
        violation_type="pipeline_execution_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "pipeline_name": event.attributes["pipeline_name"],
            "error": event.attributes["error"],
        },
    )
```

### Detect a slow component (requires duration tracking)

```python
import time
from agentcop import SentinelEvent, ViolationRecord

_component_start_times: dict = {}

def track_component_duration(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type == "component_started":
        _component_start_times[event.trace_id] = time.time()
        return None
    if event.event_type == "component_finished":
        start = _component_start_times.pop(event.trace_id, None)
        if start is not None and (time.time() - start) > 30:
            return ViolationRecord(
                violation_type="slow_component",
                severity="WARN",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={"component_name": event.attributes["component_name"]},
            )
    return None
```

### Register detectors

```python
sentinel = Sentinel(detectors=[
    detect_llm_failure,
    detect_llm_rate_limit,
    detect_empty_retrieval,
    detect_pipeline_failure,
])
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```

---

## `run_id` and trace correlation

Pass a `run_id` to correlate all events from one pipeline execution:

```python
import uuid
run_id = str(uuid.uuid4())
adapter = HaystackSentinelAdapter(run_id=run_id)
```

Every `SentinelEvent` produced during the run carries `trace_id=run_id`.
When you inspect a `ViolationRecord`, `violation.trace_id` traces back to the
same run.

---

## Translating events manually

`to_sentinel_event(raw)` accepts a plain dict and is useful for offline
processing, replaying logged events, or testing detectors without running a
real pipeline:

```python
event = adapter.to_sentinel_event({
    "type": "llm_run_error",
    "component_name": "llm",
    "model": "gpt-4o-mini",
    "error": "rate limit exceeded",
})
```

Required key: `"type"`. All other keys are optional — missing values default
to `"unknown"`, `0`, or empty string depending on the field.

---

## Composing with existing tracers

`setup()` captures whatever `provided_tracer` is already registered before
replacing it. All span calls are forwarded, so OTel and Datadog integrations
continue to work alongside agentcop:

```python
from haystack.tracing import OpenTelemetryTracer
import haystack.tracing

haystack.tracing.tracer.provided_tracer = OpenTelemetryTracer(tracer)

# Install agentcop on top — OpenTelemetryTracer calls are preserved
adapter = HaystackSentinelAdapter(run_id="run-001")
adapter.setup()
```

---

## Multiple pipeline runs in sequence

```python
all_violations = []

for i, query in enumerate(queries):
    adapter = HaystackSentinelAdapter(run_id=f"query-{i}")
    adapter.setup()

    result = pipe.run({"retriever": {"query": query}})

    sentinel = Sentinel(detectors=[detect_llm_failure, detect_empty_retrieval])
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
adapter = HaystackSentinelAdapter(run_id="ci-eval")
adapter.setup()
result = pipe.run({"prompt_builder": {"query": "..."}})

sentinel = Sentinel(detectors=[
    detect_llm_failure,
    detect_pipeline_failure,
    detect_empty_retrieval,
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

|                  | LangGraph              | CrewAI                     | AutoGen                   | LlamaIndex                  | Haystack                    |
|------------------|------------------------|----------------------------|---------------------------|-----------------------------|------------------------------|
| Event delivery   | Pull (debug stream)    | Push (event bus callbacks) | Pull (chat history)       | Push (dispatcher callbacks) | Push (ProxyTracer wrapping)  |
| Primary API      | `iter_events(stream)`  | `setup()` + `flush_into()` | `iter_messages(history)`  | `setup()` + `flush_into()`  | `setup()` + `flush_into()`   |
| Correlation ID   | LangGraph task UUID    | `run_id` you provide       | `run_id` you provide      | `run_id` you provide        | `run_id` you provide         |
| Coverage         | Graph nodes/edges      | Crew/agent/task/tool       | Chat messages + functions | Query/retrieval/LLM/agent   | Pipeline/component/LLM/retriever/embedder |

---

## Attributes reference

### Pipeline events (`pipeline_*`)

| Key             | Type        | Present in         | Description                          |
|-----------------|-------------|--------------------|--------------------------------------|
| `pipeline_name` | `str`       | all pipeline events| Pipeline name from span tags         |
| `output_keys`   | `list[str]` | `finished` only    | Sorted keys of pipeline output data  |
| `error`         | `str`       | `error` only       | Exception message                    |

### Component events (`component_*`)

| Key              | Type        | Present in          | Description                          |
|------------------|-------------|---------------------|--------------------------------------|
| `component_name` | `str`       | all component events| Component name from span tags        |
| `component_type` | `str`       | all component events| Short class name of the component    |
| `output_keys`    | `list[str]` | `finished` only     | Sorted keys of component output      |
| `error`          | `str`       | `error` only        | Exception message                    |

### LLM events (`llm_run_*`)

| Key              | Type  | Present in       | Description                          |
|------------------|-------|------------------|--------------------------------------|
| `component_name` | `str` | all LLM events   | Component name                       |
| `model`          | `str` | all LLM events   | Model identifier (from span tags)    |
| `reply`          | `str` | `finished` only  | First reply (≤500 chars)             |
| `error`          | `str` | `error` only     | Exception message                    |

### Retriever events (`retriever_run_*`)

| Key              | Type  | Present in          | Description                          |
|------------------|-------|---------------------|--------------------------------------|
| `component_name` | `str` | all retriever events| Component name                       |
| `query`          | `str` | `started` only      | Query string (≤500 chars)            |
| `num_documents`  | `int` | `finished` only     | Number of documents returned         |

### Embedder events (`embedder_run_*`)

| Key              | Type  | Present in          | Description                          |
|------------------|-------|---------------------|--------------------------------------|
| `component_name` | `str` | all embedder events | Component name                       |
| `model`          | `str` | all embedder events | Embedding model identifier           |

---

## API reference

### `HaystackSentinelAdapter(run_id=None)`

**Parameters**

- `run_id` (`str | None`) — Optional run identifier used as `trace_id` on
  every translated event. Recommended: pass a UUID per pipeline execution.

**Methods**

- `setup(proxy_tracer=None)` — Install a wrapping tracer into Haystack's
  `ProxyTracer`. Captures the current `provided_tracer` (if any) and forwards
  all calls to it. Call once before running any pipelines. Pass a mock
  `proxy_tracer` for testing.

- `to_sentinel_event(raw: dict) -> SentinelEvent` — Translate one normalized
  event dict. Dispatches on `raw["type"]`. Unknown types map to
  `unknown_haystack_event`. Never raises; missing keys fall back to safe
  defaults.

- `drain() -> list[SentinelEvent]` — Return and clear all buffered events.
  Thread-safe.

- `flush_into(sentinel: Sentinel) -> None` — Call `sentinel.ingest(self.drain())`.
  Ingest all buffered events and clear the buffer in one step.

**Class attribute**

- `source_system = "haystack"` — appears on every translated `SentinelEvent`.
