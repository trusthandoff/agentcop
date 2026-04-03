# Changelog

All notable changes to agentcop are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
agentcop uses [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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

[Unreleased]: https://github.com/trusthandoff/agentcop/compare/v0.4.5...HEAD
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
