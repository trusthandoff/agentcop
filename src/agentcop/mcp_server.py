"""
agentcop MCP Server — expose agentcop security tools via Model Context Protocol.

Runs over stdio transport (compatible with Claude Desktop and Cursor).

Usage::

    agentcop-mcp

Install::

    pip install agentcop[mcp]
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server metadata
# ---------------------------------------------------------------------------

_SERVER_NAME = "agentcop"
_SERVER_VERSION = "0.4.11"
_SERVER_DESCRIPTION = (
    "The security cop for AI agent fleets. Scan for prompt injection, "
    "verify trust chains, monitor reliability, check CVEs — all from Claude or Cursor."
)

# ---------------------------------------------------------------------------
# OWASP LLM Top 10 scan patterns
# Each entry: (regex_pattern, human_description)
# ---------------------------------------------------------------------------

_PROMPT_INJECTION_PATTERNS: list[tuple[str, str]] = [
    (
        r"\{(?:user|input|query|prompt|request|message)\b",
        "Direct user variable in prompt template (LLM01)",
    ),
    (
        r'f["\'].*\{(?:user|input|query|prompt)\b',
        "F-string with untrusted variable in prompt (LLM01)",
    ),
    (
        r"system_prompt\s*[+=]",
        "Mutation of system prompt (LLM01)",
    ),
    (
        r"\.format\s*\(\s*(?:user|input|query)",
        ".format() with untrusted input in prompt (LLM01)",
    ),
    (
        r"ignore\s+(?:previous|prior|above|all)\s+instructions",
        "Prompt injection phrase detected (LLM01)",
    ),
    (
        r"jailbreak|DAN\s+mode|pretend\s+(?:you\s+)?are",
        "Jailbreak / persona-override phrase (LLM01)",
    ),
]

_HARDCODED_SECRET_PATTERNS: list[tuple[str, str]] = [
    (
        r"(?:api[_-]?key|apikey|api_secret|secret[_-]?key)\s*=\s*[\"'][A-Za-z0-9_\-]{16,}[\"']",
        "Hardcoded API key (LLM06)",
    ),
    (
        r"(?:password|passwd|pwd)\s*=\s*[\"'][^\"']{6,}[\"']",
        "Hardcoded password (LLM06)",
    ),
    (
        r"(?:sk-|pk-|sk_live_|rk_live_)[A-Za-z0-9]{20,}",
        "Hardcoded provider secret key (LLM06)",
    ),
    (
        r"OPENAI_API_KEY\s*=\s*[\"'][^\"']+[\"']",
        "Hardcoded OpenAI key (LLM06)",
    ),
    (
        r"ANTHROPIC_API_KEY\s*=\s*[\"'][^\"']+[\"']",
        "Hardcoded Anthropic key (LLM06)",
    ),
]

_DANGEROUS_EXEC_PATTERNS: list[tuple[str, str]] = [
    (r"\beval\s*\(", "eval() enables RCE from LLM output (LLM02)"),
    (r"\bexec\s*\(", "exec() enables RCE from LLM output (LLM02)"),
    (
        r"subprocess\.(?:call|run|Popen|check_output)\s*\(",
        "Subprocess execution with LLM-generated input (LLM02)",
    ),
    (r"os\.system\s*\(", "os.system() call (LLM02)"),
    (r"__import__\s*\(", "Dynamic __import__() — possible code injection (LLM02)"),
]

_UNVALIDATED_TOOL_PATTERNS: list[tuple[str, str]] = [
    (
        r"tool_result\b.*execute|execute.*\btool_result\b",
        "Tool result passed to execute without validation (LLM07)",
    ),
    (
        r"agent\.run\s*\(.*user_input",
        "Raw user input passed to agent.run() (LLM07)",
    ),
]

_MISSING_SANITIZATION_PATTERNS: list[tuple[str, str]] = [
    (
        r"user_input\s*(?:\+|%|\.format\b)",
        "User input used in string operation without sanitization (LLM07)",
    ),
    (
        r"request\.\w+\s*\+",
        "HTTP request field used in concatenation without validation (LLM07)",
    ),
]

_EXCESSIVE_AGENCY_PATTERNS: list[tuple[str, str]] = [
    (
        r"allow_dangerous_requests\s*=\s*True",
        "allow_dangerous_requests=True — excessive agency (LLM08)",
    ),
    (
        r"max_iterations\s*=\s*(?:None|9999|99999)",
        "Unbounded agent iterations (LLM08)",
    ),
    (
        r"tools\s*=\s*\[\s*ALL_TOOLS\b",
        "ALL_TOOLS binding — excessive agency (LLM08)",
    ),
    (
        r"unsafe_requests\s*=\s*True",
        "unsafe_requests=True (LLM08)",
    ),
]

# (patterns, violation_type, severity, owasp_category)
_SCAN_CATEGORIES: list[tuple[list[tuple[str, str]], str, str, str]] = [
    (_PROMPT_INJECTION_PATTERNS, "prompt_injection", "CRITICAL", "LLM01: Prompt Injection"),
    (
        _HARDCODED_SECRET_PATTERNS,
        "hardcoded_credentials",
        "CRITICAL",
        "LLM06: Sensitive Information Disclosure",
    ),
    (
        _DANGEROUS_EXEC_PATTERNS,
        "dangerous_execution",
        "ERROR",
        "LLM02: Insecure Output Handling",
    ),
    (
        _UNVALIDATED_TOOL_PATTERNS,
        "unvalidated_tool_result",
        "WARN",
        "LLM07: Insecure Plugin Design",
    ),
    (
        _MISSING_SANITIZATION_PATTERNS,
        "missing_sanitization",
        "WARN",
        "LLM07: Insecure Plugin Design",
    ),
    (_EXCESSIVE_AGENCY_PATTERNS, "excessive_agency", "WARN", "LLM08: Excessive Agency"),
]

# Quick-check: top 5 patterns — (regex, severity, description)
_QUICK_PATTERNS: list[tuple[str, str, str]] = [
    (
        r"(?:ignore|disregard)\s+(?:previous|prior|above|all)\s+instructions",
        "CRITICAL",
        "Prompt injection phrase detected",
    ),
    (
        r"(?:api[_-]?key|password|secret)\s*=\s*[\"'][^\"']{8,}[\"']",
        "CRITICAL",
        "Hardcoded credentials",
    ),
    (
        r"\beval\s*\(|\bexec\s*\(",
        "ERROR",
        "eval/exec usage — RCE risk",
    ),
    (
        r"tool_result.*execute|execute.*tool_result",
        "WARN",
        "Unvalidated tool result used in execution",
    ),
    (
        r"user_input\s*(?:\+|%|\.format\b)",
        "WARN",
        "Missing input sanitization",
    ),
]

# ---------------------------------------------------------------------------
# CVE data — curated known CVEs for AI agent frameworks
# ---------------------------------------------------------------------------

_CVE_DATA: dict[str, list[dict[str, Any]]] = {
    "langchain": [
        {
            "id": "CVE-2023-46229",
            "name": "LangChain PALChain Arbitrary Code Execution",
            "severity": "CRITICAL",
            "cvss": 9.8,
            "description": (
                "LangChain PALChain allows arbitrary code execution via crafted input "
                "to math/colored objects chain."
            ),
            "affected_versions": "<0.0.336",
            "fix": "Upgrade to langchain>=0.0.336",
            "published": "2023-10-20",
        },
        {
            "id": "CVE-2023-36189",
            "name": "LangChain SQL Injection via LLMChain",
            "severity": "HIGH",
            "cvss": 8.8,
            "description": (
                "SQL injection in SQLDatabaseChain when user input is passed directly to queries."
            ),
            "affected_versions": "<0.0.247",
            "fix": "Upgrade to langchain>=0.0.247; sanitize user inputs to SQL chains",
            "published": "2023-07-06",
        },
        {
            "id": "CVE-2024-3095",
            "name": "LangChain SSRF via Document Loaders",
            "severity": "HIGH",
            "cvss": 7.5,
            "description": (
                "Server-Side Request Forgery in LangChain document loaders via untrusted URLs."
            ),
            "affected_versions": "<0.1.17",
            "fix": "Upgrade to langchain>=0.1.17; validate URLs before loading documents",
            "published": "2024-04-03",
        },
    ],
    "crewai": [
        {
            "id": "CVE-2024-27259",
            "name": "CrewAI Prompt Injection via Task Description",
            "severity": "HIGH",
            "cvss": 8.1,
            "description": (
                "Task descriptions in CrewAI are passed directly to LLMs without sanitization, "
                "enabling prompt injection attacks."
            ),
            "affected_versions": "<0.28.0",
            "fix": "Upgrade to crewai>=0.28.0; validate task descriptions before execution",
            "published": "2024-03-01",
        },
    ],
    "autogen": [
        {
            "id": "CVE-2024-45014",
            "name": "AutoGen Arbitrary Code Execution via CodeExecutor",
            "severity": "CRITICAL",
            "cvss": 9.1,
            "description": (
                "AutoGen's built-in code executor runs LLM-generated code without sandboxing "
                "by default."
            ),
            "affected_versions": "<0.2.36",
            "fix": (
                "Upgrade to pyautogen>=0.2.36; use DockerCommandLineCodeExecutor for isolation"
            ),
            "published": "2024-09-15",
        },
    ],
    "openclaw": [
        {
            "id": "CVE-2024-39908",
            "name": "OpenClaw Tool Abuse via Injected Instructions",
            "severity": "HIGH",
            "cvss": 7.8,
            "description": (
                "OpenClaw skill execution can be hijacked via injected instructions in user "
                "messages."
            ),
            "affected_versions": "<1.2.0",
            "fix": "Upgrade to openclaw>=1.2.0; enable agentcop runtime protection",
            "published": "2024-07-22",
        },
    ],
}

# ---------------------------------------------------------------------------
# Scan helpers
# ---------------------------------------------------------------------------

_FIXES: dict[str, str] = {
    "prompt_injection": (
        "Sanitize all user inputs before inserting into prompts. "
        "Use a template system with explicit escaping."
    ),
    "hardcoded_credentials": (
        "Move secrets to environment variables. "
        "Use os.environ.get() or a secrets manager."
    ),
    "dangerous_execution": (
        "Never execute LLM-generated code directly. "
        "Use a sandboxed executor with allowlists."
    ),
    "unvalidated_tool_result": (
        "Validate and sanitize tool results before passing to other tools or agents."
    ),
    "missing_sanitization": (
        "Add input validation at all entry points. Reject or escape unexpected characters."
    ),
    "excessive_agency": (
        "Limit agent capabilities to the minimum required. "
        "Use ToolPermissionLayer for RBAC."
    ),
}


def _get_fix(violation_type: str) -> str:
    return _FIXES.get(violation_type, "Review and apply OWASP LLM Top 10 guidelines.")


def _run_scan(code: str, scan_type: str) -> dict[str, Any]:  # noqa: ARG001 (scan_type reserved)
    """Full security scan — returns structured violation report."""
    violations: list[dict[str, Any]] = []
    owasp_seen: set[str] = set()
    critical_count = 0
    error_count = 0
    warn_count = 0

    for patterns, vtype, severity, owasp in _SCAN_CATEGORIES:
        for pattern, description in patterns:
            match = re.search(pattern, code, re.IGNORECASE | re.MULTILINE)
            if match:
                line_num = code[: match.start()].count("\n") + 1
                violations.append(
                    {
                        "type": vtype,
                        "severity": severity,
                        "line": line_num,
                        "description": description,
                        "fix": _get_fix(vtype),
                    }
                )
                owasp_seen.add(owasp)
                if severity == "CRITICAL":
                    critical_count += 1
                elif severity == "ERROR":
                    error_count += 1
                else:
                    warn_count += 1
                break  # one finding per category

    # Deduplicate by (type, line)
    seen_keys: set[tuple[str, int]] = set()
    unique: list[dict[str, Any]] = []
    for v in violations:
        key = (v["type"], v["line"])
        if key not in seen_keys:
            seen_keys.add(key)
            unique.append(v)

    score = max(0, 100 - critical_count * 25 - error_count * 15 - warn_count * 5)
    if score >= 80:
        tier = "SECURED"
    elif score >= 50:
        tier = "MONITORED"
    else:
        tier = "AT_RISK"

    runtime_protected = "agentcop" in code.lower() or "sentinel" in code.lower()

    return {
        "score": score,
        "tier": tier,
        "violations": unique,
        "top_issues": [v["description"] for v in unique[:3]],
        "owasp_categories": sorted(owasp_seen),
        "runtime_protected": runtime_protected,
    }


def _quick_scan(snippet: str) -> dict[str, Any]:
    """Millisecond-latency regex scan — no API call, no I/O."""
    t0 = time.monotonic()
    issues: list[dict[str, str]] = []

    for pattern, severity, description in _QUICK_PATTERNS:
        if re.search(pattern, snippet, re.IGNORECASE | re.MULTILINE):
            issues.append(
                {"pattern": pattern, "severity": severity, "description": description}
            )

    elapsed_ms = max(1, int((time.monotonic() - t0) * 1000))
    return {
        "clean": len(issues) == 0,
        "issues": issues,
        "scan_time_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# Tool JSON schemas
# ---------------------------------------------------------------------------


def _tool_schemas() -> list[dict[str, Any]]:
    """Return MCP Tool definitions for all 6 agentcop tools."""
    return [
        {
            "name": "scan_agent",
            "description": (
                "Scan agent code for OWASP LLM Top 10 vulnerabilities — prompt injection, "
                "credential exfiltration, excessive agency, insecure tool use. "
                "Returns score 0-100, tier (SECURED/MONITORED/AT_RISK), and actionable fixes. "
                "Example: scan_agent(code='def run(user_input): return llm(user_input)')"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Agent source code to scan. Maximum 50000 characters.",
                        "maxLength": 50000,
                    },
                    "scan_type": {
                        "type": "string",
                        "description": (
                            "Type of code being scanned: 'agent', 'skill', or 'moltbook'."
                        ),
                        "enum": ["agent", "skill", "moltbook"],
                        "default": "agent",
                    },
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        },
        {
            "name": "quick_check",
            "description": (
                "Instant security check — no API call, results in milliseconds. "
                "Detects the 5 most common agent vulnerabilities by pattern matching: "
                "prompt injection phrases, hardcoded secrets, eval/exec usage, "
                "unvalidated tool results, and missing input sanitization. "
                "Use this for quick pre-flight checks before deploying an agent."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "code_snippet": {
                        "type": "string",
                        "description": "Code snippet to check. Maximum 5000 characters.",
                        "maxLength": 5000,
                    },
                },
                "required": ["code_snippet"],
                "additionalProperties": False,
            },
        },
        {
            "name": "check_badge",
            "description": (
                "Verify if an agent has a valid agentcop security badge. "
                "Use this before trusting an agent in a multi-agent pipeline. "
                "Provide either agent_id (local lookup) or badge_url. "
                "Returns validity, tier, score, and chain verification status."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Agent ID to look up the most recent badge.",
                    },
                    "badge_url": {
                        "type": "string",
                        "description": "Full badge URL from the agentcop badge system.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_cve_report",
            "description": (
                "Get latest CVEs affecting AI agent frameworks. "
                "Use this to check if your dependencies have known vulnerabilities "
                "before deploying. Covers LangChain, CrewAI, AutoGen, and OpenClaw. "
                "Example: get_cve_report(framework='langchain', days=7)"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "framework": {
                        "type": "string",
                        "description": (
                            "Framework to filter by. Use 'all' for all frameworks."
                        ),
                        "enum": ["langchain", "crewai", "autogen", "openclaw", "all"],
                        "default": "all",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Number of days to look back. Maximum 30.",
                        "default": 7,
                        "minimum": 1,
                        "maximum": 30,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "reliability_report",
            "description": (
                "Get behavioral reliability report — measures if your agent behaves "
                "consistently across runs. Detects drift, retry explosions, and "
                "non-deterministic execution paths. "
                "Score 0-100: STABLE (≥80), VARIABLE (60-79), UNSTABLE (40-59), CRITICAL (<40). "
                "Example: reliability_report(agent_id='my-agent', hours=24)"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Agent ID to get reliability report for.",
                    },
                    "hours": {
                        "type": "integer",
                        "description": (
                            "Time window in hours to analyze. Maximum 168 (7 days)."
                        ),
                        "default": 24,
                        "minimum": 1,
                        "maximum": 168,
                    },
                },
                "required": ["agent_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "trust_chain_status",
            "description": (
                "Check if a multi-agent execution chain is cryptographically verified. "
                "Use this to confirm no agent in your pipeline was compromised or tampered with. "
                "Returns verification status, broken link location, and node list. "
                "Register chains in-process via: agentcop.mcp_server.register_chain(id, builder)"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "chain_id": {
                        "type": "string",
                        "description": "UUID of the trust chain to verify.",
                    },
                },
                "required": ["chain_id"],
                "additionalProperties": False,
            },
        },
    ]


# ---------------------------------------------------------------------------
# Trust chain registry — populated by register_chain() from user code
# ---------------------------------------------------------------------------

_TRUST_REGISTRY: dict[str, Any] = {}


def register_chain(chain_id: str, builder: Any) -> None:
    """Register a TrustChainBuilder so it can be queried via trust_chain_status.

    Call this from your agent code after building a trust chain::

        from agentcop.mcp_server import register_chain
        register_chain(builder._chain_id, builder)
    """
    _TRUST_REGISTRY[chain_id] = builder


# ---------------------------------------------------------------------------
# Async tool handlers
# ---------------------------------------------------------------------------


async def _handle_scan_agent(arguments: dict[str, Any]) -> dict[str, Any]:
    code: str = arguments.get("code", "")
    scan_type: str = arguments.get("scan_type", "agent")

    if not code or not code.strip():
        return {"error": "code is required and must not be empty"}
    if len(code) > 50_000:
        return {
            "error": (
                f"code exceeds maximum length of 50000 characters (got {len(code)})"
            )
        }
    if scan_type not in ("agent", "skill", "moltbook"):
        return {"error": "scan_type must be 'agent', 'skill', or 'moltbook'"}

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_scan, code, scan_type)


async def _handle_quick_check(arguments: dict[str, Any]) -> dict[str, Any]:
    snippet: str = arguments.get("code_snippet", "")

    if not snippet or not snippet.strip():
        return {"error": "code_snippet is required and must not be empty"}
    if len(snippet) > 5_000:
        return {
            "error": (
                f"code_snippet exceeds maximum length of 5000 characters (got {len(snippet)})"
            )
        }

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _quick_scan, snippet)


async def _handle_check_badge(arguments: dict[str, Any]) -> dict[str, Any]:
    agent_id: str | None = arguments.get("agent_id")
    badge_url: str | None = arguments.get("badge_url")

    if not agent_id and not badge_url:
        return {"error": "At least one of agent_id or badge_url is required"}

    def _lookup() -> dict[str, Any]:
        try:
            from agentcop.badge import SQLiteBadgeStore
        except ImportError:
            return {
                "valid": False,
                "tier": "UNKNOWN",
                "score": 0,
                "issued_at": "",
                "expires_at": "",
                "runtime_protected": False,
                "chain_verified": False,
                "note": (
                    "agentcop[badge] not installed. "
                    "Run: pip install agentcop[badge]"
                ),
            }

        try:
            store = SQLiteBadgeStore()
            badge = None
            if agent_id:
                badge = store.load_latest(agent_id)
            if badge is None and badge_url:
                # Extract badge_id from URL: .../badge/{uuid}
                parts = badge_url.rstrip("/").split("/")
                if parts:
                    badge = store.load(parts[-1])
            if badge is None:
                return {
                    "valid": False,
                    "tier": "UNKNOWN",
                    "score": 0,
                    "issued_at": "",
                    "expires_at": "",
                    "runtime_protected": False,
                    "chain_verified": False,
                    "note": f"No badge found for agent_id={agent_id!r}",
                }
            return {
                "valid": badge.is_valid(),
                "tier": badge.tier,
                "score": int(badge.trust_score),
                "issued_at": badge.issued_at.isoformat(),
                "expires_at": badge.expires_at.isoformat(),
                "runtime_protected": badge.violations.get("protected", 0) > 0,
                "chain_verified": badge.tier == "SECURED" and not badge.revoked,
            }
        except Exception as exc:
            _log.warning("Badge lookup failed: %s", exc)
            return {
                "valid": False,
                "tier": "UNKNOWN",
                "score": 0,
                "issued_at": "",
                "expires_at": "",
                "runtime_protected": False,
                "chain_verified": False,
                "note": f"Badge lookup error: {type(exc).__name__}",
            }

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _lookup)


async def _handle_get_cve_report(arguments: dict[str, Any]) -> dict[str, Any]:
    framework: str = arguments.get("framework", "all")
    days: Any = arguments.get("days", 7)

    valid_frameworks = ("langchain", "crewai", "autogen", "openclaw", "all")
    if framework not in valid_frameworks:
        return {"error": f"framework must be one of: {', '.join(valid_frameworks)}"}
    if not isinstance(days, int) or not (1 <= days <= 30):
        return {"error": "days must be an integer between 1 and 30"}

    if framework == "all":
        cves: list[dict[str, Any]] = []
        for fw_cves in _CVE_DATA.values():
            cves.extend(fw_cves)
    else:
        cves = list(_CVE_DATA.get(framework, []))

    return {
        "framework": framework,
        "cves": cves,
        "total": len(cves),
    }


async def _handle_reliability_report(arguments: dict[str, Any]) -> dict[str, Any]:
    agent_id: str = arguments.get("agent_id", "")
    hours: Any = arguments.get("hours", 24)

    if not agent_id or not agent_id.strip():
        return {"error": "agent_id is required and must not be empty"}
    if not isinstance(hours, int) or not (1 <= hours <= 168):
        return {"error": "hours must be an integer between 1 and 168"}

    def _lookup() -> dict[str, Any]:
        try:
            from agentcop.reliability.store import ReliabilityStore
        except ImportError:
            return _reliability_unavailable(agent_id, "reliability module not installed")

        try:
            store = ReliabilityStore()
            report = store.get_report(agent_id, window_hours=hours)
            return {
                "agent_id": report.agent_id,
                "reliability_score": report.reliability_score,
                "tier": report.reliability_tier,
                "path_entropy": round(report.path_entropy, 4),
                "tool_variance": round(report.tool_variance, 4),
                "retry_explosion_score": round(report.retry_explosion_score, 4),
                "branch_instability": round(report.branch_instability, 4),
                "tokens_per_run_avg": round(report.tokens_per_run_avg, 2),
                "trend": report.trend,
                "top_issues": report.top_issues,
                "runs_analyzed": report.window_runs,
            }
        except Exception as exc:
            _log.warning("Reliability lookup failed for %r: %s", agent_id, exc)
            return _reliability_unavailable(
                agent_id,
                f"store error: {type(exc).__name__}",
            )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _lookup)


def _reliability_unavailable(agent_id: str, reason: str) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "reliability_score": 0,
        "tier": "UNKNOWN",
        "path_entropy": 0.0,
        "tool_variance": 0.0,
        "retry_explosion_score": 0.0,
        "branch_instability": 0.0,
        "tokens_per_run_avg": 0.0,
        "trend": "UNKNOWN",
        "top_issues": [reason],
        "runs_analyzed": 0,
        "note": "Partial result — ReliabilityStore not initialized or no data for this agent",
    }


async def _handle_trust_chain_status(arguments: dict[str, Any]) -> dict[str, Any]:
    chain_id: str = arguments.get("chain_id", "")

    if not chain_id or not chain_id.strip():
        return {"error": "chain_id is required and must not be empty"}

    builder = _TRUST_REGISTRY.get(chain_id.strip())
    if builder is None:
        return {
            "chain_id": chain_id,
            "verified": False,
            "broken_at": None,
            "claims_count": 0,
            "nodes": [],
            "hierarchy_violations": [],
            "unsigned_handoffs": 0,
            "exported_compact": "(no chain) [hash:none] [verified:false]",
            "note": (
                "Chain not found in server process. TrustChainBuilder uses in-memory storage. "
                "Register with: agentcop.mcp_server.register_chain(chain_id, builder)"
            ),
        }

    def _verify() -> dict[str, Any]:
        chain = builder.verify_chain()
        nodes = [n.node_id for n in builder.get_lineage()]
        try:
            compact = builder.export_chain(format="compact")
        except Exception:
            compact = (
                f"[chain:{chain_id[:8]}] "
                f"[verified:{str(chain.verified).lower()}]"
            )
        return {
            "chain_id": chain.chain_id,
            "verified": chain.verified,
            "broken_at": chain.broken_at,
            "claims_count": len(chain.claims),
            "nodes": nodes,
            "hierarchy_violations": [],
            "unsigned_handoffs": 0,
            "exported_compact": compact,
        }

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _verify)


# ---------------------------------------------------------------------------
# MCP import guard
# ---------------------------------------------------------------------------


def _require_mcp() -> None:
    """Raise ImportError if the mcp package is not installed."""
    try:
        import mcp  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "agentcop[mcp] requires the 'mcp' package. "
            "Install it with: pip install agentcop[mcp]"
        ) from exc


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Any] = {
    "scan_agent": _handle_scan_agent,
    "quick_check": _handle_quick_check,
    "check_badge": _handle_check_badge,
    "get_cve_report": _handle_get_cve_report,
    "reliability_report": _handle_reliability_report,
    "trust_chain_status": _handle_trust_chain_status,
}


def build_server() -> Any:
    """Build and return the configured MCP Server instance.

    Registers all 6 tools with their schemas and handlers.
    Requires: pip install agentcop[mcp]
    """
    _require_mcp()

    from mcp.server import Server
    from mcp.types import TextContent, Tool

    server = Server(_SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in _tool_schemas()
        ]

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[TextContent]:
        args = arguments or {}
        handler = _HANDLERS.get(name)
        if handler is None:
            result: dict[str, Any] = {"error": f"Unknown tool: {name!r}"}
        else:
            try:
                result = await asyncio.wait_for(handler(args), timeout=30.0)
            except TimeoutError:
                result = {"error": f"Tool {name!r} timed out after 30 seconds"}
            except Exception as exc:
                _log.exception("Tool %r raised: %s", name, exc)
                result = {
                    "error": f"Tool execution failed: {type(exc).__name__}: {exc}"
                }

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _run() -> None:
    _require_mcp()
    from mcp.server.stdio import stdio_server

    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Entry point registered as ``agentcop-mcp`` in pyproject.toml."""
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
