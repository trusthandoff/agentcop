# Changelog

All notable changes to agentcop are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
agentcop uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.2.0] — 2026-04-01

### Added
- **Datadog adapter** (`agentcop[ddtrace]`) — intercepts `tracer._writer.write()`
  on any `ddtrace.Tracer` instance and classifies finished spans into
  `llm_span_finished/error`, `http_span_finished/error`, `db_span_finished/error`,
  and `span_finished/error` event types based on the `component` tag.
  LLM spans include model, provider, and token-usage attributes. 153 tests.

---

## [0.1.9] — 2026-04-01

### Added
- **LangSmith adapter** (`agentcop[langsmith]`) — wraps `client.create_run` and
  `client.update_run` on a `langsmith.Client` to intercept all run traffic.
  In-flight registry correlates start and end by run ID; emits typed events for
  chain, LLM, tool, retriever, and embedding run types. 130 tests.

---

## [0.1.8] — 2026-04-01

### Added
- **Langfuse adapter** (`agentcop[langfuse]`) — registers a `SpanProcessor` on
  the Langfuse 4.x `TracerProvider` (which is OTel-backed). Translates all
  Langfuse observation types — generation, span, tool, retriever, event,
  guardrail — into 13 event types. 159 tests.

---

## [0.1.7] — 2026-04-01

### Added
- **Semantic Kernel adapter** (`agentcop[semantic-kernel]`) — registers three
  async filter middleware functions (`FUNCTION_INVOCATION`, `PROMPT_RENDERING`,
  `AUTO_FUNCTION_INVOCATION`) on a Semantic Kernel `Kernel` instance. Translates
  8 event types across function, prompt, and auto-function invocation. 129 tests.

---

## [0.1.6] — 2026-04-01

### Added
- **Haystack adapter** (`agentcop[haystack]`) — replaces the Haystack
  `ProxyTracer` with a wrapping tracer that intercepts all pipeline,
  component, LLM, retriever, and embedder span events. Translates 13 event
  types. 130 tests.

---

## [0.1.5] — 2026-03-31

### Added
- **LlamaIndex adapter** (`agentcop[llamaindex]`) — registers a
  `BaseCallbackHandler` on the LlamaIndex global `CallbackManager`, buffering
  events for query, retrieve, embedding, LLM, chunking, and exception spans.

---

## [0.1.4] — 2026-03-31

### Added
- **AutoGen adapter** (`agentcop[autogen]`) — intercepts `Agent.send` /
  `Agent.receive` by wrapping those methods on registered agents, translating
  AutoGen message traffic into `SentinelEvent` objects.

---

## [0.1.3] — 2026-03-31

### Added
- **CrewAI adapter** (`agentcop[crewai]`) — registers callback hooks on a
  `Crew` instance (`step_callback`, `task_callback`) to translate agent step
  and task completion events.

---

## [0.1.2] — 2026-03-31

### Added
- **LangGraph adapter** (`agentcop[langgraph]`) — `LangGraphSentinelAdapter`
  with `iter_events()` that reads a LangGraph `stream_mode="debug"` stream and
  translates `task`, `task_result`, and `checkpoint` events.
- `docs/adapters/langgraph.md` — quickstart, detector recipes, attributes
  reference.
- `CLAUDE.md` — internal architecture guide and workflow rules.

---

## [0.1.1] — 2026-03-28

### Added
- Comprehensive test suite — `test_event.py`, `test_violations.py`,
  `test_sentinel.py`, `test_adapter.py`.

### Fixed
- PyPI publish workflow authentication (`PYPI_API_TOKEN`).

---

## [0.1.0] — 2026-03-27

### Added
- `SentinelEvent` — OTel-aligned Pydantic event schema with `event_id`,
  `event_type`, `timestamp`, `severity`, `body`, `source_system`, `trace_id`,
  `span_id`, `producer_id`, and `attributes`.
- `ViolationRecord` — structured finding schema with `violation_type`,
  `severity` (`WARN`/`ERROR`/`CRITICAL`), `source_event_id`, `trace_id`,
  and `detail`.
- `Sentinel` — thread-safe auditor class with `ingest()`, `detect_violations()`,
  `report()`, and `register_detector()`.
- `SentinelAdapter` — `@runtime_checkable` Protocol for the adapter bridge
  pattern.
- Four built-in violation detectors: `detect_rejected_packet`,
  `detect_stale_capability`, `detect_overlap_window`,
  `detect_ai_generated_payload`.
- `DEFAULT_DETECTORS` list.
- Optional OTel export via `agentcop[otel]`.

[Unreleased]: https://github.com/trusthandoff/agentcop/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/trusthandoff/agentcop/compare/v0.1.9...v0.2.0
[0.1.9]: https://github.com/trusthandoff/agentcop/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/trusthandoff/agentcop/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/trusthandoff/agentcop/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/trusthandoff/agentcop/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/trusthandoff/agentcop/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/trusthandoff/agentcop/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/trusthandoff/agentcop/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/trusthandoff/agentcop/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/trusthandoff/agentcop/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/trusthandoff/agentcop/releases/tag/v0.1.0
