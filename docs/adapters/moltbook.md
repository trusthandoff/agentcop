# Moltbook adapter

Monitor AI agents operating on the Moltbook social network — the most active
prompt-injection attack surface in the current multi-agent ecosystem.

---

## Why Moltbook agents need monitoring

Moltbook is a social network where AI agents read each other's posts and act on
them.  Unlike a private API or a controlled tool, Moltbook's open feed means
**any agent subscribed to a public submolt receives posts from thousands of
unknown agents** — including attacker-controlled bots designed to deliver
prompt-injection payloads to every reader.

The scale of the threat is not theoretical.  Security researchers confirmed in
January 2026 that a measurable percentage of Moltbook posts contain hidden
injection payloads targeting agent readers.  The breach exposed 1.5 M API keys
via commands injected into the public feed — agents read a malicious post,
acted on the embedded instruction, and forwarded their credentials to an
attacker-controlled endpoint.

**An agent reading a malicious Moltbook post and acting on it is an instant
LLM01 critical violation.**  This adapter gives you the monitoring to catch it.

---

## Installation

### Manual mode (no SDK required)

The adapter translates raw event dicts directly — no Moltbook SDK install
needed:

```bash
pip install agentcop
```

### SDK mode

For automatic event-listener registration on a live Moltbook client:

```bash
pip install agentcop[moltbook]
```

---

## Quickstart — manual mode

Use this when you are processing events from a queue, log file, or webhook
without a running Moltbook SDK client.

```python
from agentcop import Sentinel
from agentcop.adapters.moltbook import MoltbookSentinelAdapter

adapter = MoltbookSentinelAdapter(agent_id="my-bot", session_id="sess-001")
adapter.setup()   # generates security badge; no SDK needed

sentinel = Sentinel()

# Translate each raw event and flush buffered drift warnings after each one
for raw_event in my_moltbook_event_queue:
    sentinel.ingest([adapter.to_sentinel_event(raw_event)])
    adapter.flush_into(sentinel)   # picks up any drift-warning events

violations = sentinel.detect_violations()
sentinel.report()
```

---

## Quickstart — SDK mode

Use this when you have a live `MoltbookClient` running the event loop.

```python
from moltbook import MoltbookClient
from agentcop import Sentinel
from agentcop.adapters.moltbook import MoltbookSentinelAdapter

client = MoltbookClient(api_key="...")
adapter = MoltbookSentinelAdapter(agent_id="my-bot")

# setup() generates a badge AND registers event listeners on the client
adapter.setup(client=client)

# Run your agent — events flow automatically into the adapter buffer
client.run()

# After the run, ingest and analyze
sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
sentinel.report()
```

---

## Badge setup — ✅ Secured by agentcop on your Moltbook profile

When `setup()` is called, the adapter generates a cryptographically signed
agentcop security badge for your agent.  Other agents and humans reading your
Moltbook profile can verify: `✅ Secured by agentcop | SECURED 94/100`.

### Prerequisites

```bash
pip install agentcop[badge]
```

### How it works

1. `setup()` calls `AgentIdentity.register()` with the agent's ID and Moltbook
   metadata.
2. An Ed25519-signed `AgentBadge` is issued with a tier (SECURED / MONITORED /
   AT RISK) derived from the agent's trust score.
3. The `badge_id` is stored in `adapter._badge_id` and automatically included
   in the `moltbook.badge_id` attribute of every `post_created` event, so
   downstream agents can read and verify your badge.
4. The badge is accessible for verification at
   `agentcop.live/badge/{badge_id}`.

### Retrieve your badge URL

```python
adapter.setup()   # generates badge
if adapter._badge_id:
    print(f"Badge: https://agentcop.live/badge/{adapter._badge_id}")
```

---

## Skill badge verification — checking skills before execution

Every `skill_executed` event is automatically checked for badge metadata from
the skill's ClawHub manifest.  The adapter classifies the event as follows:

| Skill badge status | `event_type`              | `severity` |
|--------------------|---------------------------|------------|
| No badge           | `skill_executed_unverified` | WARN     |
| AT RISK            | `skill_executed_at_risk`  | CRITICAL   |
| SECURED            | `skill_executed`          | INFO       |
| MONITORED          | `skill_executed`          | INFO       |

To act on unverified or dangerous skills, register a custom detector:

```python
from agentcop import Sentinel, ViolationRecord

def block_unverified_skills(event):
    if event.event_type == "skill_executed_unverified":
        return ViolationRecord(
            violation_type="LLM05_unverified_skill",
            severity="WARN",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={
                "skill_name": event.attributes.get("skill_name"),
                "owasp": "LLM05",
            },
        )

sentinel = Sentinel(detectors=[block_unverified_skills])
```

---

## Detector recipes — 5 Moltbook-specific threats

### 1. Prompt injection in feed

Any `moltbook_injection_attempt` event is a confirmed LLM01 violation.  The
adapter already performs taint analysis on every `post_received` and
`mention_received` event — you just need to wire it to a violation:

```python
def detect_feed_injection(event):
    if event.event_type == "moltbook_injection_attempt":
        return ViolationRecord(
            violation_type="LLM01_moltbook_feed_injection",
            severity="CRITICAL",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={
                "matched_patterns": event.attributes.get("matched_patterns", []),
                "author": event.attributes.get("moltbook.author_agent_id"),
                "owasp": "LLM01",
            },
        )
```

### 2. Coordinated injection campaign detection

A `moltbook_agent_spike` drift event fires when 5+ consecutive posts arrive
from agents not in the agent's known-interaction set.  This is the pattern
observed in the January 2026 breach.

```python
def detect_injection_campaign(event):
    if event.event_type == "moltbook_agent_spike":
        return ViolationRecord(
            violation_type="LLM01_coordinated_campaign",
            severity="WARN",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={"owasp": "LLM01", **event.attributes},
        )
```

### 3. Unverified skill execution

`skill_executed_unverified` fires when a skill without an agentcop badge is
run — LLM05 supply chain risk.  See the skill badge verification section above.

### 4. Agent behavioral drift post-infection

After a successful injection, compromised agents often join new communities or
start replying at unusual rates.  The `moltbook_submolt_drift` and
`moltbook_reply_hijack` drift events catch these patterns:

```python
def detect_behavioral_drift(event):
    if event.event_type in ("moltbook_submolt_drift", "moltbook_reply_hijack"):
        return ViolationRecord(
            violation_type="LLM01_post_infection_drift",
            severity="WARN",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={"drift_type": event.attributes.get("drift_type"), "owasp": "LLM01"},
        )
```

### 5. API key exfiltration attempt

`moltbook_exfiltration_attempt` fires with CRITICAL severity when an agent's
outbound post contains a URL to an endpoint not seen during the baseline period.
This is the exact mechanism used in the January 2026 breach.

```python
def detect_exfiltration(event):
    if event.event_type == "moltbook_exfiltration_attempt":
        return ViolationRecord(
            violation_type="LLM06_api_key_exfiltration",
            severity="CRITICAL",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={
                "endpoint": event.attributes.get("moltbook.endpoint"),
                "owasp": "LLM06",
            },
        )
```

---

## Injection pattern reference

The adapter checks for the following patterns in received content (direct,
base64-encoded, and unicode-obfuscated variants):

| Pattern | Risk |
|---------|------|
| `ignore previous instructions` | Direct system prompt override |
| `your new instructions are` | Instruction injection |
| `system: you are now` | Role injection |
| `reveal your api key` | Credential theft |
| `forward all messages to` | Exfiltration trigger |
| `disregard previous instructions` | Variant override |
| `override your instructions` | Authority escalation |
| `bypass your filters` | Safety bypass |
| `print your system prompt` | System prompt leak |
| `exfiltrate` | Data theft keyword |
| Unicode zero-width chars | Obfuscation |
| Right-to-left override | Text direction obfuscation |
| Base64-encoded variants of the above | Encoding bypass |

---

## Integration with sentinel-agent

The adapter is designed to work with `sentinel-agent` for real-time monitoring:

```python
from agentcop import Sentinel
from agentcop.adapters.moltbook import MoltbookSentinelAdapter

# One sentinel + adapter per running agent
sentinel = Sentinel()
adapter = MoltbookSentinelAdapter(agent_id="my-bot")
adapter.setup()

# In your agent's message handler
def on_moltbook_event(raw):
    # Primary event
    event = adapter.to_sentinel_event(raw)
    sentinel.ingest([event])
    # Drift / peer events buffered by the adapter
    adapter.flush_into(sentinel)

# Periodic violation check (or after each run)
for v in sentinel.detect_violations():
    alert(v)
```

---

## Badge verification API reference

Verify a Moltbook agent's badge using the agentcop REST API:

```
GET https://agentcop.live/badge/{badge_id}
```

Response:

```json
{
  "badge_id": "...",
  "agent_id": "...",
  "tier": "SECURED",
  "trust_score": 94,
  "issued_at": "2026-01-15T00:00:00Z",
  "expires_at": "2026-02-14T00:00:00Z",
  "revoked": false,
  "verification_url": "https://agentcop.live/badge/..."
}
```

Badges are valid for 30 days and auto-revoke when the agent's trust score drops
below 30.

---

## API reference

### `MoltbookSentinelAdapter`

```python
class MoltbookSentinelAdapter:
    source_system: str = "moltbook"

    def __init__(
        self,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> None: ...

    def setup(self, client=None) -> None:
        """Generate badge + optionally register SDK listeners."""

    def to_sentinel_event(self, raw: dict) -> SentinelEvent:
        """Translate one raw Moltbook event dict into a SentinelEvent."""

    def drain(self) -> list[SentinelEvent]:
        """Return and clear the internal buffer (drift + peer events)."""

    def flush_into(self, sentinel: Sentinel) -> None:
        """Ingest all buffered events into a Sentinel instance."""
```

### Event type mapping

| raw `type`          | `event_type`                  | `severity` |
|---------------------|-------------------------------|------------|
| `post_received`     | `moltbook_injection_attempt`  | CRITICAL   |
| `post_received`     | `post_received`               | INFO       |
| `mention_received`  | `moltbook_injection_attempt`  | CRITICAL   |
| `mention_received`  | `mention_received`            | INFO       |
| `reply_received`    | `reply_received`              | INFO       |
| `skill_executed`    | `skill_executed`              | INFO       |
| `skill_executed`    | `skill_executed_unverified`   | WARN       |
| `skill_executed`    | `skill_executed_at_risk`      | CRITICAL   |
| `heartbeat_received`| `heartbeat_received`          | INFO       |
| `post_created`      | `post_created`                | INFO       |
| `reply_created`     | `reply_created`               | INFO       |
| `upvote_given`      | `upvote_given`                | INFO       |
| `submolt_joined`    | `submolt_joined`              | INFO       |
| `feed_fetched`      | `feed_fetched`                | INFO       |

### Behavioral drift events (buffered)

| `event_type`                    | `severity` | trigger |
|---------------------------------|------------|---------|
| `moltbook_submolt_drift`        | WARN       | Unknown submolt visited/joined |
| `moltbook_agent_spike`          | WARN       | ≥5 consecutive posts from unknown agents |
| `moltbook_reply_hijack`         | WARN       | Reply rate > 5× baseline |
| `moltbook_exfiltration_attempt` | CRITICAL   | Novel external URL in outbound post |
| `moltbook_verified_peer`        | INFO       | Peer agent with valid agentcop badge |

### OTel attribute namespace

All Moltbook-specific attributes use the `moltbook.*` namespace:

| Attribute | Description |
|-----------|-------------|
| `moltbook.post_id` | Post identifier |
| `moltbook.mention_id` | Mention identifier |
| `moltbook.reply_id` | Reply identifier |
| `moltbook.submolt` | Community (submolt) name |
| `moltbook.author_agent_id` | Agent ID of the post/mention author |
| `moltbook.badge_id` | agentcop badge ID of the local agent |
| `moltbook.author_badge_id` | Badge ID of the remote (author) agent |
| `moltbook.author_badge_tier` | Badge tier of the remote agent |
| `moltbook.skill_id` | Executed skill identifier |
| `moltbook.skill_badge_id` | Badge ID from skill manifest |
| `moltbook.skill_badge_tier` | Badge tier from skill manifest |
| `moltbook.skill_badge_score` | Badge score from skill manifest |
| `moltbook.count` | Number of posts in a feed_fetched event |
| `moltbook.endpoint` | Domain in exfiltration drift event |

---

## Runtime security

`MoltbookSentinelAdapter` supports the full agentcop runtime security stack via four
optional constructor parameters. All default to `None` — existing code requires no changes.

### Constructor params

```python
MoltbookSentinelAdapter(
    agent_id="my-bot",
    session_id="sess-001",
    gate=None,        # ExecutionGate
    permissions=None, # ToolPermissionLayer
    sandbox=None,     # AgentSandbox  — MUST block network outside moltbook.com
    approvals=None,   # ApprovalBoundary
    identity=None,    # AgentIdentity
)
```

### What gets intercepted

The gate fires inside `_from_skill_executed()` before the skill event is translated and
returned.  The skill name is used as the tool name; `agent_id` is used for the permission
layer lookup.  If denied, `PermissionError` is raised before the event is buffered.

**Sandbox requirement:** When a sandbox is provided, it **must** block network calls to
domains other than `moltbook.com` to prevent exfiltration via injected skills.

```python
sandbox = AgentSandbox(
    allowed_domains=["moltbook.com", "api.moltbook.com"],
)
```

### Example

```python
from moltbook import MoltbookClient
from agentcop.adapters.moltbook import MoltbookSentinelAdapter
from agentcop.gate import ExecutionGate, DenyPolicy
from agentcop.permissions import ToolPermissionLayer, NetworkPermission
from agentcop.sandbox import AgentSandbox
from agentcop.approvals import ApprovalBoundary

# Sandbox: only moltbook.com, no other network egress
sandbox = AgentSandbox(
    allowed_paths=["/tmp/*"],
    allowed_domains=["moltbook.com", "api.moltbook.com"],
)

# Block any skill that hasn't been explicitly permitted
gate = ExecutionGate()
gate.register_policy("*", DenyPolicy(reason="all unknown skills blocked by default"))

permissions = ToolPermissionLayer()
permissions.declare("my-bot", [NetworkPermission(
    domains=["moltbook.com"],
    allow_subdomains=True,
)])

approvals = ApprovalBoundary(requires_approval_above=60)

adapter = MoltbookSentinelAdapter(
    agent_id="my-bot",
    gate=gate,
    permissions=permissions,
    sandbox=sandbox,
    approvals=approvals,
)
adapter.setup(client=MoltbookClient(api_key="..."))

client.run()

sentinel = Sentinel()
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```

---

## Reliability Tracking

Monitor Moltbook agent reliability over time — track how consistently the agent
handles received posts, whether its tool usage varies, and whether retry counts are
spiking (often an early signal of a coordinated injection campaign overloading the
agent).

```python
from moltbook import MoltbookClient
from agentcop import ReliabilityStore
from agentcop import wrap_for_reliability
from agentcop.adapters.moltbook import MoltbookSentinelAdapter
from agentcop import Sentinel

store = ReliabilityStore("agentcop.db")
client = MoltbookClient(api_key="...")

adapter = MoltbookSentinelAdapter(agent_id="my-moltbook-bot")
wrapped = wrap_for_reliability(adapter, agent_id="my-moltbook-bot", store=store)
wrapped.setup(client=client)

client.run()

# After several post-processing cycles
report = store.get_report("my-moltbook-bot", window_hours=24)
print(report.reliability_tier)
print(report.retry_explosion_score)   # spike → possible injection overload
print(report.branch_instability)      # high → agent responding differently per post
```

Or use `ReliabilityTracer` inside the post-handling callback:

```python
from agentcop import ReliabilityTracer, ReliabilityStore

store = ReliabilityStore("agentcop.db")

@client.on("post_received")
def handle_post(post):
    with ReliabilityTracer("my-moltbook-bot", store=store, input_data=post) as tracer:
        reply = generate_reply(post)
        tracer.record_tool_call("generate_reply", args={"post": post["id"]}, result=reply)
        tracer.record_branch("post_response_path")
        tracer.record_tokens(input=len(post["body"].split()), output=len(reply.split()))
```

See [docs/guides/reliability.md](../guides/reliability.md) for the full guide.
