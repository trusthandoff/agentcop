[English](README.md) | [中文](README.zh.md)

<p align="center">
  <img src="https://raw.githubusercontent.com/trusthandoff/agentcop/main/docs/logo.png" alt="agentcop" width="120" />
</p>

<div align="center">

# agentcop — The Agent Cop

[![CI](https://github.com/trusthandoff/agentcop/actions/workflows/test.yml/badge.svg)](https://github.com/trusthandoff/agentcop/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/agentcop)](https://pypi.org/project/agentcop/)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://pypi.org/project/agentcop/)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://pypi.org/project/agentcop/)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://pypi.org/project/agentcop/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![𝕏 @theagentcop](https://img.shields.io/badge/%F0%9D%95%8F-@theagentcop-1DA1F2?logo=twitter&logoColor=white)](https://twitter.com/theagentcop)
[![Moltbook](https://img.shields.io/badge/Moltbook-%F0%9F%A6%9E-8B5CF6)](https://www.moltbook.com/u/sentinel_theagentcop)
[![website](https://img.shields.io/badge/website-agentcop.live-green)](https://agentcop.live)
[![docs](https://img.shields.io/badge/docs-docs.agentcop.live-blue)](https://docs.agentcop.live)
[![YouTube](https://img.shields.io/badge/YouTube-%40agenthijacks-red?logo=youtube&logoColor=white)](https://youtube.com/@agenthijacks)
[![TikTok](https://img.shields.io/badge/TikTok-%40agent__hijacks-black?logo=tiktok&logoColor=white)](https://www.tiktok.com/@agent_hijacks)

</div>

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
- **Runtime Security Layer** — four composable enforcement layers: `ExecutionGate` (policy-based tool execution with SQLite audit log), `ToolPermissionLayer` (declarative capability scoping, deny by default), `AgentSandbox` (runtime isolation with active syscall interception), `ApprovalBoundary` (human-in-the-loop for high-risk actions). `AgentCop.protect()` chains all four in one line.
- **Reliability Layer** — five-metric reliability scoring (path entropy, tool variance, retry explosion, branch instability, token budget), SQLite-backed run history, predictive alerts via OLS regression, K-means++ cross-agent clustering, Prometheus export, and a combined badge format: `✅ SECURED 94/100 | 🟢 STABLE 87/100`
- **TrustChain Layer** — 13-module cryptographic trust chain: SHA-256-linked execution nodes, Ed25519 attestation, tool boundary enforcement, provenance tracking, context/memory poisoning detection, agent hierarchy, cross-runtime portability, and OTel/LangSmith/Datadog export. All 10 adapters updated with optional trust params. 🔐 **CHAIN VERIFIED**
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

### Runtime security params (v0.4.8+)

Every adapter accepts four optional runtime security parameters:

```python
adapter = LangGraphSentinelAdapter(      # same for all adapters
    thread_id="run-abc",                 # framework-specific params unchanged
    gate=ExecutionGate(),                # policy-based allow/deny per tool call
    permissions=ToolPermissionLayer(),   # capability scoping per agent, deny by default
    sandbox=AgentSandbox(...),           # path/domain/syscall enforcement
    approvals=ApprovalBoundary(...),     # human-in-the-loop for high-risk actions
    identity=AgentIdentity(...),         # trust_score auto-tunes gate strictness
)
```

All parameters default to `None` — existing code requires no changes.  See the
[runtime security guide](docs/guides/runtime-security.md) for full details.

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

## Runtime Security Layer

`agentcop` v0.4.7 ships a runtime enforcement stack: four composable layers that intercept, gate, and sandbox agent tool calls before they execute. Drop it in front of any agent object with a single line.

```
pip install agentcop[runtime]
```

### One-line protection

```python
from agentcop.cop import AgentCop
from agentcop.gate import ExecutionGate
from agentcop.permissions import ToolPermissionLayer, NetworkPermission, ReadPermission
from agentcop.sandbox import AgentSandbox
from agentcop.approvals import ApprovalBoundary

gate = ExecutionGate(db_path="agentcop_gate.db")
permissions = ToolPermissionLayer()
permissions.declare("my-agent", [
    ReadPermission(paths=["/data/*", "/tmp/*"]),
    NetworkPermission(domains=["api.openai.com"], allow_subdomains=True),
])
sandbox = AgentSandbox(allowed_paths=["/data/*", "/tmp/*"], allowed_domains=["api.openai.com"])
approvals = ApprovalBoundary(requires_approval_above=70, channels=["cli"], timeout=300)

cop = AgentCop(
    gate=gate,
    permissions=permissions,
    sandbox=sandbox,
    approvals=approvals,
)

# Wrap any agent object — run() goes through the full enforcement pipeline
protected = cop.protect(your_agent)
result = protected.run(task)
```

Each `protected.run()` call passes through five stages in order:

1. **Trust guard** — blocks if `AgentIdentity` trust score < 30
2. **ExecutionGate** — evaluates registered tool policy, logs decision to SQLite
3. **ToolPermissionLayer** — enforces declared capability scope (deny by default)
4. **ApprovalBoundary** — requests human sign-off above the risk threshold
5. **AgentSandbox** — wraps the call with active syscall interception

### ExecutionGate

Policy-based execution control with a persistent audit log.

```python
from agentcop.gate import ExecutionGate, DenyPolicy, RateLimitPolicy, ConditionalPolicy

gate = ExecutionGate(db_path="agentcop_gate.db")

# Hard-deny shell access
gate.register_policy("shell_exec", DenyPolicy(reason="shell access prohibited"))

# Rate-limit web search to 10 calls per minute
gate.register_policy("web_search", RateLimitPolicy(max_calls=10, window_seconds=60))

# Allow file writes only to /tmp
gate.register_policy(
    "file_write",
    ConditionalPolicy(
        allow_if=lambda args: str(args.get("path", "")).startswith("/tmp/"),
        deny_reason="writes outside /tmp are not permitted",
    ),
)

# Use as a decorator
@gate.wrap
def my_tool(path: str) -> str:
    ...

# Audit log
for entry in gate.decision_log(limit=50):
    print(entry["tool"], entry["allowed"], entry["reason"])
```

### ToolPermissionLayer

Declare what each agent is allowed to do — everything else is denied by default.

```python
from agentcop.permissions import (
    ToolPermissionLayer,
    ReadPermission, WritePermission,
    NetworkPermission, ExecutePermission,
)

layer = ToolPermissionLayer()

layer.declare("data-pipeline-agent", [
    ReadPermission(paths=["/data/*", "/tmp/*"]),
    WritePermission(paths=["/tmp/*"]),
    NetworkPermission(domains=["api.openai.com"], allow_subdomains=True),
])

result = layer.verify("data-pipeline-agent", "file_write", {"path": "/etc/shadow"})
# PermissionResult(granted=False, reason='path /etc/shadow not in allowed paths')

# Attach to a gate to enforce automatically on every call
layer.attach_to_gate(gate, agent_id="data-pipeline-agent")
```

### AgentSandbox

Wraps agent execution with active syscall interception — patches `builtins.open`, `urllib.request.urlopen`, `subprocess.run`, and `requests.Session.request` while active.

```python
from agentcop.sandbox import AgentSandbox

sandbox = AgentSandbox(
    intercept_syscalls=True,
    allowed_paths=["/tmp/*", "/data/read-only/*"],
    allowed_domains=["api.openai.com"],
    max_execution_time=30,   # raises SandboxTimeoutError if exceeded
)

with sandbox:
    result = your_agent.run(task)
    # open() to a path outside allowed_paths → SandboxViolation
    # HTTP to a domain outside allowed_domains → SandboxViolation
```

### ApprovalBoundary

Human-in-the-loop gate for high-risk actions. Auto-approves below the threshold, holds and notifies above it.

```python
from agentcop.approvals import ApprovalBoundary

boundary = ApprovalBoundary(
    requires_approval_above=70,
    channels=["cli"],          # "cli", "webhook", "slack", or dict with "type"+"url"
    timeout=300,               # auto-deny after 5 minutes
    db_path="approvals.db",    # persistent audit trail
)

request = boundary.submit("delete_database", {"db": "prod"}, risk_score=90)
# → dispatches to configured channels, blocks waiting for decision

# From another thread or external webhook:
boundary.approve(request.request_id, actor="alice", reason="confirmed safe migration")

resolved = boundary.wait_for_decision(request.request_id)
# ApprovalRequest(status='approved', ...)
```

### RUNTIME PROTECTED badge

Agents running the full `AgentCop` stack earn the **RUNTIME PROTECTED** designation. Pass blocked violation counts under `"protected"` in the badge payload — a non-zero value renders the shield with the annotation and signals that violations were intercepted at runtime, not just detected after the fact.

```python
badge = issuer.issue(
    agent_id="my-agent",
    fingerprint=identity.fingerprint,
    trust_score=92.0,
    violations={"critical": 0, "warning": 0, "info": 3, "protected": 7},
    framework="langgraph",
    scan_count=88,
)
```

See [docs/guides/runtime-security.md](docs/guides/runtime-security.md) for the complete guide including CLI reference, channel setup, and identity integration.

---

## Reliability Layer

`agentcop` v0.4.10 ships a statistical reliability engine that turns raw run history into actionable reliability scores, predictive alerts, and cross-agent cluster analysis — all with zero ML dependencies.

### What it measures

| Metric | Description | Weight |
|---|---|---|
| Path entropy | Shannon entropy of execution paths — high entropy means unpredictable branching | 25% |
| Tool variance | Coefficient of variation in tool usage across runs | 25% |
| Retry explosion | Normalized score from retry counts and velocity | 30% |
| Branch instability | Hamming distance between execution paths for the same input | 20% |
| Token budget | Per-run token consumption vs baseline — emits spike alerts at 3× | informational |

All four weighted metrics combine into a **reliability score (0–100)** and a tier:

| Tier | Score | Badge |
|---|---|---|
| 🟢 STABLE | ≥ 80 | |
| 🟡 VARIABLE | 60–79 | |
| 🟠 UNSTABLE | 40–59 | |
| 🔴 CRITICAL | < 40 | |

### Quick example

```python
from agentcop import ReliabilityTracer, ReliabilityStore

store = ReliabilityStore("agentcop.db")

with ReliabilityTracer("my-agent", store=store) as tracer:
    tracer.record_tool_call("bash", args={"cmd": "ls"}, result="file1.txt")
    tracer.record_branch("chose_path_A")
    tracer.record_tokens(input=100, output=250, model="gpt-4o")

# After several runs, fetch the report
from agentcop.reliability import ReliabilityEngine
from agentcop.reliability.store import ReliabilityStore

store = ReliabilityStore("agentcop.db")
report = store.get_report("my-agent", window_hours=24)
print(report.reliability_tier)   # STABLE | VARIABLE | UNSTABLE | CRITICAL
print(report.reliability_score)  # 0-100
```

### Combined badge

Security trust + reliability are displayed together:

```
✅ SECURED 94/100 | 🟢 STABLE 87/100
```

Generate it programmatically:

```python
from agentcop.reliability.badge_integration import combined_badge_text

text = combined_badge_text(trust_score=94, reliability_score=87, reliability_tier="STABLE")
# → "✅ SECURED 94/100 | 🟢 STABLE 87/100"
```

### CLI

```bash
# Per-agent report
agentcop reliability report --agent my-agent --verbose

# Side-by-side leaderboard
agentcop reliability compare --agents agent-a agent-b agent-c

# Live refresh (Ctrl-C to stop)
agentcop reliability watch --agent my-agent --interval 10

# Export as JSON or Prometheus metrics
agentcop reliability export --agent my-agent --format prometheus
agentcop reliability export --agents agent-a agent-b --format json -o report.json
```

### AgentIdentity integration

`record_run()` updates trust score automatically based on tier:

```python
identity.record_run(run)          # STABLE +0 | VARIABLE −5 | UNSTABLE −15 | CRITICAL −30
print(identity.reliability_tier)  # "STABLE"
print(identity.reliability_score) # 87
```

### Predictive alerts

Linear regression over the last N runs fires a `SentinelEvent` before metrics breach a threshold:

```python
from agentcop.reliability import ReliabilityPredictor

predictor = ReliabilityPredictor()
predictions = predictor.predict(runs, horizon_hours=2.0)
for pred in predictions:
    if pred.sentinel_event:
        sentinel.push(pred.sentinel_event)
    # → "WARNING: retry_count likely to exceed threshold (3.0) — ..."
```

### Prometheus export

```python
from agentcop.reliability import PrometheusExporter

exporter = PrometheusExporter(store)
print(exporter.export(["agent-a", "agent-b"]))
# agentcop_reliability_score{agent_id="agent-a"} 87.0
# agentcop_path_entropy{agent_id="agent-a"} 0.12
# agentcop_tool_variance{agent_id="agent-a"} 0.08
# ... (8 gauges per agent)
```

---

## TrustChain Layer

`agentcop` v0.4.11 ships a cryptographic trust chain that verifies every step in a multi-agent execution. Each node — tool call, agent handoff, RAG lookup, memory read — is hashed and linked to the previous one. A broken link means something changed without authorisation. Thirteen modules, zero new mandatory dependencies, Ed25519 signing when the `cryptography` package is present and hash-only mode otherwise.

🔐 **CHAIN VERIFIED**

### Quick example

```python
from agentcop.trust import TrustChainBuilder, ExecutionNode

with TrustChainBuilder(agent_id="orchestrator") as chain:
    node = ExecutionNode(
        node_id="step-1",
        agent_id="orchestrator",
        tool_calls=["web_search"],
        context_hash="abc123",
        output_hash="def456",
        duration_ms=320,
    )
    claim = chain.add_node(node)

result = chain.verify_chain()
print(result.verified)   # True
print(chain.export_chain("compact"))
# orchestrator→step-1 [hash:a1b2c3d4] [verified:true]
```

### Multi-agent hierarchy

```python
from agentcop.trust import AgentHierarchy

hierarchy = AgentHierarchy(sentinel=sentinel)
hierarchy.define(
    supervisor="orchestrator",
    workers=["researcher", "writer"],
    can_delegate=True,
    max_depth=3,
    final_decision_authority="orchestrator",
)

# Any adapter enforces hierarchy automatically
from agentcop.adapters.langgraph import LangGraphSentinelAdapter

adapter = LangGraphSentinelAdapter(
    thread_id="run-abc",
    hierarchy=hierarchy,   # raises PermissionError on unauthorised handoffs
)
```

### Modules

| Module | What it does |
|---|---|
| `agentcop.trust.chain` | SHA-256-linked `TrustChainBuilder` — add nodes, verify the chain, export |
| `agentcop.trust.models` | Pure-dataclass value objects: `TrustClaim`, `TrustChain`, `ExecutionNode` |
| `agentcop.trust.attestation` | Ed25519 signing via `cryptography`; hash-only fallback when absent |
| `agentcop.trust.boundaries` | `ToolTrustBoundary` — declare and enforce tool-to-tool data-flow rules |
| `agentcop.trust.provenance` | `ProvenanceTracker` — record instruction origins, detect spoofed `user` claims |
| `agentcop.trust.lineage` | `ExecutionLineage` — ordered step log, diff, JSON/Mermaid/text export |
| `agentcop.trust.context_guard` | `ContextGuard` — snapshot context hashes, detect injection-pattern mutation |
| `agentcop.trust.rag_trust` | `RAGTrustLayer` — per-document trust registry, poisoning detection |
| `agentcop.trust.memory_guard` | `MemoryGuard` — hash-based memory integrity, poisoning pattern matching |
| `agentcop.trust.hierarchy` | `AgentHierarchy` — supervisor/worker delegation graph, veto rights, quorum |
| `agentcop.trust.interop` | `TrustInterop` — portable `agentcop.trust.v1.*` claims, OpenAI/Anthropic formats |
| `agentcop.trust.observability` | `TrustObserver` — OTel spans, LangSmith runs, Datadog traces, Prometheus |
| `agentcop.trust` (package) | Re-exports all 28 public symbols; `RAGPoisoningAlert` and `MemoryPoisoningAlert` aliases |

### Adapter trust params (v0.4.11+)

All 10 adapters now accept optional trust parameters:

```python
# Agent adapters (LangGraph, AutoGen, CrewAI, Haystack, LlamaIndex, Semantic Kernel)
adapter = LangGraphSentinelAdapter(
    thread_id="run-abc",
    trust=TrustChainBuilder(agent_id="my-graph"),  # records every completed node
    attestor=NodeAttestor(private_key_pem=key),     # signs each claim (optional)
    hierarchy=AgentHierarchy(...),                  # enforces delegation rules
    trust_interop=TrustInterop(),                   # exports portable claims
)

# Observability adapters (LangSmith, Langfuse, Datadog)
adapter = LangSmithSentinelAdapter(
    trust_observer=TrustObserver(),  # calls record_verified_chain() on run completion
    hierarchy=AgentHierarchy(...),
)

# Moltbook
adapter = MoltbookSentinelAdapter(
    rag_trust=RAGTrustLayer(),       # verifies each received post's submolt source
    trust_observer=TrustObserver(),
    hierarchy=AgentHierarchy(...),
)
```

All parameters default to `None` — existing code requires no changes. See [docs/guides/trust-chain.md](docs/guides/trust-chain.md) for the complete guide.

---

## Requirements

- Python 3.11+
- `pydantic>=2.7`

---

## License

MIT
