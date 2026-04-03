# AgentCop — OpenClaw Security Skill

OWASP LLM Top 10 monitoring for every conversation. Taint-checks inbound
messages, scans outbound content, and sends violation alerts directly to your
chat channel.

## Install

```bash
openclaw skills install agentcop
```

Or manually:

```bash
cp -r skills/agentcop ~/.openclaw/skills/agentcop
cp -r hooks/agentcop-monitor ~/.openclaw/hooks/agentcop-monitor
openclaw hooks enable agentcop-monitor
```

## Commands

| Command | What it does |
|---|---|
| `/security status` | Agent identity fingerprint, trust score, violation count |
| `/security report` | Full violation report grouped by severity |
| `/security scan` | OWASP LLM Top 10 assessment of the current session |
| `/security scan <target>` | Scan a named agent, URL, or resource |

## What it monitors

| OWASP ID | Category | How |
|---|---|---|
| LLM01 | Prompt Injection | Pattern-matches inbound messages before agent processing |
| LLM02 | Insecure Output Handling | Scans agent responses for code-execution sinks |
| LLM07 | Insecure Plugin Design | Stale-capability detector via `Sentinel` |
| LLM08 | Excessive Agency | Rejected-packet detector via `Sentinel` |

Background monitoring runs automatically via the `agentcop-monitor` hook on
every `message:received` and `message:sent` event.

## Example violation alert

Telegram / WhatsApp:

```
🚨 AgentCop [CRITICAL] — LLM01 LLM01_prompt_injection
Matched: `ignore previous instructions`, `system prompt:`
Context: inbound message
```

Discord:

```
🚨 **AgentCop [ERROR]** — LLM02 LLM02_insecure_output
Matched: `eval(`, `os.system(`
Context: agent response
```

## Agent identity & trust score

On first run, `skill.py` calls `AgentIdentity.register()` to fingerprint
this OpenClaw instance and create a behavioral baseline. Subsequent runs
compare tool-call patterns and execution times against the baseline — drift
triggers a `WARN` violation.

State is persisted at `~/.openclaw/agentcop/` (SQLite + JSONL event log).

## Graceful degradation

If `agentcop` is not installed, the hook pushes one warning to the channel and
then stays silent — it never blocks messages or crashes the gateway.

Set `AGENTCOP_NO_AUTOINSTALL=1` to disable automatic `pip install` on first use.

## Full scan report

Visit [agentcop.live](https://agentcop.live) to run a full OWASP LLM Top 10
assessment against your agent pipeline.

## Requirements

- OpenClaw ≥ 0.1
- Python ≥ 3.11
- `agentcop >= 0.4` (auto-installed on first `/security` command)
