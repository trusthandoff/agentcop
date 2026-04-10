# agentcop MCP Server

Scan agents, verify trust chains, check CVEs, and monitor reliability — all without
leaving Claude or Cursor. The agentcop MCP server exposes 6 security tools over the
Model Context Protocol (stdio transport), making them available as first-class tools
in any MCP-compatible AI assistant.

---

## What is the agentcop MCP server?

The Model Context Protocol (MCP) lets AI assistants call external tools with structured
inputs and outputs. `agentcop-mcp` is an MCP server that wraps the agentcop security
library — so Claude or Cursor can directly scan your agent code for vulnerabilities,
check a badge before trusting a third-party agent, pull a CVE report, or verify a
cryptographic trust chain, all in natural language.

No HTTP server. No API keys. No infrastructure. Runs as a local subprocess over stdio.

---

## Installation

```bash
pip install agentcop[mcp]
```

This installs the `agentcop-mcp` entry-point and the `mcp>=1.0` dependency.

---

## Claude Desktop configuration

Add the following to `~/.claude/claude_desktop_config.json` (create it if absent):

```json
{
  "mcpServers": {
    "agentcop": {
      "command": "agentcop-mcp"
    }
  }
}
```

Restart Claude Desktop. You will see an **agentcop** section in the tools panel.

### With a virtualenv

If `agentcop-mcp` is installed inside a virtualenv rather than globally:

```json
{
  "mcpServers": {
    "agentcop": {
      "command": "/path/to/your/.venv/bin/agentcop-mcp"
    }
  }
}
```

---

## Cursor configuration

Add the following to `.cursor/mcp.json` in your project root (or the global
`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "agentcop": {
      "command": "agentcop-mcp"
    }
  }
}
```

Restart Cursor. The agentcop tools will be available in Composer and Chat.

---

## Docker usage

Build a minimal image and run the server inside a container. Use `--init` so the
stdio process receives signals correctly.

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir agentcop[mcp]
ENTRYPOINT ["agentcop-mcp"]
```

```bash
docker build -t agentcop-mcp .
```

Claude Desktop / Cursor config:

```json
{
  "mcpServers": {
    "agentcop": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "--init", "agentcop-mcp"]
    }
  }
}
```

---

## Available tools

### `scan_agent` — Full OWASP LLM Top 10 vulnerability scan

Scans agent source code for security issues across six OWASP LLM Top 10 categories.

**Ask Claude:**

> "Scan this agent for vulnerabilities"

> "Check my LangGraph agent for prompt injection"

> "What security issues does this CrewAI crew have?"

**What it detects:**

| Category | OWASP | Severity |
|---|---|---|
| Prompt injection via f-strings, `.format()`, or direct variable substitution | LLM01 | CRITICAL |
| System prompt mutation (`system_prompt +=`) | LLM01 | CRITICAL |
| Jailbreak / persona-override phrases | LLM01 | CRITICAL |
| Hardcoded API keys, passwords, provider secrets | LLM06 | CRITICAL |
| `eval()` / `exec()` on LLM output | LLM02 | ERROR |
| `subprocess.run()` / `os.system()` without sandboxing | LLM02 | ERROR |
| Unvalidated tool results passed to execute | LLM07 | WARN |
| Raw user input in string operations without sanitization | LLM07 | WARN |
| `allow_dangerous_requests=True` / unbounded iterations | LLM08 | WARN |

**Returns:** score 0–100, tier (SECURED / MONITORED / AT_RISK), per-finding line
numbers, OWASP categories found, and an actionable fix for each violation.

---

### `quick_check` — Instant 5-pattern check (millisecond latency)

Checks a code snippet against the 5 highest-signal patterns — no API call, no I/O.

**Ask Claude:**

> "Quick check this function for security issues"

> "Is this snippet safe to deploy?"

**What it detects:**

| Pattern | Severity |
|---|---|
| `ignore previous instructions` / prompt injection phrases | CRITICAL |
| Hardcoded credentials (`api_key = "..."`, `password = "..."`) | CRITICAL |
| `eval()` / `exec()` usage | ERROR |
| Unvalidated tool result in execution | WARN |
| User input in string operation without sanitization | WARN |

**Returns:** `clean: true/false`, list of issues with severity, and `scan_time_ms`.

---

### `check_badge` — Verify an agent's security badge

Checks whether an agent holds a valid, unexpired agentcop security badge before
trusting it in a multi-agent pipeline.

**Ask Claude:**

> "Check the badge for agent my-orchestrator"

> "Is this agent's badge still valid? https://agentcop.live/badge/abc123"

> "Verify the trust badge before I delegate to this agent"

**What it returns:**

| Field | Description |
|---|---|
| `valid` | Badge is current and not revoked |
| `tier` | SECURED / MONITORED / AT_RISK |
| `score` | Trust score 0–100 |
| `issued_at` | ISO timestamp |
| `expires_at` | ISO timestamp (badges expire after 30 days) |
| `runtime_protected` | Agent has runtime enforcement active |
| `chain_verified` | SECURED tier and not revoked |

Requires `pip install agentcop[badge]` for local badge lookup. Degrades gracefully
(returns an explanatory note) if the badge package is not installed.

---

### `get_cve_report` — CVEs for AI agent frameworks

Returns curated CVEs affecting LangChain, CrewAI, AutoGen, and OpenClaw.

**Ask Claude:**

> "What CVEs affect LangChain?"

> "Are there any known vulnerabilities in CrewAI I should know about?"

> "Give me a full CVE report for all agent frameworks"

**Covered frameworks:**

| Framework | CVEs included |
|---|---|
| `langchain` | CVE-2023-46229 (PALChain RCE, CVSS 9.8), CVE-2023-36189 (SQL injection, CVSS 8.8), CVE-2024-3095 (SSRF, CVSS 7.5) |
| `crewai` | CVE-2024-27259 (prompt injection via task description, CVSS 8.1) |
| `autogen` | CVE-2024-45014 (arbitrary code execution via CodeExecutor, CVSS 9.1) |
| `openclaw` | CVE-2024-39908 (tool abuse via injected instructions, CVSS 7.8) |

Filter by `framework` and `days` (1–30). Use `framework: "all"` for everything.

---

### `reliability_report` — Behavioral consistency metrics

Fetches a behavioral reliability report for an agent from the local
`ReliabilityStore`. Reports on whether the agent behaves consistently across runs.

**Ask Claude:**

> "How reliable is my data-pipeline agent over the last 24 hours?"

> "Is agent-orchestrator showing any drift or retry explosions?"

> "Give me a reliability breakdown for all agents in my fleet"

**Metrics returned:**

| Metric | Description |
|---|---|
| `reliability_score` | Weighted composite 0–100 |
| `tier` | STABLE (≥80) / VARIABLE (60–79) / UNSTABLE (40–59) / CRITICAL (<40) |
| `path_entropy` | Shannon entropy of execution paths |
| `tool_variance` | Coefficient of variation in tool usage across runs |
| `retry_explosion_score` | Normalized retry burst score |
| `branch_instability` | Hamming distance between paths for the same input |
| `tokens_per_run_avg` | Mean token consumption per run |
| `trend` | IMPROVING / STABLE / DEGRADING |
| `top_issues` | Top actionable issues detected |
| `runs_analyzed` | Number of runs in the time window |

Requires `agentcop.reliability` (included in the base install). Degrades gracefully
when no data exists for the requested agent.

---

### `trust_chain_status` — Cryptographic chain verification

Verifies a registered `TrustChainBuilder` to confirm no node in your multi-agent
pipeline was tampered with.

**Ask Claude:**

> "Check if my trust chain is verified"

> "Has any agent in the pipeline been tampered with?"

> "Show me the trust chain status for chain-id abc-123"

**What it returns:**

| Field | Description |
|---|---|
| `verified` | All hashes check out |
| `broken_at` | Node ID where the first broken link was found (or `null`) |
| `claims_count` | Number of signed claims in the chain |
| `nodes` | Ordered list of node IDs |
| `hierarchy_violations` | Detected delegation violations |
| `unsigned_handoffs` | Number of handoffs without Ed25519 signatures |
| `exported_compact` | Human-readable chain summary, e.g. `A→B [hash:a1b2] [verified:true]` |

**Registering a chain from your agent code:**

```python
from agentcop.trust import TrustChainBuilder, ExecutionNode
from agentcop.mcp_server import register_chain

with TrustChainBuilder(agent_id="orchestrator") as chain:
    chain.add_node(ExecutionNode(
        node_id="step-1",
        agent_id="orchestrator",
        tool_calls=["web_search"],
        context_hash="abc123",
        output_hash="def456",
        duration_ms=320,
    ))

register_chain("my-pipeline-run-001", chain)
# Now Claude can query: trust_chain_status(chain_id="my-pipeline-run-001")
```

---

## Natural-language usage examples

Once configured, you can ask Claude or Cursor in plain English:

```
"Scan this agent for vulnerabilities" — paste code inline or reference a file

"Quick check this function before I deploy it"

"Check the badge for agent my-orchestrator before I delegate to it"

"What CVEs affect LangChain right now?"

"How reliable has my data-pipeline agent been over the last week?"

"Is my trust chain verified for pipeline run abc-123?"

"Find all prompt injection issues in this codebase and fix them"

"Which agents in my fleet have AT_RISK badges?"
```

---

## Troubleshooting

**`agentcop-mcp` not found**

```bash
which agentcop-mcp         # check PATH
pip show agentcop          # verify install
pip install agentcop[mcp]  # reinstall with mcp extra
```

**Badge lookup returns "agentcop[badge] not installed"**

```bash
pip install agentcop[badge]
```

**Reliability report returns zero data**

The `ReliabilityStore` is only populated when you instrument your agent with
`ReliabilityTracer` or `wrap_for_reliability`. See the
[reliability guide](reliability.md).

**Trust chain not found**

The `TrustChainBuilder` uses in-memory storage — it must be registered in the same
process via `register_chain()` before querying. The MCP server itself is stateless
across restarts.

**Timeout after 30 seconds**

Each tool call has a 30-second timeout. For very large code files, trim to the
relevant section before scanning.
