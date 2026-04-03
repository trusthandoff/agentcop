# agentcop Roadmap

The mission: make agent fleet security a solved problem. Every violation detectable.
Every handoff auditable. Every incident reconstructable. Without requiring teams to
rebuild forensic infrastructure from scratch.

This document describes where we are, what's shipping next, and where the ecosystem
is going. It is honest about what is done and what is not yet real.

---

## Where we are — v0.2.0

**Released April 2026.**

The foundation is solid. Nine adapters cover the major agent frameworks in production
today. The core schema is stable, thread-safe, and OTel-aligned. The test suite is
comprehensive enough to catch regressions in detector logic, serialization contracts,
and lock behavior independently.

### What's shipped

**Core library**
- `SentinelEvent` — universal OTel-aligned event schema. `event_id`, `event_type`,
  `timestamp`, `severity`, `body`, `source_system`, `trace_id`, `span_id`,
  `producer_id`, `attributes`. Pydantic v2. Immutable after construction.
- `ViolationRecord` — structured finding. `violation_type`, `severity`
  (`WARN`/`ERROR`/`CRITICAL`), `source_event_id`, `trace_id`, `detail`.
- `Sentinel` — thread-safe auditor. `ingest()`, `detect_violations()`, `report()`,
  `register_detector()`. Lock-minimal design: snapshot under lock, run detectors outside.
- `SentinelAdapter` — `@runtime_checkable` Protocol. Implement `to_sentinel_event()`
  and any system can feed the pipeline.
- Four built-in detectors: `detect_rejected_packet`, `detect_stale_capability`,
  `detect_overlap_window`, `detect_ai_generated_payload`.
- Optional OTel log export via `agentcop[otel]`.

**Nine production adapters**

| Adapter | Framework | Interception method |
|---|---|---|
| LangGraph | Graph nodes & edges | `stream_mode="debug"` stream |
| LangSmith | Run tracing | `client.create_run` / `client.update_run` wrapping |
| Langfuse | Observations | `SpanProcessor` on OTel-backed `TracerProvider` |
| Datadog | APM spans | `tracer._writer.write()` interception |
| Haystack | Pipeline components | `ProxyTracer` replacement |
| Semantic Kernel | Kernel filters | `FUNCTION_INVOCATION`, `PROMPT_RENDERING`, `AUTO_FUNCTION_INVOCATION` |
| LlamaIndex | Pipeline events | `BaseCallbackHandler` on global `CallbackManager` |
| CrewAI | Agent & task events | `step_callback` / `task_callback` hooks |
| AutoGen | Agent messages | `Agent.send` / `Agent.receive` wrapping |

**1199 tests** across all adapters, core schema, detector logic, and thread safety.
The test suite runs without any optional dependency installed — every adapter mocks
its import guard.

**agentcop.live** — public scanner at agentcop.live. Paste a trace JSON (any supported
format), get violation findings back instantly. Built on the same detector pipeline as
the library. No data retained.

---

## What's coming

### Near-term — Q2 2026

**`semgrep-rules-agents` (new public repo)**
A curated Semgrep ruleset for static analysis of agent code. Rules targeting:
- Unguarded tool registrations (`shell`, `fs_write`, `eval` in tool schemas)
- Missing nonce or TTL fields on delegation payloads
- Detectors that perform I/O (breaking the pure-function contract)
- `register_detector()` calls after `ingest()` (race-prone ordering)
- Hardcoded `trace_id` or `event_id` values in production code

These rules run in CI before any event is ever emitted. The goal is to catch
structural problems at the code level, not at runtime. The repo will publish to
the Semgrep Registry and ship a GitHub Actions workflow.

**GitHub App**
Install on any repo. On every PR:
1. Runs `semgrep-rules-agents` across changed files.
2. Annotates violations inline on the diff — line-level, not just file-level.
3. Blocks merge on CRITICAL findings (configurable).
4. Posts a summary comment with finding counts by severity and rule.

The app will have zero required configuration for basic use. Advanced settings
(custom detector rules, severity thresholds, ignore paths) via `.agentcop.yml`.

**VS Code extension**
Real-time violation hints as you write agent code. The extension:
- Runs the Semgrep ruleset in the background on save.
- Surfaces findings as squiggles and hover diagnostics — same UX as a type error.
- Ships a "Sentinel: Run detectors on selection" command for pasting raw traces.
- Integrates with the agentcop.live scanner for live trace analysis from the editor.

No account required. The Semgrep rules run locally. The trace scanner requires a
network call only when explicitly invoked.

---

### Medium-term — Q3 2026

**Runtime continuous monitoring**
Right now `Sentinel` is invoked explicitly — you call `ingest()` and
`detect_violations()` at defined points. The next model adds a long-running monitor
mode:

```python
sentinel = Sentinel()
sentinel.monitor(source=my_event_stream, interval_ms=500)
# violations surfaced via callback, not polling
sentinel.on_violation(lambda v: alert(v))
```

The monitor runs a background thread that drains the event stream, runs detectors
on a sliding window, and fires callbacks on new findings. Designed for production
agent fleets where you want continuous coverage, not point-in-time audits.

Alert sinks will ship as a separate `agentcop[alerts]` extra: PagerDuty, Slack,
and generic webhook out of the box.

**Distributed nonce tracking**
The `detect_overlap_window` detector currently operates on in-process state — it
can catch overlapping token windows within a single `Sentinel` instance, but not
across processes or replicas.

Distributed nonce tracking adds a pluggable backend for nonce state:

```python
from agentcop.nonce import RedisNonceStore

sentinel = Sentinel(nonce_store=RedisNonceStore(redis_client))
```

The store interface is minimal: `get(nonce_id)`, `set(nonce_id, expiry)`,
`delete(nonce_id)`. A default in-memory store preserves current behavior.
Redis is the first production backend. Any store that satisfies the protocol works.

This is the precondition for the cross-agent invalidation feature below.

**Cross-agent invalidation**
When a violation is detected in one agent, its downstream delegates should be
notified and optionally halted. This requires two things:
1. The nonce store (above) — shared state that all agents in a fleet read.
2. An invalidation signal — a lightweight pub/sub message that marks a trace as
   tainted.

The design: a `TaintRecord` is written to the nonce store when a CRITICAL violation
fires. Any agent that checks its delegation token before executing reads the taint
and can refuse to proceed. This is passive invalidation — agents opt in to checking,
the auditor does not actively kill processes.

Integration will ship as `agentcop[invalidation]` with Redis Pub/Sub and a
NATS backend. The `TrustHandoff` integration will use this natively.

---

### Longer-term — Q4 2026 and beyond

**LangSmith and Langfuse deep integration**
The current adapters intercept at the SDK method level — good for coverage, but
they miss the rich metadata that LangSmith and Langfuse surfaces in their own
data models (feedback scores, latency percentiles, token cost, human labels).

Deep integration means:
- A scheduled sync mode: pull runs/traces from LangSmith/Langfuse APIs on a
  configurable cadence and run the full detector suite against historical data.
- Bidirectional feedback: write `ViolationRecord` findings back as LangSmith
  feedback annotations or Langfuse scores. Violations become queryable in their
  native UIs.
- Composite detectors that join across multiple runs — detect, for example, a
  pattern of stale capabilities across an agent's last 50 runs, not just the
  current one.

This is the closest agentcop gets to a SIEM for agent fleets.

**Detector registry and community rules**
A public registry of named detectors — versioned, searchable, composable. Teams
publish detectors the same way they publish npm packages. The registry will ship
with a signing model so consumers can verify detector provenance before registering
them in production sentinels.

The `DEFAULT_DETECTORS` list will remain small and stable. The registry is the
extension point for everything else.

**`agentcop audit` CLI**
A local CLI for offline trace analysis:

```
agentcop audit --trace trace.json --detectors @community/owasp-llm-top10
```

Reads any supported trace format, runs the specified detector set, outputs a
SARIF report. Composable with CI pipelines and local debugging. The same engine
that powers the GitHub App and the VS Code extension.

---

## What we are deliberately not doing

**Not a proxy or a firewall.** agentcop is a forensic auditor, not a traffic
interceptor. We do not sit in the hot path between an agent and its tools. This
keeps latency impact zero and keeps the library safe to add to production systems
without operational risk.

**Not a managed cloud platform (yet).** agentcop.live is a convenience scanner.
We are not building a SaaS that retains your traces. If that changes, it will be
opt-in, clearly documented, and auditable.

**Not a replacement for access control.** Detectors fire after the fact. They are
a forensic layer, not an authorization layer. Use proper IAM and tool gating for
prevention; use agentcop for detection and incident reconstruction.

---

## Schema stability commitment

`SentinelEvent` and `ViolationRecord` field names are stable from v0.1.0.
`violation_type` strings in built-in detectors are stable from the version they
shipped. Breaking changes require a major version bump and an entry in CHANGELOG.md.

The adapter event ID schemes (`lg-task-{id}`, `lg-result-{id}`, etc.) are stable
from the version they shipped — they are join keys for downstream systems and
treated accordingly.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The highest-value contributions right now:
- New adapters following the established pattern in `CLAUDE.md`
- New detectors with clear `violation_type` semantics and tests
- Semgrep rules for the upcoming `semgrep-rules-agents` repo (open an issue first)

The architecture constraints in `CLAUDE.md` are load-bearing. Read them before
opening a PR.
