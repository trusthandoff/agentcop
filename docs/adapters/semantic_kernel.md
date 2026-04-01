# Semantic Kernel adapter

Plug agentcop into any Semantic Kernel 1.x application with two lines of
setup. The adapter registers three async filters on a `Kernel` instance and
buffers translated `SentinelEvent` objects for every function invocation,
prompt render, and LLM-initiated tool call.

Semantic Kernel uses a filter middleware chain for observability: every kernel
call flows through `FUNCTION_INVOCATION`, `PROMPT_RENDERING`, and
`AUTO_FUNCTION_INVOCATION` filters before and after execution. The adapter adds
one filter per type, forwards the chain normally, and buffers events on both
sides of `await next(context)`.

---

## Installation

```bash
pip install agentcop[semantic-kernel]
```

---

## How it works

```
semantic_kernel.Kernel.invoke(...)
      │
      │  FUNCTION_INVOCATION filter  (pre: function_invoking)
      │    PROMPT_RENDERING filter   (pre: prompt_rendering)
      │      ... actual LLM call ...
      │    PROMPT_RENDERING filter   (post: prompt_rendered)
      │  FUNCTION_INVOCATION filter  (post: function_invoked / function_error)
      │
      │  AUTO_FUNCTION_INVOCATION filter  (pre: auto_function_invoking)
      │    ... tool function executed ...
      │  AUTO_FUNCTION_INVOCATION filter  (post: auto_function_invoked / auto_function_error)
      ▼
SemanticKernelSentinelAdapter._buffer  (thread-safe list)
      │
      ▼
adapter.flush_into(sentinel)
      │
      ▼
sentinel.detect_violations() → ViolationRecord list
```

The adapter translates 8 event types across three categories (plus unknown):

| Category       | type                      | `event_type`              | `severity` |
|----------------|---------------------------|---------------------------|------------|
| Function       | `function_invoking`       | `function_invoking`       | INFO       |
| Function       | `function_invoked`        | `function_invoked`        | INFO       |
| Function       | `function_error`          | `function_error`          | ERROR      |
| Prompt         | `prompt_rendering`        | `prompt_rendering`        | INFO       |
| Prompt         | `prompt_rendered`         | `prompt_rendered`         | INFO       |
| Auto-function  | `auto_function_invoking`  | `auto_function_invoking`  | INFO       |
| Auto-function  | `auto_function_invoked`   | `auto_function_invoked`   | INFO       |
| Auto-function  | `auto_function_error`     | `auto_function_error`     | ERROR      |

---

## Quickstart

```python
import asyncio
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
from agentcop import Sentinel
from agentcop.adapters.semantic_kernel import SemanticKernelSentinelAdapter

# --- Your kernel (unchanged) ---

kernel = Kernel()
kernel.add_service(OpenAIChatCompletion(service_id="chat", ai_model_id="gpt-4o-mini"))
# ... add plugins ...

# --- Audit layer ---

adapter = SemanticKernelSentinelAdapter(run_id="run-001")
adapter.setup(kernel)   # registers three filters on the kernel instance

async def main():
    result = await kernel.invoke("MyPlugin", "MyFunction", input="hello")

    sentinel = Sentinel()
    adapter.flush_into(sentinel)
    violations = sentinel.detect_violations()
    sentinel.report()

asyncio.run(main())
```

`adapter.setup(kernel)` only adds filters. The kernel is otherwise unchanged.
Filters forward every call to the next handler in the chain — no intercepted
execution, no side effects.

---

## Quickstart (prompt function)

```python
import asyncio
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
from semantic_kernel.prompt_template import PromptTemplateConfig
from agentcop import Sentinel
from agentcop.adapters.semantic_kernel import SemanticKernelSentinelAdapter

kernel = Kernel()
kernel.add_service(OpenAIChatCompletion(service_id="chat", ai_model_id="gpt-4o-mini"))

summarize_fn = kernel.add_function(
    plugin_name="TextPlugin",
    function_name="Summarize",
    prompt="Summarize the following in one sentence: {{$input}}",
)

adapter = SemanticKernelSentinelAdapter(run_id="summary-run")
adapter.setup(kernel)

async def main():
    result = await kernel.invoke(summarize_fn, input="Semantic Kernel is a ...")

    sentinel = Sentinel()
    adapter.flush_into(sentinel)
    violations = sentinel.detect_violations()
    sentinel.report()

asyncio.run(main())
```

---

## Writing detectors for Semantic Kernel events

### Detect a function that failed

```python
from typing import Optional
from agentcop import SentinelEvent, ViolationRecord

def detect_function_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "function_error":
        return None
    return ViolationRecord(
        violation_type="function_execution_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "plugin_name": event.attributes["plugin_name"],
            "function_name": event.attributes["function_name"],
            "error": event.attributes["error"],
        },
    )
```

### Detect a restricted plugin being called

```python
RESTRICTED_PLUGINS = {"ExecPlugin", "ShellPlugin", "FileWritePlugin"}

def detect_restricted_plugin(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type not in ("function_invoking", "auto_function_invoking"):
        return None
    plugin_name = event.attributes.get("plugin_name", "")
    if plugin_name not in RESTRICTED_PLUGINS:
        return None
    return ViolationRecord(
        violation_type="restricted_plugin_called",
        severity="CRITICAL",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "plugin_name": plugin_name,
            "function_name": event.attributes.get("function_name"),
        },
    )
```

### Detect a prompt injection attempt

```python
INJECTION_SIGNALS = {
    "ignore previous instructions",
    "disregard your",
    "you are now",
    "new system prompt",
}

def detect_prompt_injection(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "prompt_rendered":
        return None
    prompt = event.attributes.get("rendered_prompt", "").lower()
    for signal in INJECTION_SIGNALS:
        if signal in prompt:
            return ViolationRecord(
                violation_type="prompt_injection_detected",
                severity="CRITICAL",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={
                    "signal": signal,
                    "plugin_name": event.attributes["plugin_name"],
                    "function_name": event.attributes["function_name"],
                },
            )
    return None
```

### Detect an LLM-initiated tool call that errored

```python
def detect_tool_call_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "auto_function_error":
        return None
    return ViolationRecord(
        violation_type="tool_call_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "plugin_name": event.attributes["plugin_name"],
            "function_name": event.attributes["function_name"],
            "error": event.attributes["error"],
        },
    )
```

### Detect unexpected loop termination

```python
def detect_unexpected_terminate(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "auto_function_invoked":
        return None
    if not event.attributes.get("terminate"):
        return None
    # Flag early termination on the first request round
    if event.attributes.get("request_sequence_index", 0) == 0:
        return ViolationRecord(
            violation_type="early_tool_loop_termination",
            severity="WARN",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={
                "plugin_name": event.attributes["plugin_name"],
                "function_name": event.attributes["function_name"],
            },
        )
    return None
```

### Register detectors

```python
sentinel = Sentinel(detectors=[
    detect_function_failure,
    detect_restricted_plugin,
    detect_prompt_injection,
    detect_tool_call_failure,
    detect_unexpected_terminate,
])
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```

---

## `run_id` and trace correlation

Pass a `run_id` to correlate all events from one kernel session:

```python
import uuid
run_id = str(uuid.uuid4())
adapter = SemanticKernelSentinelAdapter(run_id=run_id)
```

Every `SentinelEvent` produced during the session carries `trace_id=run_id`.
When you inspect a `ViolationRecord`, `violation.trace_id` traces back to the
same session.

---

## Translating events manually

`to_sentinel_event(raw)` accepts a plain dict and is useful for offline
processing, replaying logged events, or testing detectors without a live kernel:

```python
event = adapter.to_sentinel_event({
    "type": "function_error",
    "plugin_name": "SearchPlugin",
    "function_name": "Search",
    "error": "connection refused",
})
```

Required key: `"type"`. All other keys are optional — missing values default
to `"unknown"`, `False`, or empty string depending on the field.

---

## Multiple kernel sessions

```python
all_violations = []

for i, query in enumerate(queries):
    adapter = SemanticKernelSentinelAdapter(run_id=f"query-{i}")
    adapter.setup(kernel)

    async def run():
        return await kernel.invoke("QueryPlugin", "Run", input=query)

    asyncio.run(run())

    sentinel = Sentinel(detectors=[detect_function_failure, detect_tool_call_failure])
    adapter.flush_into(sentinel)
    all_violations.extend(sentinel.detect_violations())

if all_violations:
    print(f"{len(all_violations)} violation(s) across {len(queries)} sessions")
    for v in all_violations:
        print(f"  [{v.severity}] {v.violation_type} trace={v.trace_id}")
```

---

## Assertion-style auditing in CI

```python
adapter = SemanticKernelSentinelAdapter(run_id="ci-eval")
adapter.setup(kernel)
asyncio.run(kernel.invoke("EvalPlugin", "Run", input="..."))

sentinel = Sentinel(detectors=[
    detect_function_failure,
    detect_restricted_plugin,
    detect_prompt_injection,
])
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()

if violations:
    for v in violations:
        print(f"[{v.severity}] {v.violation_type}: {v.detail}")
    raise RuntimeError(f"Kernel run failed audit — {len(violations)} violation(s)")
```

---

## Differences from other adapters

|                  | LangGraph              | CrewAI                     | LlamaIndex                  | Haystack                    | Semantic Kernel                  |
|------------------|------------------------|----------------------------|-----------------------------|------------------------------|----------------------------------|
| Event delivery   | Pull (debug stream)    | Push (event bus callbacks) | Push (dispatcher callbacks) | Push (ProxyTracer wrapping)  | Push (async filter middleware)   |
| Primary API      | `iter_events(stream)`  | `setup()` + `flush_into()` | `setup()` + `flush_into()`  | `setup()` + `flush_into()`   | `setup(kernel)` + `flush_into()` |
| Correlation ID   | LangGraph task UUID    | `run_id` you provide       | `run_id` you provide        | `run_id` you provide         | `run_id` you provide             |
| Coverage         | Graph nodes/edges      | Crew/agent/task/tool       | Query/retrieval/LLM/agent   | Pipeline/component/LLM/ret   | Function/prompt/auto-function    |
| Async required   | No                     | No                         | No                          | No                           | Yes (kernel is async)            |

---

## Attributes reference

### Function events (`function_invoking`, `function_invoked`, `function_error`)

| Key             | Type   | Present in                           | Description                           |
|-----------------|--------|--------------------------------------|---------------------------------------|
| `plugin_name`   | `str`  | all function events                  | SK plugin name                        |
| `function_name` | `str`  | all function events                  | SK function name                      |
| `is_prompt`     | `bool` | all function events                  | Whether this is a prompt function     |
| `is_streaming`  | `bool` | `invoking`, `invoked`                | Whether the call is streaming         |
| `arguments`     | `dict` | `invoking` only                      | KernelArguments as string dict        |
| `result`        | `str`  | `invoked` only                       | str(FunctionResult) ≤500 chars        |
| `metadata`      | `dict` | `invoked` only                       | FunctionResult.metadata as string map |
| `error`         | `str`  | `error` only                         | Exception message                     |

### Prompt events (`prompt_rendering`, `prompt_rendered`)

| Key               | Type   | Present in              | Description                           |
|-------------------|--------|-------------------------|---------------------------------------|
| `plugin_name`     | `str`  | all prompt events       | SK plugin name                        |
| `function_name`   | `str`  | all prompt events       | SK function name                      |
| `is_streaming`    | `bool` | `rendering` only        | Whether the call is streaming         |
| `rendered_prompt` | `str`  | `rendered` only         | Final rendered prompt (≤500 chars)    |

### Auto-function events (`auto_function_invoking`, `auto_function_invoked`, `auto_function_error`)

| Key                      | Type   | Present in                              | Description                           |
|--------------------------|--------|-----------------------------------------|---------------------------------------|
| `plugin_name`            | `str`  | all auto-function events                | SK plugin name                        |
| `function_name`          | `str`  | all auto-function events                | SK function name                      |
| `request_sequence_index` | `int`  | `invoking`, `invoked`                   | Which LLM request round this is       |
| `function_sequence_index`| `int`  | `invoking`, `invoked`                   | Position in parallel tool-call batch  |
| `function_count`         | `int`  | `invoking` only                         | Total functions in current batch      |
| `is_streaming`           | `bool` | `invoking` only                         | Whether the call is streaming         |
| `result`                 | `str`  | `invoked` only                          | str(function_result) ≤500 chars       |
| `terminate`              | `bool` | `invoked` only                          | Whether the tool loop was terminated  |
| `error`                  | `str`  | `error` only                            | Exception message                     |

---

## API reference

### `SemanticKernelSentinelAdapter(run_id=None)`

**Parameters**

- `run_id` (`str | None`) — Optional session identifier used as `trace_id`
  on every translated event. Recommended: pass a UUID per kernel session.

**Methods**

- `setup(kernel)` — Register three async filters on a `semantic_kernel.Kernel`
  instance via `kernel.add_filter()`. Call once before invoking any functions.

- `to_sentinel_event(raw: dict) -> SentinelEvent` — Translate one normalized
  event dict. Dispatches on `raw["type"]`. Unknown types map to
  `unknown_sk_event`. Never raises; missing keys fall back to safe defaults.

- `drain() -> list[SentinelEvent]` — Return and clear all buffered events.
  Thread-safe.

- `flush_into(sentinel: Sentinel) -> None` — Call `sentinel.ingest(self.drain())`.
  Ingest all buffered events and clear the buffer in one step.

**Class attribute**

- `source_system = "semantic_kernel"` — appears on every translated
  `SentinelEvent`.
