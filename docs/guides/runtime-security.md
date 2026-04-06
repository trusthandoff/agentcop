# Runtime Security Layer

`agentcop` v0.4.7 introduces a runtime enforcement stack: four composable layers
that intercept, gate, and sandbox agent tool calls before they execute.

---

## Why runtime enforcement?

Static scanning (badge analysis, SKILL.md audits) tells you what an agent *was*
permitted to do when it was scanned. Runtime enforcement tells you what it *does*
at execution time — and stops it if the answer is wrong.

**Static scanning catches:**
- Undeclared external calls in skill code
- Hardcoded credential exfiltration patterns
- Wildcard permission declarations
- Known injection vectors in prompts at rest

**Runtime enforcement catches:**
- Dynamic calls built from user input at execution time
- Permissions that drift after a scan (new code path, new dependency)
- Unexpected tool invocations from a compromised LLM response
- High-risk actions (destructive writes, bulk deletes) that need a human gate
- Path traversal and domain hopping during a live agent run

The two are complementary. Use static scanning to block unsafe agents from
deploying; use runtime enforcement to contain the blast radius if something slips
through.

---

## Install

```
pip install agentcop[runtime]
```

---

## Full pipeline example

Wire all four layers together and wrap any agent object in one call:

```python
from agentcop.cop import AgentCop
from agentcop.gate import ExecutionGate, DenyPolicy, RateLimitPolicy, ConditionalPolicy
from agentcop.permissions import (
    ToolPermissionLayer,
    ReadPermission, WritePermission,
    NetworkPermission, ExecutePermission,
)
from agentcop.sandbox import AgentSandbox
from agentcop.approvals import ApprovalBoundary
from agentcop import Sentinel, AgentIdentity, SQLiteIdentityStore

# Identity — trust score feeds the trust guard
store = SQLiteIdentityStore("agentcop.db")
identity = AgentIdentity.register(
    agent_id="pipeline-agent",
    code=agent_function,
    metadata={"framework": "langgraph", "version": "2.0"},
    store=store,
)

# Sentinel — receives SentinelEvents from permission violations + gate denials
sentinel = Sentinel()
sentinel.attach_identity(identity)

# ExecutionGate — policy per tool, SQLite audit log
gate = ExecutionGate(db_path="agentcop_gate.db")
gate.register_policy("shell_exec", DenyPolicy(reason="shell access prohibited"))
gate.register_policy("web_search", RateLimitPolicy(max_calls=20, window_seconds=60))
gate.register_policy(
    "file_write",
    ConditionalPolicy(
        allow_if=lambda args: str(args.get("path", "")).startswith("/tmp/"),
        deny_reason="writes outside /tmp are not permitted",
    ),
)

# ToolPermissionLayer — capability declarations per agent
permissions = ToolPermissionLayer(sentinel=sentinel)
permissions.declare("pipeline-agent", [
    ReadPermission(paths=["/data/*", "/tmp/*"]),
    WritePermission(paths=["/tmp/*"]),
    NetworkPermission(domains=["api.openai.com", "storage.googleapis.com"], allow_subdomains=True),
])

# AgentSandbox — syscall interception
sandbox = AgentSandbox(
    intercept_syscalls=True,
    allowed_paths=["/data/*", "/tmp/*"],
    allowed_domains=["api.openai.com", "storage.googleapis.com"],
    max_execution_time=120,
    permission_layer=permissions,
    agent_id="pipeline-agent",
)

# ApprovalBoundary — human gate for high-risk actions
approvals = ApprovalBoundary(
    requires_approval_above=70,
    channels=["cli", {"type": "slack", "url": "https://hooks.slack.com/..."}],
    timeout=300,
    db_path="approvals.db",
)

# AgentCop — chains everything
cop = AgentCop(
    gate=gate,
    permissions=permissions,
    sandbox=sandbox,
    approvals=approvals,
    sentinel=sentinel,
    agent_id="pipeline-agent",
    identity=identity,
)

# One line to protect any agent
protected = cop.protect(your_agent)

# All five stages fire on every call
result = protected.run(task)
```

### Enforcement pipeline stages

Each `protected.run()` call passes through five stages in order:

| Stage | Layer | Blocks when |
|-------|-------|-------------|
| 1 | **Trust guard** | `AgentIdentity` trust score < 30 |
| 2 | **ExecutionGate** | Registered policy returns `allowed=False` |
| 3 | **ToolPermissionLayer** | No declared permission covers the tool, or scope check fails |
| 4 | **ApprovalBoundary** | Risk score > threshold and approval is denied or times out |
| 5 | **AgentSandbox** | Syscall violates path/domain/output constraints |

Any stage can raise a `PermissionError` or `ApprovalDenied` exception. The call
never reaches the underlying agent if any stage blocks.

---

## ExecutionGate

### Quickstart

```python
from agentcop.gate import ExecutionGate

gate = ExecutionGate(db_path="agentcop_gate.db")
```

`db_path` defaults to `"agentcop_gate.db"` in the current directory. Pass
`":memory:"` for a non-persistent gate (useful in tests).

### Policy types

#### AllowPolicy

Always allows the tool call. Useful as an explicit opt-in when other tools default to deny.

```python
from agentcop.gate import AllowPolicy

gate.register_policy("safe_read", AllowPolicy())
```

#### DenyPolicy

Always blocks. Use for tools that must never be called regardless of arguments.

```python
from agentcop.gate import DenyPolicy

gate.register_policy("shell_exec", DenyPolicy(reason="shell access prohibited"))
gate.register_policy("drop_table", DenyPolicy(reason="destructive SQL blocked"))
```

#### ConditionalPolicy

Allow or deny based on a predicate over the call's `args` dict. The predicate
receives the full `args` dict and must return `True` to allow.

```python
from agentcop.gate import ConditionalPolicy

# Allow file writes only to /tmp
gate.register_policy(
    "file_write",
    ConditionalPolicy(
        allow_if=lambda args: str(args.get("path", "")).startswith("/tmp/"),
        deny_reason="writes outside /tmp are not permitted",
        risk_score_if_denied=80,
    ),
)

# Allow API calls only to a known base URL
gate.register_policy(
    "api_call",
    ConditionalPolicy(
        allow_if=lambda args: str(args.get("url", "")).startswith("https://api.example.com/"),
        deny_reason="calls to undeclared endpoints not permitted",
    ),
)
```

#### RateLimitPolicy

Sliding-window rate limiter. Thread-safe. Each tool name gets its own counter.

```python
from agentcop.gate import RateLimitPolicy

# Max 10 web searches per minute
gate.register_policy(
    "web_search",
    RateLimitPolicy(max_calls=10, window_seconds=60.0, deny_reason="search rate limit exceeded"),
)

# Max 3 database writes per second
gate.register_policy(
    "db_write",
    RateLimitPolicy(max_calls=3, window_seconds=1.0),
)
```

### Decorator usage

```python
@gate.wrap
def send_email(to: str, subject: str, body: str) -> bool:
    ...
# Raises PermissionError if gate denies "send_email"
```

### Inline check

```python
decision = gate.check("file_write", {"path": "/etc/passwd"})
print(decision.allowed)    # False
print(decision.reason)     # 'writes outside /tmp are not permitted'
print(decision.risk_score) # 80
```

### Audit log

Every decision is written to the `gate_decisions` table in the SQLite database.

```python
for entry in gate.decision_log(limit=100):
    print(entry["timestamp"], entry["tool"], entry["allowed"], entry["reason"])
```

---

## ToolPermissionLayer

### Quickstart

```python
from agentcop.permissions import ToolPermissionLayer

layer = ToolPermissionLayer()
```

Pass a `Sentinel` to emit `permission_violation` events to your audit trail when
a call is denied:

```python
layer = ToolPermissionLayer(sentinel=sentinel)
```

### Permission types

#### ReadPermission

Controls `file_read`, `read_file`, `open_file`, `read`, `get_file`, `load_file`, `cat`.
Path argument extracted from `path`, `file_path`, `file`, or `filename` in args.
Supports fnmatch patterns.

```python
from agentcop.permissions import ReadPermission

ReadPermission(paths=["/data/*", "/tmp/*", "/home/agent/config.yaml"])
```

#### WritePermission

Controls `file_write`, `write_file`, `save_file`, `create_file`, `write`, `put_file`, `append_file`.
Same path matching as `ReadPermission`.

```python
from agentcop.permissions import WritePermission

WritePermission(paths=["/tmp/*"])
```

#### NetworkPermission

Controls `http_get`, `http_post`, `http_request`, `fetch`, `request`, `web_search`, `api_call`, `curl`.
Extracts host from `url`, `endpoint`, `host`, or `domain` in args (full URL parsing via `urllib.parse`).

```python
from agentcop.permissions import NetworkPermission

# Exact domain match
NetworkPermission(domains=["api.openai.com"])

# Include all subdomains (api.openai.com, files.openai.com, ...)
NetworkPermission(domains=["openai.com"], allow_subdomains=True)
```

#### ExecutePermission

Controls `shell_exec`, `run_command`, `execute`, `bash`, `exec`, `subprocess`, `run`.
Compares the leading token of the `command` arg against the allowed list.

```python
from agentcop.permissions import ExecutePermission

ExecutePermission(commands=["python", "pip"])
# Allows: python script.py, pip install foo
# Denies: rm, curl, bash -c "..."
```

#### Custom tool names

Every permission type accepts an optional `tool_names` list to extend or replace
the default covered tools:

```python
ReadPermission(paths=["/data/*"], tool_names=["my_custom_reader", "read_parquet"])
```

### Declaring capabilities

```python
layer.declare("data-pipeline-agent", [
    ReadPermission(paths=["/data/*", "/tmp/*"]),
    WritePermission(paths=["/tmp/*"]),
    NetworkPermission(domains=["api.openai.com"], allow_subdomains=True),
])
```

Calling `declare()` again with the same `agent_id` replaces the previous
declaration.

### Verifying a call

```python
result = layer.verify("data-pipeline-agent", "file_write", {"path": "/etc/shadow"})
# PermissionResult(granted=False, reason='path /etc/shadow not in allowed paths')

result = layer.verify("data-pipeline-agent", "web_search", {"url": "https://api.openai.com/v1/chat"})
# PermissionResult(granted=True, reason='network permitted')
```

Deny-by-default: if no declaration exists for the agent, or no permission covers
the tool name, the call is denied.

### Attaching to ExecutionGate

```python
layer.attach_to_gate(gate, agent_id="data-pipeline-agent")
# Registers a ConditionalPolicy for every tool covered by the declared permissions
```

---

## AgentSandbox

### Quickstart

```python
from agentcop.sandbox import AgentSandbox

sandbox = AgentSandbox(
    intercept_syscalls=True,
    allowed_paths=["/tmp/*", "/data/*"],
    allowed_domains=["api.openai.com"],
    max_execution_time=60,
)

with sandbox:
    result = agent.run(task)
```

`AgentSandbox` is a re-entrant context manager. Nesting is safe.

### What gets intercepted

When `intercept_syscalls=True` (the default), `AgentSandbox` patches the
following on entry and restores them on exit:

| Stdlib symbol | Intercepted when |
|---|---|
| `builtins.open` | Path not in `allowed_paths` |
| `urllib.request.urlopen` | Host not in `allowed_domains` |
| `subprocess.run` | Always blocked (no subprocess allowlist) |
| `requests.Session.request` | Host not in `allowed_domains` (if `requests` installed) |

Any violation raises `SandboxViolation` with a `violation_type` string and a
`detail` dict for debugging.

### SandboxPolicy (validation-only mode)

For lighter-weight validation without patching stdlib, use `ExecutionSandbox`
with an explicit `SandboxPolicy`:

```python
from agentcop.sandbox import ExecutionSandbox, SandboxPolicy

policy = SandboxPolicy(
    allowed_paths=["/data/*", "/tmp/*"],
    denied_paths=["/tmp/secret/"],
    allowed_env_vars=["HOME", "PATH", "OPENAI_API_KEY"],
    denied_env_vars=["AWS_SECRET_ACCESS_KEY"],
    max_output_bytes=1_000_000,   # 1 MB
)

with ExecutionSandbox(policy=policy) as sandbox:
    sandbox.assert_path_allowed("/tmp/output.csv")   # ok
    sandbox.assert_path_allowed("/etc/passwd")        # raises SandboxViolation
    sandbox.assert_env_allowed("AWS_SECRET_ACCESS_KEY")  # raises SandboxViolation
```

### Merging ToolPermissionLayer

Pass a `ToolPermissionLayer` and `agent_id` to automatically derive path and
domain constraints from the agent's declared permissions:

```python
sandbox = AgentSandbox(
    permission_layer=layer,
    agent_id="data-pipeline-agent",
    # allowed_paths and allowed_domains are merged from the declared permissions
)
```

### Timeout

```python
from agentcop.sandbox import AgentSandbox, SandboxTimeoutError

sandbox = AgentSandbox(max_execution_time=10)

try:
    with sandbox:
        slow_agent.run(task)
except SandboxTimeoutError:
    print("agent exceeded 10-second execution budget")
```

---

## ApprovalBoundary

### Quickstart

```python
from agentcop.approvals import ApprovalBoundary

boundary = ApprovalBoundary(
    requires_approval_above=70,
    channels=["cli"],
    timeout=300,
    db_path="approvals.db",
)
```

Calls with `risk_score <= requires_approval_above` are auto-approved. Calls above
the threshold are held pending until a human approves, denies, or the timeout
fires (auto-deny).

### Submitting a request

```python
request = boundary.submit("delete_records", {"table": "users", "filter": "all"}, risk_score=90)
# Returns immediately. Request is "pending" — notification dispatched to channels.
```

### Waiting for a decision

```python
resolved = boundary.wait_for_decision(request.request_id, timeout=300.0)

if resolved.approved:
    proceed()
elif resolved.denied:
    abort(resolved.reason)
```

### Approving and denying

```python
# From any thread — or from a webhook handler
boundary.approve(request.request_id, actor="alice", reason="confirmed safe")
boundary.deny(request.request_id, actor="bob", reason="unsafe in production")
```

### Channel setup

#### CLI

Prints an approval prompt to `stderr`. Unblocks when any `boundary.approve()` or
`boundary.deny()` call resolves the request.

```python
ApprovalBoundary(channels=["cli"])
```

#### Webhook

POSTs a JSON payload to your endpoint. Your handler calls `boundary.approve()` or
`boundary.deny()` via the agentcop API.

```python
ApprovalBoundary(channels=[{"type": "webhook", "url": "https://hooks.example.com/agentcop"}])
```

Payload shape:

```json
{
  "request_id": "uuid4",
  "tool": "delete_records",
  "args": {"table": "users", "filter": "all"},
  "risk_score": 90,
  "created_at": "2026-04-06T10:00:00Z"
}
```

#### Slack

POSTs to a Slack Incoming Webhook URL. Approval is performed programmatically
via `boundary.approve()` / `boundary.deny()` (e.g., triggered by a Slack slash
command or button click that calls your backend).

```python
ApprovalBoundary(channels=[{"type": "slack", "url": "https://hooks.slack.com/services/..."}])
```

#### Multiple channels

```python
ApprovalBoundary(channels=[
    "cli",
    {"type": "slack", "url": "https://hooks.slack.com/services/..."},
    {"type": "webhook", "url": "https://hooks.example.com/approval"},
])
```

### Audit trail

Every approval decision is persisted to SQLite:

```python
# All events for a specific request
for event in boundary.audit_trail(request_id="uuid4"):
    print(event["event_type"], event["actor"], event["reason"], event["ts"])

# Latest 500 events across all requests
for event in boundary.audit_trail(limit=500):
    print(event)
```

### ApprovalGate (in-memory, no SQLite)

For testing or short-lived processes use `ApprovalGate` directly:

```python
from agentcop.approvals import ApprovalGate, AutoDenyPolicy

gate = ApprovalGate(policy=AutoDenyPolicy(risk_threshold=80))

request = gate.request("dangerous_op", {"target": "prod"}, risk_score=95)
# ApprovalRequest(status='denied', ...)

gate.enforce("safe_op", risk_score=30)   # passes
gate.enforce("dangerous_op", risk_score=95)  # raises ApprovalDenied
```

---

## Integration with AgentIdentity

Attach an `AgentIdentity` to `AgentCop` to enable the trust guard. If the
identity's trust score drops below 30 (auto-revocation threshold), `protect()`
blocks all calls immediately — no other layer is consulted.

```python
from agentcop import AgentIdentity, SQLiteIdentityStore

store = SQLiteIdentityStore("agentcop.db")
identity = AgentIdentity.register(
    agent_id="pipeline-agent",
    code=agent_function,
    metadata={"framework": "langgraph"},
    store=store,
)

cop = AgentCop(
    gate=gate,
    permissions=permissions,
    sandbox=sandbox,
    approvals=approvals,
    sentinel=sentinel,
    agent_id="pipeline-agent",
    identity=identity,
)
```

Trust score adjustments happen automatically via the `Sentinel`. Wire it up:

```python
sentinel = Sentinel()
sentinel.attach_identity(identity)
# Every ViolationRecord detected by sentinel.detect_violations() adjusts the score:
# CRITICAL → -20, ERROR → -10, WARN → -5, clean run → +1
```

---

## Integration with the badge system

Pass `"protected"` violation counts to `BadgeIssuer.issue()` to earn the
**RUNTIME PROTECTED** badge annotation. The count should reflect how many calls
were actively intercepted by `AgentCop` during the badge period.

```python
from agentcop.badge import BadgeIssuer, SQLiteBadgeStore

store = SQLiteBadgeStore("agentcop.db")
issuer = BadgeIssuer(store=store)

# Tally intercepted calls from the gate audit log
blocked = sum(1 for e in gate.decision_log(limit=10_000) if not e["allowed"])

badge = issuer.issue(
    agent_id="pipeline-agent",
    fingerprint=identity.fingerprint,
    trust_score=identity.trust_score,
    violations={
        "critical": 0,
        "warning": 2,
        "info": 5,
        "protected": blocked,   # non-zero → RUNTIME PROTECTED annotation
    },
    framework="langgraph",
    scan_count=identity.execution_count,
)

assert issuer.verify(badge)
print(generate_markdown(badge))
# ![AgentCop SECURED](https://agentcop.live/badge/<id>/shield)
```

Badge tiers:

| Tier | Trust score | Color |
|---|---|---|
| 🟢 SECURED | ≥ 80 | `#00ff88` |
| 🟡 MONITORED | 50–79 | `#ffaa00` |
| 🔴 AT RISK | < 50 | `#ff3333` |

A non-zero `protected` count renders the shield with the **RUNTIME PROTECTED**
annotation regardless of tier.

---

## CLI commands reference

```bash
# Scan an agent or skill directory for static violations
agentcop scan ./my-agent/

# Scan and issue a badge
agentcop scan ./my-agent/ --badge

# Show current agent identity and trust score
agentcop status --agent-id pipeline-agent --db agentcop.db

# Print violation report grouped by severity
agentcop report --agent-id pipeline-agent --db agentcop.db

# Verify a badge signature
agentcop badge verify <badge-id>

# Dump gate audit log (last 100 entries)
agentcop gate log --db agentcop_gate.db --limit 100

# Dump approval audit trail
agentcop approvals log --db approvals.db --limit 100

# Dump pending approval requests
agentcop approvals pending --db approvals.db

# Approve a pending request from the CLI
agentcop approvals approve <request-id> --actor alice --reason "confirmed safe"

# Deny a pending request
agentcop approvals deny <request-id> --actor bob --reason "not safe in prod"
```

---

## Putting it all together — example output

Running a protected agent that attempts a write outside `/tmp`:

```
[AgentCop] stage=gate        tool=file_write  allowed=False  reason='writes outside /tmp are not permitted'
[AgentCop] stage=permissions tool=file_write  granted=False  reason='path /var/app/config.yaml not in allowed paths'
PermissionError: file_write blocked by ExecutionGate: writes outside /tmp are not permitted
```

Running a high-risk action that requires approval:

```
[AgentCop] stage=approvals   tool=delete_records  risk_score=90  status=pending
[AgentCop] Awaiting approval via: cli, slack
  → Approve:  boundary.approve("a1b2c3d4", actor="you")
  → Deny:     boundary.deny("a1b2c3d4", actor="you")
  → Timeout in 300s → auto-deny
```

After trust score drops to 28:

```
[AgentCop] stage=trust_guard  trust_score=28  threshold=30
PermissionError: agent trust score 28 is below minimum threshold 30 — execution blocked
```

---

## Adapter integration

As of v0.4.8, all framework adapters accept `gate`, `permissions`, `sandbox`, `approvals`,
and `identity` as optional keyword-only constructor arguments.  All default to `None` for
full backward compatibility.

### Universal pattern

Every adapter follows the same pattern regardless of framework:

```python
from agentcop.adapters.<framework> import <Framework>SentinelAdapter
from agentcop.gate import ExecutionGate, ConditionalPolicy
from agentcop.permissions import ToolPermissionLayer, NetworkPermission
from agentcop.sandbox import AgentSandbox
from agentcop.approvals import ApprovalBoundary

gate = ExecutionGate()
permissions = ToolPermissionLayer()
sandbox = AgentSandbox(allowed_paths=["/tmp/*"], allowed_domains=["api.openai.com"])
approvals = ApprovalBoundary(requires_approval_above=75)

adapter = <Framework>SentinelAdapter(
    gate=gate,
    permissions=permissions,
    sandbox=sandbox,
    approvals=approvals,
)
```

### Framework-specific interception points

| Adapter | Interception point | Blocking? |
|---|---|---|
| **LangGraph** | `iter_events()` for `task` (node start) events | Yes — raises `PermissionError` |
| **CrewAI** | `ToolUsageStartedEvent` bus handler | Yes — raises `PermissionError` |
| **AutoGen** | `_from_function_call_started()` | Yes — raises `PermissionError` |
| **LlamaIndex** | `AgentToolCallEvent` in `setup()` handler | Yes — raises `PermissionError` |
| **Haystack** | `_WrappingTracer.trace()` before component start | Yes — raises `PermissionError` |
| **Semantic Kernel** | `_function_invocation_filter` before `await next()` | Yes — raises `PermissionError` |
| **Moltbook** | `_from_skill_executed()` | Yes — raises `PermissionError` |
| **LangSmith** | `_intercepted_create()` for tool runs | Logged only |
| **Langfuse** | `SpanProcessor.on_start()` for tool observations | Logged only |
| **Datadog** | `_intercepted_write()` for LLM spans | Logged only |

**Blocking adapters** raise `PermissionError` before execution and buffer a
`gate_denied` or `permission_violation` SentinelEvent.

**Logging adapters** (LangSmith, Langfuse, Datadog) are observability wrappers that fire
after or alongside execution.  Gate decisions are logged as SentinelEvents but execution
is not halted — use blocking adapters upstream for enforcement.

### Enforcement order

When all four parameters are provided, the check executes in this order:

1. **`permissions`** — `ToolPermissionLayer.verify(agent_id, tool_name, args)`
   → `PermissionError` + `permission_violation` event on denial
2. **`gate`** — `ExecutionGate.check(tool_name, args, context)`
   → `PermissionError` + `gate_denied` event on denial
3. **`approvals`** — if `gate.risk_score > approvals.requires_approval_above`:
   → `approval_requested` event → `wait_for_decision()` blocks thread
   → `PermissionError` + `gate_denied` event if approval denied

### AgentIdentity trust_score

When `identity` is provided, its `trust_score` (0–100) is forwarded to the gate as
`context["trust_score"]`.  This allows policy predicates to tighten enforcement for
low-trust agents:

```python
from agentcop.identity import AgentIdentity, InMemoryIdentityStore

store = InMemoryIdentityStore()
identity = AgentIdentity.register("agent-1", store=store, metadata={})

gate.register_policy("*", ConditionalPolicy(
    allow_if=lambda args, ctx=None: (ctx or {}).get("trust_score", 100) >= 50,
    deny_reason="agent trust score below threshold",
))

adapter = LangGraphSentinelAdapter(
    gate=gate,
    identity=identity,
)
```

Trust score guidance:
- `trust_score < 50` → restrict: lower rate limits, smaller path allowlists
- `trust_score 50–79` → standard policies
- `trust_score >= 80` → relaxed policies for known-good agents

### Security event types

All four event types are produced by `agentcop.adapters._runtime`:

| `event_type` | `severity` | When |
|---|---|---|
| `permission_violation` | `CRITICAL` | `ToolPermissionLayer.verify()` returns `granted=False` |
| `gate_denied` | `CRITICAL` | `ExecutionGate.check()` returns `allowed=False`, or approval denied |
| `approval_requested` | `WARN` | `risk_score > requires_approval_above` |
| `sandbox_escape` | `CRITICAL` | Reserved for future sandbox violation reporting |

Security events are buffered on the adapter (`_buffer`) and flushed via
`adapter.flush_into(sentinel)`.

### LangGraph example with all four layers

```python
from agentcop import Sentinel
from agentcop.adapters.langgraph import LangGraphSentinelAdapter
from agentcop.gate import ExecutionGate, ConditionalPolicy, RateLimitPolicy
from agentcop.permissions import ToolPermissionLayer, NetworkPermission, WritePermission
from agentcop.sandbox import AgentSandbox
from agentcop.approvals import ApprovalBoundary

gate = ExecutionGate()
gate.register_policy("web_search", RateLimitPolicy(max_calls=10, window_seconds=60))
gate.register_policy("file_write", ConditionalPolicy(
    allow_if=lambda args: args.get("path", "").startswith("/tmp/"),
    deny_reason="writes outside /tmp are prohibited",
))

permissions = ToolPermissionLayer()
permissions.declare("planner", [
    NetworkPermission(domains=["api.openai.com", "serpapi.com"]),
    WritePermission(paths=["/tmp/*"]),
])

sandbox = AgentSandbox(
    allowed_paths=["/tmp/*", "/data/readonly/*"],
    allowed_domains=["api.openai.com", "serpapi.com"],
    max_execution_time=30.0,
)

approvals = ApprovalBoundary(
    requires_approval_above=75,
    channels=["cli"],
    timeout=120,
)

adapter = LangGraphSentinelAdapter(
    thread_id="run-abc",
    gate=gate,
    permissions=permissions,
    sandbox=sandbox,
    approvals=approvals,
)

sentinel = Sentinel()
try:
    sentinel.ingest(adapter.iter_events(
        graph.stream(input, config, stream_mode="debug")
    ))
except PermissionError as e:
    print(f"Blocked: {e}")
finally:
    adapter.flush_into(sentinel)

violations = sentinel.detect_violations()
sentinel.report()
```
