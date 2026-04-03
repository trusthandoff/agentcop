---
name: agentcop
description: "OWASP LLM Top 10 security monitor — taint-checks messages, detects violations, alerts on policy breaches in real time"
homepage: https://agentcop.live
user-invocable: true
metadata: { "openclaw": { "emoji": "🔒", "requires": { "anyBins": ["python3", "python"] } } }
---

# AgentCop Security Skill

You are now equipped with AgentCop — a forensic security monitor for OWASP LLM Top 10 violations. The skill bridge is at `~/.openclaw/skills/agentcop/skill.py`.

## Commands

### /security status
Run:
```
python3 ~/.openclaw/skills/agentcop/skill.py status
```
Then report: agent identity fingerprint, trust score (0–1.0), number of events buffered, active watch thread status, and any violations detected since startup. Format it clearly for the user.

### /security report
Run:
```
python3 ~/.openclaw/skills/agentcop/skill.py report
```
Parse the JSON output and present a formatted violation report grouped by severity (CRITICAL → ERROR → WARN). For each violation show: type, severity, detected_at, and detail fields. If the output contains `"no_violations": true`, tell the user the session is clean.

### /security scan [target]
Run:
```
python3 ~/.openclaw/skills/agentcop/skill.py scan [target]
```
where `[target]` is the optional argument the user provided (e.g. a URL, agent name, or "session"). Present results as a structured security assessment with OWASP LLM category labels (LLM01–LLM10).

## Error handling
If the script exits non-zero or prints `AGENTCOP_UNAVAILABLE`, tell the user:
> AgentCop is not installed. Run `pip install agentcop` then retry.

Never block the conversation waiting for the script — if it takes more than 5 seconds, report a timeout and suggest the user run it manually.

## Background monitoring
Automatic security alerts require the `agentcop-monitor` hook to be installed and enabled alongside this skill (`openclaw hooks enable agentcop-monitor`). The hook checks every inbound message for prompt injection (LLM01), every outbound message for insecure output patterns (LLM02), and every tool result before it is written to the session transcript. If the hook is not enabled, the `/security` commands still work on demand but no automatic alerts are sent.
