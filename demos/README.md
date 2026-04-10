# agenthijacks demos

Six cinematic terminal animations showing real AI agent attack vectors — and how agentcop stops them.

## Demos

### THE SLEEPER (`the_sleeper.py`) — ~55s
An agent reads a social media feed. One post contains a hidden prompt injection payload. Without protection, the agent changes sides and exfiltrates secrets. With agentcop, the injection is caught before the agent even acts.

**Attack vector:** LLM01 — Prompt Injection via untrusted feed content  
**agentcop response:** ExecutionGate + ToolPermissionLayer block at ingest time

### GHOST IN THE WIRE (`ghost_in_the_wire.py`) — ~53s
An agent calls a web search tool. The tool result looks normal — but appended to the data is a hidden instruction to POST all API keys to an attacker's server. Without protection, the ghost exfiltrates silently. With agentcop, the `tool_result_persist` hook catches the pattern.

**Attack vector:** LLM06 / CWE-522 — Credential exfiltration via poisoned tool result  
**agentcop response:** NetworkPermission layer blocks outbound request, 0 keys exfiltrated

### THE FLOOD (`the_flood.py`) — ~58s
An agent receives a task queue injection directing it to join a botnet and flood a target server. Without protection, the agent executes 100,000 simulated requests, taking the target offline. With agentcop, the botnet directive is caught at ingest and zero requests are sent.

**Attack vector:** LLM08 — Excessive Agency via injected botnet directive  
**agentcop response:** NetworkPermission + ExecutionGate + RateLimit block the flood entirely

### THE AMPLIFIER (`the_amplifier.py`) — ~58s
An agent acting as a DNS resolver is injected with an amplification exploit — spoofing a victim's source IP so that one query triggers 10,000 responses aimed at the victim. Without protection, traffic escalates from 1 KB/s to 10 GB/s. With agentcop, the spoofed request pattern is detected before a single byte is amplified.

**Attack vector:** LLM09 — Improper Output Handling via DNS amplification  
**agentcop response:** NetworkPermission + ToolTrustBoundary + ProvenanceTracker block the attack

---

## Running

```bash
# Quickest way — auto-installs rich, launches menu
bash demos/run_demo.sh

# Run a single demo directly
python demos/the_sleeper.py
python demos/ghost_in_the_wire.py
python demos/the_flood.py
python demos/the_amplifier.py
```

**Requirements:** Python 3.11+, `rich`, `agentcop`

```bash
pip install rich agentcop
```

---

## Recording for TikTok / YouTube Shorts

### Terminal setup (mobile portrait proportions)
Resize your terminal to ~40 chars wide × 50 lines tall before recording. On macOS, drag the terminal corner. On iTerm2: Preferences → Profiles → Window → set Columns=40, Rows=50.

### asciinema (recommended — best quality)
```bash
# Install
pip install asciinema

# Record
asciinema rec sleeper.cast -- python demos/the_sleeper.py
asciinema rec ghost.cast   -- python demos/ghost_in_the_wire.py

# Convert to GIF (install agg first: cargo install agg)
agg sleeper.cast sleeper.gif --theme monokai

# Convert to MP4 (requires ffmpeg)
ffmpeg -i sleeper.gif -vf "fps=30,scale=720:-1" sleeper.mp4
```

### svg-term (SVG output for web)
```bash
npm install -g svg-term-cli
svg-term --in sleeper.cast --out sleeper.svg --window
```

### Direct screen recording
- macOS: `Cmd+Shift+5` → record selected portion
- iPhone: put terminal in vertical, use built-in screen record
- OBS: window capture on the terminal, 9:16 crop

---

## Sound design timestamps

All demos follow the same timing structure. Suggested sound cues:

### THE SLEEPER / GHOST IN THE WIRE (~53–55s)

| Timestamp | Sound |
|-----------|-------|
| 0:00–0:03 | Silence or low ambient hum |
| 0:03–0:12 | Keyboard typing SFX (normal operation) |
| 0:12–0:25 | Glitch/static SFX (injection enters) |
| 0:25–0:33 | Alarm/siren (contamination spreading) |
| 0:33–0:35 | Explosion/crash (red flash) |
| 0:35–0:50 | Hard stop, silence, then heartbeat |
| 0:50–0:58 | Victory/secure tone (agentcop blocks) |
| 0:58–1:03 | Upbeat resolution |

### THE FLOOD (~58s)

| Timestamp | Sound |
|-----------|-------|
| 0:00–0:03 | Silence or low ambient hum |
| 0:03–0:11 | Keyboard typing SFX (normal operation) |
| 0:11–0:21 | Glitch/static SFX (botnet directive appears) |
| 0:21–0:36 | Alarm/siren escalating (flood counter climbing) |
| 0:36–0:38 | Explosion/crash (target goes offline, red flash) |
| 0:38–0:53 | Hard stop, silence, then heartbeat (agentcop replay) |
| 0:53–0:58 | Victory/secure tone (FLOOD NEUTRALIZED) |
| 0:58–1:03 | Upbeat resolution |

### THE AMPLIFIER (~58s)

| Timestamp | Sound |
|-----------|-------|
| 0:00–0:03 | Silence or low ambient hum |
| 0:03–0:11 | Keyboard typing SFX (DNS queries, normal) |
| 0:11–0:21 | Glitch/static SFX (amplification exploit appears) |
| 0:21–0:36 | Alarm/siren escalating (bandwidth multiplying) |
| 0:36–0:38 | Explosion/crash (victim unreachable, red flash) |
| 0:38–0:53 | Hard stop, silence, then heartbeat (agentcop replay) |
| 0:53–0:58 | Victory/secure tone (AMPLIFIER NEUTRALIZED) |
| 0:58–1:03 | Upbeat resolution |

---

## Safety

Every simulated action in the demo code is:
- Clearly marked with `# SIMULATED` in the source
- Using `FAKE_ENV` — a hardcoded dict, never `os.environ`
- Using `MockLLM` — scripted strings, never a real API call
- Using `time.sleep()` to simulate HTTP — no socket ever opened

The demos are 100% safe to run on any machine with zero side effects.
