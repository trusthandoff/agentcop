---
name: agentcop-monitor
description: "Taint-checks inbound messages for LLM01 prompt injection and scans outbound content for LLM02 insecure output patterns. Sends violation alerts directly to the user's active channel."
metadata:
  { "openclaw": { "emoji": "🛡️", "events": ["message:received", "message:sent", "tool_result_persist"], "requires": { "anyBins": ["python3", "python"] } } }
---

# AgentCop Monitor Hook

Runs on every inbound and outbound message. Calls `skill.py` to taint-check
content against OWASP LLM01 (prompt injection) and LLM02 (insecure output).

Violations are injected into `event.messages` so they appear directly in the
user's active channel (Telegram, WhatsApp, Discord, etc.) before the agent
processes the message.

## Requirements

- `python3` on PATH
- `agentcop` Python package (auto-installed on first run via `skill.py`)
- The `agentcop` skill must also be installed at `~/.openclaw/skills/agentcop/`

## Configuration

Set `AGENTCOP_NO_AUTOINSTALL=1` in your environment to prevent automatic
`pip install` on first use.

Set `AGENTCOP_STATE_DIR` to override the default `~/.openclaw/agentcop/` state
directory.
