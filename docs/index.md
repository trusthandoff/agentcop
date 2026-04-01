# agentcop documentation

**Universal forensic auditor for agent systems.**

agentcop ingests events from any agent framework, runs pluggable violation
detectors, and produces structured findings. OTel-aligned schema, zero required
infrastructure.

```
pip install agentcop
```

---

## Core concepts

| Concept | Description |
|---|---|
| [`SentinelEvent`](../src/agentcop/event.py) | Immutable event record — the universal unit of observation |
| [`ViolationRecord`](../src/agentcop/event.py) | Structured finding produced by a detector |
| [`Sentinel`](../src/agentcop/sentinel.py) | Thread-safe auditor — ingests events, runs detectors, reports |
| [`SentinelAdapter`](../src/agentcop/adapters/base.py) | Protocol for bridging a framework's events to `SentinelEvent` |
| `ViolationDetector` | `(SentinelEvent) -> ViolationRecord | None` — a pure function |

---

## Adapters

Each adapter bridges one framework into the universal `SentinelEvent` schema.
Install only the adapters you need.

| Adapter | Framework | Hook mechanism | Install |
|---|---|---|---|
| [LangGraph](adapters/langgraph.md) | LangGraph graph nodes & edges | Debug stream (`stream_mode="debug"`) | `pip install agentcop[langgraph]` |
| [LangSmith](adapters/langsmith.md) | LangSmith run tracing | `client.create_run` / `update_run` wrapping | `pip install agentcop[langsmith]` |
| [Langfuse](adapters/langfuse.md) | Langfuse 4.x observations | OTel `SpanProcessor` on `TracerProvider` | `pip install agentcop[langfuse]` |
| [Datadog](adapters/datadog.md) | ddtrace APM spans | `tracer._writer.write()` wrapping | `pip install agentcop[ddtrace]` |
| [Haystack](adapters/haystack.md) | Haystack pipeline components | `ProxyTracer` replacement | `pip install agentcop[haystack]` |
| [Semantic Kernel](adapters/semantic_kernel.md) | Semantic Kernel function/prompt filters | Async filter middleware chain | `pip install agentcop[semantic-kernel]` |
| [LlamaIndex](adapters/llamaindex.md) | LlamaIndex pipeline events | `BaseCallbackHandler` on `CallbackManager` | `pip install agentcop[llamaindex]` |
| [CrewAI](adapters/crewai.md) | CrewAI agent & task events | `step_callback` / `task_callback` hooks | `pip install agentcop[crewai]` |
| [AutoGen](adapters/autogen.md) | AutoGen agent messages | `Agent.send` / `receive` wrapping | `pip install agentcop[autogen]` |

---

## Built-in detectors

Four detectors ship in `DEFAULT_DETECTORS` and run automatically with a plain
`Sentinel()`:

| Detector | Fires on `event_type` | Severity |
|---|---|---|
| `detect_rejected_packet` | `packet_rejected` | ERROR |
| `detect_stale_capability` | `capability_stale` | ERROR |
| `detect_overlap_window` | `token_overlap_used` | WARN |
| `detect_ai_generated_payload` | `ai_generated_payload` | WARN |

---

## Optional integrations

- **OpenTelemetry export** — `pip install agentcop[otel]`  
  Export `SentinelEvent` objects as OTel log records via `OtelSentinelExporter`.

---

## Project files

| File | Purpose |
|---|---|
| [README.md](../README.md) | Project overview and quickstart |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | How to contribute — setup, adding adapters/detectors, code style |
| [CHANGELOG.md](../CHANGELOG.md) | Version history |
| [SECURITY.md](../SECURITY.md) | Vulnerability reporting policy |
| [LICENSE](../LICENSE) | MIT License |
| [CLAUDE.md](../CLAUDE.md) | Internal architecture guide for AI-assisted development |
