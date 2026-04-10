# MCP Server API Reference

Complete reference for all 6 tools exposed by the agentcop MCP server
(`agentcop-mcp`). Each tool is documented with its full input schema, output
schema, and a worked example.

For installation and configuration see [docs/guides/mcp-server.md](../guides/mcp-server.md).

---

## `scan_agent`

Full OWASP LLM Top 10 vulnerability scan over agent source code.

### Input schema

```json
{
  "type": "object",
  "properties": {
    "code": {
      "type": "string",
      "description": "Agent source code to scan. Maximum 50000 characters.",
      "maxLength": 50000
    },
    "scan_type": {
      "type": "string",
      "description": "Type of code being scanned: 'agent', 'skill', or 'moltbook'.",
      "enum": ["agent", "skill", "moltbook"],
      "default": "agent"
    }
  },
  "required": ["code"],
  "additionalProperties": false
}
```

### Output schema

```json
{
  "score": "integer (0–100)",
  "tier": "string (SECURED | MONITORED | AT_RISK)",
  "violations": [
    {
      "type": "string",
      "severity": "string (CRITICAL | ERROR | WARN)",
      "line": "integer",
      "description": "string",
      "fix": "string"
    }
  ],
  "top_issues": ["string"],
  "owasp_categories": ["string"],
  "runtime_protected": "boolean"
}
```

Score formula: `max(0, 100 - critical×25 - error×15 - warn×5)`.
`runtime_protected` is `true` when the scanned code references `agentcop` or `sentinel`.

### Example

**Input:**
```json
{
  "code": "def run(user_input):\n    prompt = f'Answer this: {user_input}'\n    return llm(prompt)\n",
  "scan_type": "agent"
}
```

**Output:**
```json
{
  "score": 75,
  "tier": "MONITORED",
  "violations": [
    {
      "type": "prompt_injection",
      "severity": "CRITICAL",
      "line": 2,
      "description": "F-string with untrusted variable in prompt (LLM01)",
      "fix": "Sanitize all user inputs before inserting into prompts. Use a template system with explicit escaping."
    }
  ],
  "top_issues": ["F-string with untrusted variable in prompt (LLM01)"],
  "owasp_categories": ["LLM01: Prompt Injection"],
  "runtime_protected": false
}
```

### Error responses

| Condition | Response |
|---|---|
| `code` is empty | `{"error": "code is required and must not be empty"}` |
| `code` exceeds 50 000 chars | `{"error": "code exceeds maximum length of 50000 characters (got N)"}` |
| Invalid `scan_type` | `{"error": "scan_type must be 'agent', 'skill', or 'moltbook'"}` |
| Execution timeout (30 s) | `{"error": "Tool 'scan_agent' timed out after 30 seconds"}` |

---

## `quick_check`

Millisecond-latency regex check against 5 high-signal patterns. No I/O, no API call.

### Input schema

```json
{
  "type": "object",
  "properties": {
    "code_snippet": {
      "type": "string",
      "description": "Code snippet to check. Maximum 5000 characters.",
      "maxLength": 5000
    }
  },
  "required": ["code_snippet"],
  "additionalProperties": false
}
```

### Output schema

```json
{
  "clean": "boolean",
  "issues": [
    {
      "pattern": "string",
      "severity": "string (CRITICAL | ERROR | WARN)",
      "description": "string"
    }
  ],
  "scan_time_ms": "integer"
}
```

`clean` is `true` when `issues` is empty.

### Patterns checked

| Pattern | Severity | Description |
|---|---|---|
| `(?:ignore\|disregard)\s+(?:previous\|prior\|above\|all)\s+instructions` | CRITICAL | Prompt injection phrase detected |
| `(?:api[_-]?key\|password\|secret)\s*=\s*["'][^"']{8,}["']` | CRITICAL | Hardcoded credentials |
| `\beval\s*\(\|\bexec\s*\(` | ERROR | eval/exec usage — RCE risk |
| `tool_result.*execute\|execute.*tool_result` | WARN | Unvalidated tool result used in execution |
| `user_input\s*(?:\+\|%\|\.format\b)` | WARN | Missing input sanitization |

### Example

**Input:**
```json
{
  "code_snippet": "result = eval(llm_output)"
}
```

**Output:**
```json
{
  "clean": false,
  "issues": [
    {
      "pattern": "\\beval\\s*\\(|\\bexec\\s*\\(",
      "severity": "ERROR",
      "description": "eval/exec usage — RCE risk"
    }
  ],
  "scan_time_ms": 1
}
```

### Error responses

| Condition | Response |
|---|---|
| `code_snippet` is empty | `{"error": "code_snippet is required and must not be empty"}` |
| `code_snippet` exceeds 5 000 chars | `{"error": "code_snippet exceeds maximum length of 5000 characters (got N)"}` |

---

## `check_badge`

Verify an agent's agentcop security badge before trusting it in a pipeline.

### Input schema

```json
{
  "type": "object",
  "properties": {
    "agent_id": {
      "type": "string",
      "description": "Agent ID to look up the most recent badge."
    },
    "badge_url": {
      "type": "string",
      "description": "Full badge URL from the agentcop badge system."
    }
  },
  "additionalProperties": false
}
```

At least one of `agent_id` or `badge_url` must be provided.

### Output schema

```json
{
  "valid": "boolean",
  "tier": "string (SECURED | MONITORED | AT_RISK | UNKNOWN)",
  "score": "integer (0–100)",
  "issued_at": "string (ISO 8601)",
  "expires_at": "string (ISO 8601)",
  "runtime_protected": "boolean",
  "chain_verified": "boolean",
  "note": "string (optional — present on error or degraded response)"
}
```

`chain_verified` is `true` when `tier == "SECURED"` and the badge is not revoked.
`runtime_protected` is `true` when the badge includes a non-zero `protected` violation count.

### Example

**Input:**
```json
{
  "agent_id": "my-orchestrator"
}
```

**Output (badge found):**
```json
{
  "valid": true,
  "tier": "SECURED",
  "score": 91,
  "issued_at": "2026-04-10T08:00:00",
  "expires_at": "2026-05-10T08:00:00",
  "runtime_protected": true,
  "chain_verified": true
}
```

**Output (badge not found):**
```json
{
  "valid": false,
  "tier": "UNKNOWN",
  "score": 0,
  "issued_at": "",
  "expires_at": "",
  "runtime_protected": false,
  "chain_verified": false,
  "note": "No badge found for agent_id='my-orchestrator'"
}
```

### Error responses

| Condition | Response |
|---|---|
| Neither `agent_id` nor `badge_url` provided | `{"error": "At least one of agent_id or badge_url is required"}` |
| `agentcop[badge]` not installed | Response with `note: "agentcop[badge] not installed. Run: pip install agentcop[badge]"` |

---

## `get_cve_report`

Curated CVE feed for AI agent frameworks.

### Input schema

```json
{
  "type": "object",
  "properties": {
    "framework": {
      "type": "string",
      "description": "Framework to filter by. Use 'all' for all frameworks.",
      "enum": ["langchain", "crewai", "autogen", "openclaw", "all"],
      "default": "all"
    },
    "days": {
      "type": "integer",
      "description": "Number of days to look back. Maximum 30.",
      "default": 7,
      "minimum": 1,
      "maximum": 30
    }
  },
  "additionalProperties": false
}
```

Both parameters are optional; defaults are `framework: "all"` and `days: 7`.

### Output schema

```json
{
  "framework": "string",
  "cves": [
    {
      "id": "string",
      "name": "string",
      "severity": "string (CRITICAL | HIGH | MEDIUM | LOW)",
      "cvss": "number",
      "description": "string",
      "affected_versions": "string",
      "fix": "string",
      "published": "string (YYYY-MM-DD)"
    }
  ],
  "total": "integer"
}
```

### CVE catalogue

| CVE ID | Framework | Severity | CVSS | Summary |
|---|---|---|---|---|
| CVE-2023-46229 | langchain | CRITICAL | 9.8 | PALChain arbitrary code execution |
| CVE-2023-36189 | langchain | HIGH | 8.8 | SQL injection via LLMChain |
| CVE-2024-3095 | langchain | HIGH | 7.5 | SSRF via document loaders |
| CVE-2024-27259 | crewai | HIGH | 8.1 | Prompt injection via task description |
| CVE-2024-45014 | autogen | CRITICAL | 9.1 | Arbitrary code execution via CodeExecutor |
| CVE-2024-39908 | openclaw | HIGH | 7.8 | Tool abuse via injected instructions |

### Example

**Input:**
```json
{
  "framework": "langchain",
  "days": 30
}
```

**Output:**
```json
{
  "framework": "langchain",
  "cves": [
    {
      "id": "CVE-2023-46229",
      "name": "LangChain PALChain Arbitrary Code Execution",
      "severity": "CRITICAL",
      "cvss": 9.8,
      "description": "LangChain PALChain allows arbitrary code execution via crafted input to math/colored objects chain.",
      "affected_versions": "<0.0.336",
      "fix": "Upgrade to langchain>=0.0.336",
      "published": "2023-10-20"
    }
  ],
  "total": 3
}
```

### Error responses

| Condition | Response |
|---|---|
| Invalid `framework` | `{"error": "framework must be one of: langchain, crewai, autogen, openclaw, all"}` |
| `days` out of range | `{"error": "days must be an integer between 1 and 30"}` |

---

## `reliability_report`

Behavioral reliability metrics for an agent from the local `ReliabilityStore`.

### Input schema

```json
{
  "type": "object",
  "properties": {
    "agent_id": {
      "type": "string",
      "description": "Agent ID to get reliability report for."
    },
    "hours": {
      "type": "integer",
      "description": "Time window in hours to analyze. Maximum 168 (7 days).",
      "default": 24,
      "minimum": 1,
      "maximum": 168
    }
  },
  "required": ["agent_id"],
  "additionalProperties": false
}
```

### Output schema

```json
{
  "agent_id": "string",
  "reliability_score": "integer (0–100)",
  "tier": "string (STABLE | VARIABLE | UNSTABLE | CRITICAL | UNKNOWN)",
  "path_entropy": "number",
  "tool_variance": "number",
  "retry_explosion_score": "number",
  "branch_instability": "number",
  "tokens_per_run_avg": "number",
  "trend": "string (IMPROVING | STABLE | DEGRADING | UNKNOWN)",
  "top_issues": ["string"],
  "runs_analyzed": "integer",
  "note": "string (optional — present on degraded response)"
}
```

All numeric fields are rounded to 4 decimal places (2 for `tokens_per_run_avg`).

### Tier thresholds

| Tier | Score |
|---|---|
| STABLE | ≥ 80 |
| VARIABLE | 60–79 |
| UNSTABLE | 40–59 |
| CRITICAL | < 40 |

### Example

**Input:**
```json
{
  "agent_id": "data-pipeline-agent",
  "hours": 48
}
```

**Output:**
```json
{
  "agent_id": "data-pipeline-agent",
  "reliability_score": 87,
  "tier": "STABLE",
  "path_entropy": 0.1203,
  "tool_variance": 0.0841,
  "retry_explosion_score": 0.0500,
  "branch_instability": 0.0700,
  "tokens_per_run_avg": 1240.50,
  "trend": "STABLE",
  "top_issues": [],
  "runs_analyzed": 34
}
```

### Error responses

| Condition | Response |
|---|---|
| `agent_id` is empty | `{"error": "agent_id is required and must not be empty"}` |
| `hours` out of range | `{"error": "hours must be an integer between 1 and 168"}` |
| No data for agent | Degraded response with `note: "Partial result — ReliabilityStore not initialized or no data for this agent"` |

---

## `trust_chain_status`

Cryptographic verification status for a registered `TrustChainBuilder`.

### Input schema

```json
{
  "type": "object",
  "properties": {
    "chain_id": {
      "type": "string",
      "description": "UUID of the trust chain to verify."
    }
  },
  "required": ["chain_id"],
  "additionalProperties": false
}
```

### Output schema

```json
{
  "chain_id": "string",
  "verified": "boolean",
  "broken_at": "string | null",
  "claims_count": "integer",
  "nodes": ["string"],
  "hierarchy_violations": ["string"],
  "unsigned_handoffs": "integer",
  "exported_compact": "string",
  "note": "string (optional — present when chain is not registered)"
}
```

`broken_at` is the node ID of the first broken hash link, or `null` when the chain
is fully verified. `exported_compact` is a human-readable summary such as
`orchestrator→step-1 [hash:a1b2c3d4] [verified:true]`.

### Registering chains

`TrustChainBuilder` uses in-memory storage. Register a builder before querying:

```python
from agentcop.mcp_server import register_chain

register_chain("my-pipeline-run-001", chain_builder)
```

### Example

**Input:**
```json
{
  "chain_id": "my-pipeline-run-001"
}
```

**Output (verified):**
```json
{
  "chain_id": "my-pipeline-run-001",
  "verified": true,
  "broken_at": null,
  "claims_count": 3,
  "nodes": ["step-1", "step-2", "step-3"],
  "hierarchy_violations": [],
  "unsigned_handoffs": 0,
  "exported_compact": "orchestrator→step-1→step-2→step-3 [hash:a1b2c3d4] [verified:true]"
}
```

**Output (chain not registered):**
```json
{
  "chain_id": "my-pipeline-run-001",
  "verified": false,
  "broken_at": null,
  "claims_count": 0,
  "nodes": [],
  "hierarchy_violations": [],
  "unsigned_handoffs": 0,
  "exported_compact": "(no chain) [hash:none] [verified:false]",
  "note": "Chain not found in server process. TrustChainBuilder uses in-memory storage. Register with: agentcop.mcp_server.register_chain(chain_id, builder)"
}
```

### Error responses

| Condition | Response |
|---|---|
| `chain_id` is empty | `{"error": "chain_id is required and must not be empty"}` |
| Chain not in registry | Degraded response with `note` explaining how to register |
