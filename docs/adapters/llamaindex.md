# LlamaIndex adapter

Plug agentcop into any LlamaIndex query pipeline or agent with two lines of
setup. The adapter hooks into LlamaIndex's instrumentation dispatcher, buffers
translated events during execution, and lets you drain them into a `Sentinel`
instance for violation detection.

LlamaIndex is push-based: events fire through a singleton dispatcher during
query and agent execution. The adapter registers a handler before you run
queries, then you call `flush_into(sentinel)` after.

---

## Installation

```bash
pip install agentcop[llamaindex]
```

---

## How it works

```
llama_index dispatcher  (singleton, fires during query/agent execution)
      │
      │  QueryStartEvent(query=...)         → handler
      │  RetrievalEndEvent(nodes=...)       → handler
      │  LLMPredictEndEvent(output=...)     → handler
      │  AgentToolCallEvent(tool=...)       → handler
      │  ...
      ▼
LlamaIndexSentinelAdapter._buffer  (thread-safe list)
      │
      ▼
adapter.flush_into(sentinel)
      │
      ▼
sentinel.detect_violations() → ViolationRecord list
```

The adapter translates 14 event types across five categories:

| Category  | type                   | `event_type`            | `severity` |
|-----------|------------------------|-------------------------|------------|
| Query     | `query_started`        | `query_started`         | INFO       |
| Query     | `query_finished`       | `query_finished`        | INFO       |
| Query     | `query_error`          | `query_error`           | ERROR      |
| Retrieval | `retrieval_started`    | `retrieval_started`     | INFO       |
| Retrieval | `retrieval_finished`   | `retrieval_finished`    | INFO       |
| Retrieval | `retrieval_error`      | `retrieval_error`       | ERROR      |
| LLM       | `llm_predict_started`  | `llm_predict_started`   | INFO       |
| LLM       | `llm_predict_finished` | `llm_predict_finished`  | INFO       |
| LLM       | `llm_predict_error`    | `llm_predict_error`     | ERROR      |
| Agent     | `agent_step_started`   | `agent_step_started`    | INFO       |
| Agent     | `agent_step_finished`  | `agent_step_finished`   | INFO       |
| Agent     | `agent_tool_call`      | `agent_tool_call`       | INFO       |
| Embedding | `embedding_started`    | `embedding_started`     | INFO       |
| Embedding | `embedding_finished`   | `embedding_finished`    | INFO       |

---

## Quickstart

```python
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader
from llama_index.core.agent import ReActAgent
from llama_index.core.tools import FunctionTool
from agentcop import Sentinel
from agentcop.adapters.llamaindex import LlamaIndexSentinelAdapter

# --- Your index and query engine (unchanged) ---

documents = SimpleDirectoryReader("data").load_data()
index = VectorStoreIndex.from_documents(documents)
query_engine = index.as_query_engine()

# --- Audit layer ---

adapter = LlamaIndexSentinelAdapter(run_id="run-001")
adapter.setup()          # register with the LlamaIndex dispatcher before querying

response = query_engine.query("What is retrieval-augmented generation?")

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

That's all. No changes to the index, query engine, or agents.

---

## Quickstart (agent)

```python
from llama_index.core.agent import ReActAgent
from llama_index.core.tools import FunctionTool
from agentcop import Sentinel
from agentcop.adapters.llamaindex import LlamaIndexSentinelAdapter

def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b

tool = FunctionTool.from_defaults(fn=multiply)
agent = ReActAgent.from_tools([tool], verbose=True)

adapter = LlamaIndexSentinelAdapter(run_id="run-001")
adapter.setup()

response = agent.chat("What is 12 times 37?")

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

---

## Writing detectors for LlamaIndex events

### Detect an LLM call that failed

```python
from typing import Optional
from agentcop import SentinelEvent, ViolationRecord

def detect_llm_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "llm_predict_error":
        return None
    return ViolationRecord(
        violation_type="llm_call_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "model": event.attributes["model_name"],
            "error": event.attributes["error"],
        },
    )
```

### Detect empty retrieval (no nodes found)

```python
def detect_empty_retrieval(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "retrieval_finished":
        return None
    if event.attributes.get("num_nodes", -1) != 0:
        return None
    return ViolationRecord(
        violation_type="empty_retrieval",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={"query_str": event.attributes.get("query_str")},
    )
```

### Detect a query that errored

```python
def detect_query_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "query_error":
        return None
    return ViolationRecord(
        violation_type="query_execution_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "query_str": event.attributes.get("query_str"),
            "error": event.attributes.get("error"),
        },
    )
```

### Detect a rate-limited LLM call

```python
RATE_LIMIT_SIGNALS = {"429", "rate limit", "quota exceeded", "too many requests"}

def detect_llm_rate_limit(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "llm_predict_error":
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
            "model": event.attributes["model_name"],
            "error": event.attributes["error"],
        },
    )
```

### Detect a restricted tool being called by an agent

```python
RESTRICTED_TOOLS = {"exec_code", "write_file", "delete_file", "shell_command"}

def detect_restricted_tool(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "agent_tool_call":
        return None
    tool = event.attributes.get("tool_name", "")
    if tool not in RESTRICTED_TOOLS:
        return None
    return ViolationRecord(
        violation_type="restricted_tool_called",
        severity="CRITICAL",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "tool_name": tool,
            "tool_input": event.attributes.get("tool_input"),
        },
    )
```

### Register detectors

```python
sentinel = Sentinel(detectors=[
    detect_llm_failure,
    detect_empty_retrieval,
    detect_query_failure,
    detect_llm_rate_limit,
    detect_restricted_tool,
])
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```

---

## `run_id` and trace correlation

Pass a `run_id` to correlate all events from one query execution:

```python
import uuid
run_id = str(uuid.uuid4())
adapter = LlamaIndexSentinelAdapter(run_id=run_id)
```

Every `SentinelEvent` produced during the run will carry `trace_id=run_id`.
When you inspect a `ViolationRecord`, `violation.trace_id` traces back to the
same run.

---

## Translating events manually

`to_sentinel_event(raw)` accepts a plain dict and is useful for offline
processing, replaying logged events, or testing detectors without running a
real query:

```python
event = adapter.to_sentinel_event({
    "type": "llm_predict_error",
    "model_name": "gpt-4o",
    "error": "rate limit exceeded",
    "timestamp": "2026-04-01T12:00:00Z",
})
```

Required key: `"type"`. All other keys are optional — missing values default
to `"unknown"`, `0`, or empty string depending on the field.

---

## Multiple queries in sequence

For benchmarking or batch-processing pipelines, use one adapter per query (or
one adapter for the full batch) and separate violations by `run_id`:

```python
all_violations = []

for i, query in enumerate(queries):
    adapter = LlamaIndexSentinelAdapter(run_id=f"query-{i}")
    adapter.setup()

    response = query_engine.query(query)

    sentinel = Sentinel(detectors=[detect_llm_failure, detect_empty_retrieval])
    adapter.flush_into(sentinel)
    all_violations.extend(sentinel.detect_violations())

if all_violations:
    print(f"{len(all_violations)} violation(s) detected across {len(queries)} queries")
    for v in all_violations:
        print(f"  [{v.severity}] {v.violation_type} trace={v.trace_id}")
```

---

## Assertion-style auditing in CI

Raise on violations to hard-fail an evaluation or integration test:

```python
adapter = LlamaIndexSentinelAdapter(run_id="ci-eval")
adapter.setup()
response = query_engine.query("...")

sentinel = Sentinel(detectors=[
    detect_llm_failure,
    detect_query_failure,
    detect_restricted_tool,
])
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()

if violations:
    for v in violations:
        print(f"[{v.severity}] {v.violation_type}: {v.detail}")
    raise RuntimeError(f"Query run failed audit — {len(violations)} violation(s)")
```

---

## Differences from other adapters

|                  | LangGraph              | CrewAI                     | AutoGen                   | LlamaIndex                  |
|------------------|------------------------|----------------------------|---------------------------|-----------------------------|
| Event delivery   | Pull (debug stream)    | Push (event bus callbacks) | Pull (chat history)       | Push (dispatcher callbacks) |
| Primary API      | `iter_events(stream)`  | `setup()` + `flush_into()` | `iter_messages(history)`  | `setup()` + `flush_into()`  |
| Correlation ID   | LangGraph task UUID    | `run_id` you provide       | `run_id` you provide      | `run_id` you provide        |
| Coverage         | Graph nodes/edges      | Crew/agent/task/tool       | Chat messages + functions | Query/retrieval/LLM/agent   |

---

## Attributes reference

### Query events (`query_*`)

| Key         | Type  | Present in            | Description                      |
|-------------|-------|-----------------------|----------------------------------|
| `query_str` | `str` | all query events      | The query string                 |
| `response`  | `str` | `finished` only       | Final response (≤500 chars)      |
| `error`     | `str` | `error` only          | Error message                    |

### Retrieval events (`retrieval_*`)

| Key         | Type  | Present in            | Description                      |
|-------------|-------|-----------------------|----------------------------------|
| `query_str` | `str` | all retrieval events  | The query string                 |
| `num_nodes` | `int` | `finished` only       | Number of nodes retrieved        |
| `error`     | `str` | `error` only          | Error message                    |

### LLM events (`llm_predict_*`)

| Key          | Type  | Present in            | Description                      |
|--------------|-------|-----------------------|----------------------------------|
| `model_name` | `str` | all LLM events        | Model identifier                 |
| `query_str`  | `str` | `started` (if set)    | Prompt context                   |
| `response`   | `str` | `finished` only       | LLM output (≤500 chars)          |
| `error`      | `str` | `error` only          | Error message                    |

### Agent events (`agent_step_*`, `agent_tool_call`)

| Key          | Type   | Present in                  | Description                        |
|--------------|--------|-----------------------------|------------------------------------|
| `task_id`    | `str`  | `started`, `finished`       | Agent task identifier              |
| `step_num`   | `int`  | `started`, `finished`       | Step index within the task         |
| `input`      | `str`  | `started` only              | Input to the step (≤500 chars)     |
| `output`     | `str`  | `finished` only             | Step output (≤500 chars)           |
| `is_last`    | `bool` | `finished` only             | Whether this is the final step     |
| `tool_name`  | `str`  | `agent_tool_call` only      | Name of the tool invoked           |
| `tool_input` | `str`  | `agent_tool_call` only      | Tool arguments (≤500 chars)        |

### Embedding events (`embedding_*`)

| Key          | Type  | Present in             | Description                    |
|--------------|-------|------------------------|--------------------------------|
| `model_name` | `str` | all embedding events   | Embedding model identifier     |
| `num_chunks` | `int` | all embedding events   | Number of chunks to embed      |

---

## API reference

### `LlamaIndexSentinelAdapter(run_id=None)`

**Parameters**

- `run_id` (`str | None`) — Optional run identifier used as `trace_id` on
  every translated event. Recommended: pass a UUID per query execution.

**Methods**

- `setup(dispatcher=None)` — Register an event handler with the LlamaIndex
  instrumentation dispatcher. Call this once before running any queries or
  agents. Pass a mock dispatcher for testing.

- `to_sentinel_event(raw: dict) -> SentinelEvent` — Translate one normalized
  event dict. Dispatches on `raw["type"]`. Unknown types map to
  `unknown_llamaindex_event`. Never raises; missing keys fall back to safe
  defaults.

- `drain() -> list[SentinelEvent]` — Return and clear all buffered events.
  Thread-safe.

- `flush_into(sentinel: Sentinel) -> None` — Call `sentinel.ingest(self.drain())`.
  Ingest all buffered events and clear the buffer in one step.

**Class attribute**

- `source_system = "llamaindex"` — appears on every translated `SentinelEvent`.

---

## Runtime security

`LlamaIndexSentinelAdapter` supports the full agentcop runtime security stack via four
optional constructor parameters. All default to `None` — existing code requires no changes.

### Constructor params

```python
LlamaIndexSentinelAdapter(
    run_id="run-001",
    gate=None,        # ExecutionGate
    permissions=None, # ToolPermissionLayer
    sandbox=None,     # AgentSandbox
    approvals=None,   # ApprovalBoundary
    identity=None,    # AgentIdentity
)
```

### What gets intercepted

The gate fires inside the `setup()` event handler for **`AgentToolCallEvent`** — before the
translated event is buffered.  This is the LlamaIndex instrumentation event that fires
when a ReAct or OpenAI agent decides to call a tool.  If denied, `PermissionError` is
raised and a security SentinelEvent is buffered.

### Example

```python
from llama_index.core import VectorStoreIndex
from agentcop.adapters.llamaindex import LlamaIndexSentinelAdapter
from agentcop.gate import ExecutionGate, ConditionalPolicy
from agentcop.permissions import ToolPermissionLayer, NetworkPermission
from agentcop.approvals import ApprovalBoundary

gate = ExecutionGate()
gate.register_policy("web_search", ConditionalPolicy(
    allow_if=lambda args: len(args.get("query", "")) <= 512,
    deny_reason="query too long — possible prompt injection",
))

permissions = ToolPermissionLayer()
permissions.declare("default", [NetworkPermission(domains=["api.openai.com"])])

approvals = ApprovalBoundary(requires_approval_above=75)

adapter = LlamaIndexSentinelAdapter(
    run_id="run-001",
    gate=gate,
    permissions=permissions,
    approvals=approvals,
)
adapter.setup()

response = query_engine.query("What is RAG?")

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```

---

## Reliability Tracking

Track LlamaIndex query and tool execution patterns over time using reliability
scoring. Wrap the LlamaIndex adapter with `wrap_for_reliability()` or add
`ReliabilityTracer` to your query engine.

```python
from llama_index.core import VectorStoreIndex
from agentcop import ReliabilityStore
from agentcop import wrap_for_reliability
from agentcop.adapters.llamaindex import LlamaIndexSentinelAdapter

store = ReliabilityStore("agentcop.db")
index = VectorStoreIndex.from_documents(docs)

adapter = LlamaIndexSentinelAdapter(run_id="run-001")
wrapped = wrap_for_reliability(adapter, agent_id="my-lli-agent", store=store)
wrapped.setup()

query_engine = index.as_query_engine()
response = query_engine.query("What is RAG?")

report = store.get_report("my-lli-agent", window_hours=24)
print(report.reliability_tier)
print(report.path_entropy)   # do queries take consistent retrieval paths?
```

Or instrument query execution directly:

```python
from agentcop import ReliabilityTracer, ReliabilityStore

store = ReliabilityStore("agentcop.db")

def reliable_query(query_engine, query: str) -> str:
    with ReliabilityTracer("my-lli-agent", store=store, input_data=query) as tracer:
        response = query_engine.query(query)
        tracer.record_tool_call("query", args={"query": query}, result=str(response))
        tracer.record_branch("retrieval_path")
        # LlamaIndex doesn't expose token counts directly; set if available
        tracer.record_tokens(input=100, output=200, model="gpt-4o")
    return str(response)
```

See [docs/guides/reliability.md](../guides/reliability.md) for the full guide.

---

## TrustChain Integration

Attach a cryptographic trust chain to every LlamaIndex agent step. All four
params default to `None` — no changes required.

### Constructor params

```python
LlamaIndexSentinelAdapter(
    run_id="run-001",
    trust=None,        # TrustChainBuilder — SHA-256-linked execution chain
    attestor=None,     # NodeAttestor      — Ed25519 signatures per node
    hierarchy=None,    # AgentHierarchy    — supervisor/worker delegation rules
    trust_interop=None,# TrustInterop      — portable cross-runtime claim export
)
```

### What gets recorded

`record_trust_node()` is called inside the `AgentRunStepEndEvent` handler.
The LlamaIndex task ID becomes both `node_id` and `agent_id`; `tool_calls` is
set to `["agent_step"]`.

### Example

```python
from agentcop.adapters.llamaindex import LlamaIndexSentinelAdapter
from agentcop.trust import TrustChainBuilder, NodeAttestor
from agentcop import Sentinel

private_pem, public_pem = NodeAttestor.generate_key_pair()

adapter = LlamaIndexSentinelAdapter(
    run_id="run-001",
    trust=TrustChainBuilder(agent_id="my-lli-agent"),
    attestor=NodeAttestor(private_key_pem=private_pem),
)
adapter.setup()

response = query_engine.query("What is RAG?")

sentinel = Sentinel()
adapter.flush_into(sentinel)

result = adapter._trust.verify_chain()
print(result.verified)
print(adapter._trust.export_chain("json"))
```

See [docs/guides/trust-chain.md](../guides/trust-chain.md) for the full guide.
