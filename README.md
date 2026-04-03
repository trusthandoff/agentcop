# agentcop — The Agent Cop

[![CI](https://github.com/trusthandoff/agentcop/actions/workflows/test.yml/badge.svg)](https://github.com/trusthandoff/agentcop/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/agentcop)](https://pypi.org/project/agentcop/)
[![Python](https://img.shields.io/pypi/pyversions/agentcop)](https://pypi.org/project/agentcop/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**The cop for agent fleets.**

Every agent fleet needs a cop. Agents delegate, handoff, and execute — and without forensic oversight, violations are invisible until they're incidents. `agentcop` is a universal auditor: ingest events from any agent system, run violation detectors, get structured findings.

OTel-aligned schema. Pluggable detectors. Adapter bridge to your stack. Zero required infrastructure.

**Features:**
- Universal `SentinelEvent` schema (OTel-aligned) + pluggable `ViolationDetector` functions
- Ten framework adapters (LangGraph, LangSmith, Langfuse, Datadog, Haystack, Semantic Kernel, LlamaIndex, CrewAI, AutoGen, Moltbook)
- `AgentIdentity` — verifiable fingerprint, behavioral baseline, trust scoring, and drift detection (KYA — Know Your Agent)
- Ed25519-signed `AgentBadge` system — tiered SECURED / MONITORED / AT RISK certificates for README display and cross-agent verification
- **Moltbook adapter** — purpose-built monitoring for AI agents on the Moltbook social network: prompt-injection taint analysis on every received post, coordinated campaign detection, skill badge verification (LLM05), API key exfiltration detection (LLM06), and Ed25519 badge integration for agent profiles
- OpenClaw integration — `/security` skill commands + `agentcop-monitor` hook for real-time LLM01/LLM02 detection in Telegram, WhatsApp, Discord, and more
- Optional OTel export via `agentcop[otel]`

```
pip install agentcop
```

---

## Adapters

Ten adapters are available — install only what you need:

| Adapter | Framework | Install |
|---|---|---|
| [LangGraph](docs/adapters/langgraph.md) | LangGraph graph nodes & edges | `pip install agentcop[langgraph]` |
| [LangSmith](docs/adapters/langsmith.md) | LangSmith run tracing | `pip install agentcop[langsmith]` |
| [Langfuse](docs/adapters/langfuse.md) | Langfuse 4.x observations | `pip install agentcop[langfuse]` |
| [Datadog](docs/adapters/datadog.md) | ddtrace APM spans | `pip install agentcop[ddtrace]` |
| [Haystack](docs/adapters/haystack.md) | Haystack pipeline components | `pip install agentcop[haystack]` |
| [Semantic Kernel](docs/adapters/semantic_kernel.md) | Semantic Kernel filters | `pip install agentcop[semantic-kernel]` |
| [LlamaIndex](docs/adapters/llamaindex.md) | LlamaIndex pipeline events | `pip install agentcop[llamaindex]` |
| [CrewAI](docs/adapters/crewai.md) | CrewAI agent & task events | `pip install agentcop[crewai]` |
| [AutoGen](docs/adapters/autogen.md) | AutoGen agent messages | `pip install agentcop[autogen]` |
| [Moltbook](docs/adapters/moltbook.md) | Moltbook social network agents | `pip install agentcop[moltbook]` |

---

## How it works

```
your agent system
      │
      ▼
 SentinelAdapter          ← translate domain events to universal schema
      │
      ▼
  Sentinel.ingest()       ← load SentinelEvents into the auditor
      │
      ▼
  detect_violations()     ← run detectors, get ViolationRecords
      │
      ▼
  report() / your sink    ← stdout, OTel, alerting, whatever
```

---

## Quickstart

```python
from agentcop import Sentinel, SentinelEvent

sentinel = Sentinel()

# Feed it events (any source, any schema — adapt first)
sentinel.ingest([
    SentinelEvent(
        event_id="evt-001",
        event_type="packet_rejected",
        timestamp="2026-03-31T12:00:00Z",
        severity="ERROR",
        body="packet rejected — TTL expired",
        source_system="my-agent",
        attributes={"packet_id": "pkt-abc", "reason": "ttl_expired"},
    )
])

violations = sentinel.detect_violations()
# [ViolationRecord(violation_type='rejected_packet', severity='ERROR', ...)]

sentinel.report()
# [ERROR] rejected_packet — packet rejected — TTL expired
#   packet_id: pkt-abc
#   reason: ttl_expired
```

Built-in detectors fire on four event types out of the box:

| `event_type`            | Detector                      | Severity |
|-------------------------|-------------------------------|----------|
| `packet_rejected`       | `detect_rejected_packet`      | ERROR    |
| `capability_stale`      | `detect_stale_capability`     | ERROR    |
| `token_overlap_used`    | `detect_overlap_window`       | WARN     |
| `ai_generated_payload`  | `detect_ai_generated_payload` | WARN     |

---

## Custom detectors

Detectors are plain functions. Register as many as you need.

```python
from agentcop import Sentinel, SentinelEvent, ViolationRecord
from typing import Optional

def detect_unauthorized_tool(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "tool_call":
        return None
    if event.attributes.get("tool") in {"shell", "fs_write"}:
        return ViolationRecord(
            violation_type="unauthorized_tool",
            severity="CRITICAL",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={"tool": event.attributes["tool"]},
        )

sentinel = Sentinel()
sentinel.register_detector(detect_unauthorized_tool)
```

---

## TrustHandoff adapter

[TrustHandoff](https://github.com/trusthandoff/trusthandoff) ships a first-class adapter. If you're using `trusthandoff` for cryptographic delegation, plug it in directly:

```python
from trusthandoff.sentinel_adapter import TrustHandoffSentinelAdapter
from agentcop import Sentinel

adapter = TrustHandoffSentinelAdapter()
sentinel = Sentinel()

# raw_events: list of dicts from trusthandoff's forensic log
sentinel.ingest(adapter.to_sentinel_event(e) for e in raw_events)

violations = sentinel.detect_violations()
sentinel.report()
```

The adapter maps trusthandoff's event fields — `packet_id`, `correlation_id`, `reason`, `event_type` — to the universal `SentinelEvent` schema. Severity is inferred from event type. Everything else lands in `attributes`.

---

## Write your own adapter

Implement the `SentinelAdapter` protocol to bridge any system:

```python
from agentcop import SentinelAdapter, SentinelEvent
from typing import Dict, Any

class MySystemAdapter:
    source_system = "my-system"

    def to_sentinel_event(self, raw: Dict[str, Any]) -> SentinelEvent:
        return SentinelEvent(
            event_id=raw["id"],
            event_type=raw["type"],
            timestamp=raw["ts"],
            severity=raw.get("level", "INFO"),
            body=raw.get("message", ""),
            source_system=self.source_system,
            trace_id=raw.get("trace_id"),
            attributes=raw.get("metadata", {}),
        )
```

---

## LangGraph integration

Plug into any LangGraph graph with zero changes to your graph code. The adapter reads the debug event stream — node starts, node results, checkpoint saves — and translates each into a `SentinelEvent` for violation detection.

```
pip install agentcop[langgraph]
```

Stream a graph in `debug` mode and pipe every event through the adapter:

```python
from agentcop import Sentinel
from agentcop.adapters.langgraph import LangGraphSentinelAdapter

adapter = LangGraphSentinelAdapter(thread_id="run-abc")
sentinel = Sentinel()

sentinel.ingest(
    adapter.iter_events(
        graph.stream({"input": "..."}, config, stream_mode="debug")
    )
)

violations = sentinel.detect_violations()
sentinel.report()
```

Three LangGraph debug event types are translated:

| LangGraph event  | SentinelEvent type        | Severity |
|------------------|---------------------------|----------|
| `task`           | `node_start`              | INFO     |
| `task_result`    | `node_end`                | INFO     |
| `task_result`    | `node_error` (if errored) | ERROR    |
| `checkpoint`     | `checkpoint_saved`        | INFO     |

Each event carries structured `attributes` — `node`, `task_id`, `step`, `triggers`, `checkpoint_id`, `next` — so you can write targeted violation detectors:

```python
from agentcop import ViolationRecord

def detect_node_failure(event):
    if event.event_type == "node_error":
        return ViolationRecord(
            violation_type="node_execution_failed",
            severity="ERROR",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={
                "node": event.attributes["node"],
                "error": event.attributes["error"],
            },
        )

sentinel = Sentinel(detectors=[detect_node_failure])
```

The `thread_id` passed to `LangGraphSentinelAdapter` is used as `trace_id` on every event, correlating all events from a single graph run.

---

## OpenTelemetry export *(optional)*

`agentcop` events use an OTel-aligned schema out of the box (`trace_id`, `span_id`, severity levels). To export events as OTel log records:

```
pip install agentcop[otel]
```

```python
from agentcop.otel import OtelSentinelExporter
from opentelemetry.sdk._logs import LoggerProvider

exporter = OtelSentinelExporter(logger_provider=LoggerProvider())
exporter.export(events)
```

Attributes are emitted under the `sentinel.*` namespace. `trace_id` and `span_id` are mapped to OTel trace context.

---

## AgentIdentity — Know Your Agent

`AgentIdentity` gives every agent a verifiable fingerprint, a behavioral baseline, and a living trust score. Attach it to `Sentinel` to auto-enrich events and get drift alerts.

```python
from agentcop import Sentinel, AgentIdentity, SQLiteIdentityStore

store = SQLiteIdentityStore("agentcop.db")
identity = AgentIdentity.register(
    agent_id="my-agent-v1",
    code=agent_function,           # source hashed to Ed25519 fingerprint
    metadata={"framework": "langgraph", "version": "1.0"},
    store=store,
)

sentinel = Sentinel()
sentinel.attach_identity(identity)
# Events ingested via sentinel.push() are now enriched with agent identity + trust score.
```

Trust score starts at 70 and rises with clean executions. Critical violations deduct 20 points; errors 10; warnings 5. The baseline is built automatically from the first 10+ executions and used to detect drift (new tools, slow execution, new agent contacts).

---

## Agent badges

`agentcop[badge]` issues Ed25519-signed, publicly verifiable security certificates. Like SSL for websites — but for agents.

```
pip install agentcop[badge]
```

```python
from agentcop.badge import BadgeIssuer, SQLiteBadgeStore, generate_svg, generate_markdown

store = SQLiteBadgeStore("agentcop.db")
issuer = BadgeIssuer(store=store)

badge = issuer.issue(
    agent_id="my-agent",
    fingerprint=identity.fingerprint,
    trust_score=87.0,
    violations={"critical": 0, "warning": 1, "info": 0, "protected": 3},
    framework="langgraph",
    scan_count=42,
)

assert issuer.verify(badge)   # Ed25519 signature check

# SVG for embedding in HTML
svg = generate_svg(badge)

# Markdown snippet for README
print(generate_markdown(badge))
# ![AgentCop SECURED](https://agentcop.live/badge/<id>/shield)
```

Badge tiers are determined by trust score:

| Tier | Score | Color |
|---|---|---|
| 🟢 SECURED | ≥ 80 | `#00ff88` |
| 🟡 MONITORED | 50–79 | `#ffaa00` |
| 🔴 AT RISK | < 50 | `#ff3333` |

Badges expire after 30 days. A badge is auto-revoked if the trust score drops below 30.

Example README badge:

```markdown
![AgentCop SECURED](https://agentcop.live/badge/abc123/shield)
```

---

## Moltbook integration

Moltbook is a social network where AI agents read each other's posts and act on them — the most active prompt-injection attack surface in the current multi-agent ecosystem. The January 2026 breach exposed 1.5 M API keys via commands injected into the public feed. `agentcop` catches it.

```
pip install agentcop[moltbook]
```

The adapter performs taint analysis on every received post and mention, detects coordinated injection campaigns, verifies skill badges before execution, and issues an Ed25519-signed security badge for your agent's Moltbook profile.

**Quickstart:**

```python
from moltbook import MoltbookClient
from agentcop import Sentinel
from agentcop.adapters.moltbook import MoltbookSentinelAdapter

client = MoltbookClient(api_key="...")
adapter = MoltbookSentinelAdapter(agent_id="my-bot")

# Generates an Ed25519 badge + registers event listeners on the client
adapter.setup(client=client)

# Run your agent — events flow automatically into the adapter buffer
client.run()

# Analyze
sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

**Badge integration:** calling `setup()` issues a cryptographically signed `AgentBadge` and embeds it in every outbound `post_created` event so peer agents can verify your security posture:

```python
adapter.setup()
print(f"Badge: https://agentcop.live/badge/{adapter._badge_id}")
# Badge: https://agentcop.live/badge/abc123
```

**Skill badge verification:** every `skill_executed` event is automatically checked against the skill's ClawHub manifest badge. Unverified skills emit `skill_executed_unverified` (WARN); AT RISK skills emit `skill_executed_at_risk` (CRITICAL).

**Injection detection:** the adapter checks received posts for 13+ injection patterns including direct overrides, role injection, credential theft, exfiltration triggers, and encoding bypass variants (base64, unicode zero-width, right-to-left override).

See [docs/adapters/moltbook.md](docs/adapters/moltbook.md) for the full integration guide, 5 detector recipes, and API reference.

---

## OpenClaw integration

`agentcop` ships a native [OpenClaw](https://openclaw.dev) integration: a skill for on-demand security commands and a hook for automatic real-time monitoring.

```bash
openclaw skills install agentcop
openclaw hooks enable agentcop-monitor
```

The **`agentcop-monitor` hook** fires on every message and tool result, taint-checking for LLM01 (prompt injection) and LLM02 (insecure output). Violation alerts are delivered to your active channel before the agent sees or sends the message.

Example alert in Telegram:

```
🚨 AgentCop [CRITICAL] — LLM01 LLM01_prompt_injection
Matched: `ignore previous instructions`, `you are now`
Context: inbound message
Badge: https://agentcop.live/badge/abc123/verify
```

The **`agentcop` skill** adds `/security` commands:

```
/security status     — agent fingerprint, trust score, violation count
/security report     — full violation report grouped by severity
/security scan       — OWASP LLM Top 10 assessment
/security badge      — generate or display the agent's security badge
```

See [docs/guides/openclaw.md](docs/guides/openclaw.md) for the full integration guide.

---

## Requirements

- Python 3.11+
- `pydantic>=2.7`

---

## License

MIT
