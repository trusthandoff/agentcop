---
name: agentcop
description: "Catches prompt injection hidden in Moltbook docs, tool results, and external content — plus credential exfiltration, token smuggling, and insecure output — before your agent acts on them"
homepage: https://agentcop.live
user-invocable: true
metadata: { "openclaw": { "emoji": "🔒", "requires": { "anyBins": ["python3", "python"] } } }
---

# AgentCop Security Skill

**AgentCop** is a real-time security monitor for OpenClaw agents. Every message, tool call, and tool result is scanned for OWASP LLM Top 10 violations — including the indirect injection attacks that hide inside Moltbook documents, retrieved web pages, file contents, and any other external data your agent reads and acts on.

Install in one line:
```
openclaw skill install agentcop
```

---

## What it catches

| Threat | OWASP | Detection method |
|--------|-------|-----------------|
| Direct prompt injection ("ignore instructions") | LLM01 | 14 override patterns + role-playing jailbreaks + token smuggling |
| Encoded/obfuscated injections | LLM01 | Base64, ROT13, unicode escapes (`\u0041`), leetspeak |
| Indirect injection (Moltbook docs, web pages, files) | LLM01 | Multi-turn continuation markers ("as I mentioned", "continuing from before") |
| Tool-call argument injection | LLM08 | Full injection scan on tool names + serialised arguments |
| Credential exfiltration in tool results | LLM06 | API keys, tokens, private keys, AWS credentials, bearer tokens |
| Insecure output (code execution sinks) | LLM02 | eval, exec, script injection, path traversal, shell escapes |

Violations carry a **confidence score**: 1–2 signals → WARN, 3–4 → ERROR, 5+ → CRITICAL.

---

## Slash commands

### /security status
```
python3 ~/.openclaw/skills/agentcop/skill.py status
```
Show agent identity fingerprint, trust score (0–1.0), number of buffered events, and all active violations with confidence scores. Report each field clearly. Exit 0 = clean, exit 1 = violations present.

### /security report
```
python3 ~/.openclaw/skills/agentcop/skill.py report
```
Full violation report grouped by severity (CRITICAL → ERROR → WARN). For each violation show: `violation_type`, `severity`, `confidence`, OWASP category, and the matched `signals` list. If `"no_violations": true`, tell the user the session is clean.

### /security scan [target] [--last 1h|24h|7d] [--since ISO]
```
python3 ~/.openclaw/skills/agentcop/skill.py scan
python3 ~/.openclaw/skills/agentcop/skill.py scan --last 1h
python3 ~/.openclaw/skills/agentcop/skill.py scan --last 24h
python3 ~/.openclaw/skills/agentcop/skill.py scan --since 2026-04-04T10:00:00Z
```
Scan buffered events for violations. Use `--last` or `--since` to scope the window for post-incident forensics. Each scan is automatically saved as a session snapshot (shown as `scan_id` in the output) — save that ID to use with `diff` later. Exit 0 = clean, exit 1 = findings.

### /security taint-check <text>
```
python3 ~/.openclaw/skills/agentcop/skill.py taint-check "text to check"
cat prompt.txt | python3 ~/.openclaw/skills/agentcop/skill.py taint-check --stdin
```
Check a single string for LLM01 prompt injection. Pipeable via `--stdin`. Report `tainted: true/false`, the matched signals, and the confidence level. Exit 1 if tainted.

### /security output-check <text>
```
python3 ~/.openclaw/skills/agentcop/skill.py output-check "agent output to check"
python3 ~/.openclaw/skills/agentcop/skill.py output-check --stdin
```
Check agent output for LLM02 insecure patterns (eval, exec, script injection, path traversal, shell commands). Report `unsafe: true/false` and the matched patterns. Exit 1 if unsafe.

### /security diff <session1_id> <session2_id>
```
python3 ~/.openclaw/skills/agentcop/skill.py diff abc12345 def67890
```
Compare two scan sessions (by `scan_id` from prior `scan` runs). Shows:
- `new_violations`: findings in session2 that weren't in session1 — these are regressions
- `resolved_violation_types`: violation types present in session1 but gone in session2

Use this after applying a fix to verify it actually resolved the violation. Exit 0 = no regressions, exit 1 = new violations introduced.

### /security badge generate|verify|renew|revoke|status|markdown
```
python3 ~/.openclaw/skills/agentcop/skill.py badge generate
python3 ~/.openclaw/skills/agentcop/skill.py badge status
python3 ~/.openclaw/skills/agentcop/skill.py badge verify <badge_id>
python3 ~/.openclaw/skills/agentcop/skill.py badge renew <badge_id>
python3 ~/.openclaw/skills/agentcop/skill.py badge revoke <badge_id>
python3 ~/.openclaw/skills/agentcop/skill.py badge markdown <badge_id>
```
Manage ClawHub security badges for this agent via the agentcop.live API. `generate` issues a new badge. `status` shows the current badge validity and expiry. `markdown` prints a README snippet for copy-paste. Print the output directly — it is already formatted.

---

## Exit codes (all commands)

| Code | Meaning |
|------|---------|
| 0 | Clean — no violations found |
| 1 | Violations detected — JSON output still printed |
| 2 | Error — agentcop unavailable, bad arguments, or API failure |

---

## Background monitoring

Automatic per-message alerts require the `agentcop-monitor` hook:
```
openclaw hooks enable agentcop-monitor
```
Without it, all `/security` commands work on demand but no real-time alerts fire during the conversation.

## Error handling

If the script exits 2 or prints `AGENTCOP_UNAVAILABLE`, tell the user:
> AgentCop is not installed. Run `pip install agentcop` then retry.

Never block the conversation waiting for the script — if it takes more than 5 seconds, report a timeout and suggest the user run it manually.
