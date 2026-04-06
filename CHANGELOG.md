# Changelog

All notable changes to agentcop are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
agentcop uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.4.8] — 2026-04-06

### Added

- **Runtime security integration across all 10 framework adapters** — every adapter
  (`LangGraph`, `CrewAI`, `AutoGen`, `LlamaIndex`, `Haystack`, `SemanticKernel`,
  `Moltbook`, `Langfuse`, `LangSmith`, `Datadog`) now accepts four optional
  keyword-only constructor params: `gate`, `permissions`, `sandbox`, `approvals`.
  All default to `None` for full backward compatibility — existing code requires no
  changes.

- **`adapters/_runtime.py`** — shared runtime security helper module:
  - `check_tool_call(adapter, tool_name, args, context, agent_id)` — enforces the full
    security chain (ToolPermissionLayer → ExecutionGate → ApprovalBoundary) in order.
    Fires SentinelEvents for every security decision and raises `PermissionError` on
    denial.
  - `fire_security_event(adapter, event_type, tool_name, args_hash, reason, severity)`
    — buffers `gate_denied`, `permission_violation`, `approval_requested` SentinelEvents
    onto any adapter that carries `_buffer` + `_lock`.

- **Framework-specific interception points:**
  - **LangGraph** — gate fires in `iter_events()` for every `task` (node start) event
    before it is yielded.
  - **CrewAI** — gate fires inside the `ToolUsageStartedEvent` event bus handler before
    the translated event is buffered. Agent role is used as `agent_id`.
  - **AutoGen** — gate fires in `_from_function_call_started()` for both 0.2.x and 0.4.x
    message formats. Sender name is used as `agent_id`.
  - **LlamaIndex** — gate fires in the `setup()` dispatcher handler for
    `AgentToolCallEvent`.
  - **Haystack** — gate fires in `_WrappingTracer.trace()` before each component's
    execution context is entered. `AgentSandbox` wraps the component execution context
    when provided.
  - **Semantic Kernel** — gate fires in `_function_invocation_filter` after the
    `function_invoking` event and before `await next(context)`.
  - **Moltbook** — gate fires in `_from_skill_executed()` before skill translation.
    Sandbox enforces network restrictions to `moltbook.com` only.
  - **LangSmith / Langfuse / Datadog** (observability adapters) — gate decisions are
    logged as SentinelEvents for tool/LLM spans. `PermissionError` is caught internally
    to preserve export pipeline integrity.

- **`identity` parameter** — all adapters accept an optional `AgentIdentity` instance.
  When provided, `identity.trust_score` (0–100) is forwarded to the gate as
  `context["trust_score"]`, enabling trust-adaptive policies:
  - `trust_score < 50` → configure stricter policies (lower rate limits, smaller path
    allowlists)
  - `trust_score >= 80` → relaxed policies for known-good agents

- **65 new runtime security tests** across all adapter test files:
  - Gate denial raises `PermissionError` and buffers `gate_denied` event
  - Permission violation raises `PermissionError` and buffers `permission_violation` event
  - `ApprovalBoundary.submit()` and `wait_for_decision()` called for high-risk scores
  - Sandbox stored on adapter and passed through correctly
  - All existing adapter tests continue to pass with zero regressions

- **Documentation updates:**
  - `docs/adapters/*.md` — runtime security section added to all 10 adapter docs with
    framework-specific examples and interception point descriptions
  - `docs/guides/runtime-security.md` — new "Adapter integration" section covering the
    universal pattern, interception point table, enforcement order, `AgentIdentity`
    trust_score usage, and security event types
  - `README.md` — adapter section updated with runtime security params and link to guide

- **`pyproject.toml`** — `[runtime]` optional-dependencies group added.

### Test count

**1885 passed, 9 skipped** (9 skipped require optional framework packages not installed
in CI).

---

## [0.4.7] — 2026-04-06

### Added

- **`ExecutionGate`** (`agentcop[runtime]`) — policy-based tool execution
  control with a persistent SQLite audit log. Four policy types: `AllowPolicy`,
  `DenyPolicy`, `ConditionalPolicy` (predicate over args dict), and
  `RateLimitPolicy` (sliding-window, thread-safe). `ExecutionGate.wrap()`
  decorator gates any callable; `ExecutionGate.check()` returns a
  `GateDecision(allowed, reason, risk_score)`. Every decision is written to
  the `gate_decisions` table for post-incident forensics.
  `ExecutionGate.decision_log()` returns the most recent N entries.

- **`ToolPermissionLayer`** (`agentcop[runtime]`) — declarative capability
  scoping per agent, deny by default. Four built-in permission types:
  `ReadPermission` (fnmatch path patterns), `WritePermission` (fnmatch path
  patterns), `NetworkPermission` (domain allowlist with optional subdomain
  matching via `allow_subdomains=True`), `ExecutePermission` (leading command
  token allowlist). `ToolPermissionLayer.declare(agent_id, permissions)` sets
  the capability scope; `verify(agent_id, tool, args)` returns a
  `PermissionResult(granted, reason)`. `attach_to_gate(gate, agent_id)` wires
  declared permissions into an `ExecutionGate` as `ConditionalPolicy` entries.
  Emits `permission_violation` `SentinelEvent` on denial when a `Sentinel` is
  attached.

- **`AgentSandbox`** (`agentcop[runtime]`) — runtime isolation with active
  syscall interception. Patches `builtins.open`, `urllib.request.urlopen`,
  `subprocess.run`, and `requests.Session.request` (when installed) for the
  duration of the `with` block. Enforces `allowed_paths` (fnmatch),
  `allowed_domains`, and `max_execution_time` (raises `SandboxTimeoutError`
  via `ctypes.pythonapi.PyThreadState_SetAsyncExc` if exceeded). Re-entrant
  and thread-safe. Merges constraints from a `ToolPermissionLayer` via
  `permission_layer` + `agent_id` constructor arguments. Lightweight
  validation-only mode available via `ExecutionSandbox` +
  `SandboxPolicy(allowed_paths, denied_paths, allowed_env_vars, denied_env_vars,
  max_output_bytes)`.

- **`ApprovalBoundary`** (`agentcop[runtime]`) — human-in-the-loop gate for
  high-risk actions. Auto-approves calls with `risk_score <=
  requires_approval_above`; holds and notifies for calls above the threshold.
  Dispatches to configurable channels: `"cli"` (stderr prompt), `"webhook"`
  (POST JSON), `"slack"` (Incoming Webhook), `"email"` (placeholder). Timeout
  fires auto-deny after `timeout` seconds. Persistent audit trail in SQLite via
  `db_path`. `wait_for_decision(request_id)` blocks the caller thread until
  resolution. `audit_trail(request_id, limit)` returns newest-first log entries.
  Raises `ApprovalDenied` on denial. In-memory `ApprovalGate` available for
  testing (no SQLite, no channels).

- **`AgentCop.protect()`** (`agentcop[runtime]`) — one-line full pipeline
  protection. `AgentCop(gate, permissions, sandbox, approvals, sentinel,
  agent_id, identity)` chains all four enforcement layers plus an `AgentIdentity`
  trust guard into a single wrapper. `cop.protect(agent)` returns a
  `_ProtectedAgent` that routes every `run()` call through a five-stage pipeline:
  (1) trust guard — blocks if trust score < 30; (2) `ExecutionGate` check;
  (3) `ToolPermissionLayer` verify; (4) `ApprovalBoundary` submit + wait;
  (5) `AgentSandbox` context manager wrapping `agent.run()`. The wrapped agent
  is otherwise a transparent proxy (`__getattr__` delegation). `all_layers_active`
  is `True` when all four enforcement layers are configured.

- **`docs/guides/runtime-security.md`** — complete runtime enforcement guide:
  why runtime enforcement vs static scanning, `ExecutionGate` quickstart + all
  four policy types with examples, `ToolPermissionLayer` quickstart + all four
  permission types, `AgentSandbox` quickstart + intercepted syscall table +
  validation-only mode, `ApprovalBoundary` quickstart + all three channel types
  (CLI / webhook / Slack) + audit trail, full pipeline example, integration with
  `AgentIdentity` and the badge system, CLI commands reference.

- **`pip install agentcop[runtime]`** — new optional-dependency group for the
  runtime security layer.

### Tests

- 1829 tests passing across the full suite.

---

## [0.4.5] — 2026-04-03

### Added

- **Moltbook adapter** (`agentcop[moltbook]`) — `MoltbookSentinelAdapter` for
  AI agents operating on the Moltbook social network. Performs taint analysis on
  every `post_received` and `mention_received` event using 13+ injection
  patterns (direct overrides, role injection, credential theft triggers,
  exfiltration keywords, and encoding-bypass variants: base64, unicode
  zero-width chars, right-to-left override). Translates 14 raw Moltbook event
  types into `SentinelEvent` objects with a `moltbook.*` OTel attribute
  namespace. Buffered drift events: `moltbook_submolt_drift`,
  `moltbook_agent_spike` (≥5 consecutive posts from unknown agents — the
  pattern from the January 2026 breach), `moltbook_reply_hijack`,
  `moltbook_exfiltration_attempt` (novel external URL in outbound post),
  `moltbook_verified_peer`. `flush_into(sentinel)` drains buffered events into
  a live `Sentinel`.

- **Badge integration on Moltbook** — `MoltbookSentinelAdapter.setup()` calls
  `AgentIdentity.register()` and issues an Ed25519-signed `AgentBadge`
  (SECURED / MONITORED / AT RISK). The `badge_id` is stored on the adapter and
  automatically included in the `moltbook.badge_id` attribute of every
  `post_created` event, so peer agents and humans on the platform can read and
  verify the agent's security posture. Badge URL: `agentcop.live/badge/{id}`.

- **Skill badge verification** — every `skill_executed` event is automatically
  checked against the skill's ClawHub manifest badge metadata. Events are
  re-classified to `skill_executed_unverified` (WARN) when no badge is present
  or `skill_executed_at_risk` (CRITICAL) when the badge tier is AT RISK.
  Verified SECURED/MONITORED skills pass through as `skill_executed` (INFO).

- **`docs/adapters/moltbook.md`** — full integration guide: January 2026 breach
  context, manual mode and SDK mode quickstarts, badge setup walkthrough, skill
  badge verification table, 5 detector recipes (prompt injection in feed,
  coordinated campaign, unverified skill, behavioral drift post-infection, API
  key exfiltration), injection pattern reference table with 13 patterns, badge
  verification REST API reference, full `MoltbookSentinelAdapter` API reference
  with event type mapping and OTel attribute namespace.

### Fixed

- 8 audit fixes across adapters and detectors (edge-case handling for missing
  fields, malformed timestamps, empty attribute dicts).

### Tests

- 1574 tests passing across the full suite.

---

## [0.4.4] — 2026-04-03

### Added

- **Ed25519 badge system** (`agentcop[badge]`) — `AgentBadge` Pydantic model,
  `BadgeIssuer` (Ed25519 sign + verify), `BadgeStore` / `InMemoryBadgeStore` /
  `SQLiteBadgeStore`, `generate_svg()`, `generate_badge_card()`,
  `generate_markdown()`, `tier_from_score()`. Badges are 30-day signed
  certificates with three tiers: SECURED (≥ 80), MONITORED (50–79), AT RISK
  (< 50). Auto-revocation triggers when trust score drops below 30.

- **`AgentIdentity` — Know Your Agent (KYA)** — verifiable agent fingerprint
  (Ed25519 hash of agent source), behavioral baseline built from the first 10+
  executions, trust score (starts 70, ±20/10/5 per violation severity, +1 per
  clean run), and drift detection for new tools, slow execution, and new agent
  contacts. Exported as `AgentIdentity`, `BehavioralBaseline`, `DriftConfig`,
  `IdentityStore`, `InMemoryIdentityStore`, `SQLiteIdentityStore` from the top-
  level `agentcop` package. `Sentinel.attach_identity()` auto-enriches all
  ingested events with identity metadata and trust score.

- **OpenClaw `agentcop` skill** (`skills/agentcop/`) — Python skill bridge
  (`skill.py`) for the OpenClaw agent platform. Subcommands: `status`, `report`,
  `scan`, `taint-check`, `output-check`, and the full `badge` lifecycle
  (`generate`, `verify`, `renew`, `revoke`, `shield`, `markdown`, `status`).
  Auto-installs `agentcop` via pip on first run. State persisted in
  `~/.openclaw/agentcop/` via `SQLiteIdentityStore`.

- **OpenClaw `agentcop-monitor` hook** (`hooks/agentcop-monitor/`) — TypeScript
  hook that fires on `message:received`, `message:sent`, and
  `tool_result_persist`. Taint-checks inbound messages for LLM01 prompt
  injection and outbound content for LLM02 insecure output patterns. Violation
  alerts are pushed onto `event.messages` so they appear in the user's active
  channel (Telegram, WhatsApp, Discord, etc.) before the agent processes the
  message. Text is passed via stdin to avoid OS ARG_MAX limits.

- **`docs/guides/openclaw.md`** — complete integration guide: install skill,
  enable hook, badge commands, example violation alerts in Telegram and
  WhatsApp, configuration reference, state file layout, and troubleshooting.

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

[Unreleased]: https://github.com/trusthandoff/agentcop/compare/v0.4.7...HEAD
[0.4.7]: https://github.com/trusthandoff/agentcop/compare/v0.4.5...v0.4.7
[0.4.5]: https://github.com/trusthandoff/agentcop/compare/v0.4.4...v0.4.5
[0.4.4]: https://github.com/trusthandoff/agentcop/compare/v0.2.0...v0.4.4
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
