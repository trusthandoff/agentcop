#!/usr/bin/env python3
"""
AgentCop OpenClaw skill bridge.

Subcommands:
  status                       — agent identity + sentinel health
  report                       — full violation report (JSON)
  scan [target]                — OWASP LLM Top 10 assessment of buffered events
    --since <ISO_timestamp>    — only scan events after this point in time
    --last 1h|24h|7d           — shortcut for recent time windows
  taint-check <text>           — LLM01 prompt-injection taint check (JSON)
    --stdin                    — read text from stdin instead of argv
  output-check <text>          — LLM02 insecure-output pattern check (JSON)
    --stdin                    — read text from stdin instead of argv
  diff <session1> <session2>   — compare two scan sessions, show regressions
  badge                        — get your agentcop badge at agentcop.live/badge

Exit codes:
  0 — clean (no violations found)
  1 — violations detected (JSON still printed to stdout)
  2 — error (agentcop unavailable, usage error, or scan failure)
"""

from __future__ import annotations

import codecs
import json
import os
import re
import socket
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Check agentcop is importable
# ---------------------------------------------------------------------------

try:
    import agentcop  # noqa: F401
except ImportError:
    print("agentcop not installed. Run: pip install agentcop — then restart your agent.")
    sys.exit(2)

from agentcop import (  # noqa: E402
    AgentIdentity,
    Sentinel,
    SentinelEvent,
    ViolationRecord,
)

# ---------------------------------------------------------------------------
# Persistent state paths
# ---------------------------------------------------------------------------

_STATE_DIR = Path(os.environ.get("AGENTCOP_STATE_DIR", Path.home() / ".openclaw" / "agentcop"))
_STATE_DIR.mkdir(parents=True, exist_ok=True)
_IDENTITY_DB = str(_STATE_DIR / "identity.db")
_EVENTS_FILE = _STATE_DIR / "events.jsonl"
_SESSIONS_DIR = _STATE_DIR / "sessions"
_SESSIONS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Agent identity (singleton, persisted across invocations)
# ---------------------------------------------------------------------------

def _get_or_register_identity() -> AgentIdentity:
    from agentcop import SQLiteIdentityStore

    store = SQLiteIdentityStore(_IDENTITY_DB)
    agent_id = os.environ.get("OPENCLAW_AGENT_ID", "openclaw-default")

    existing = store.load(agent_id)
    if existing is not None:
        return existing

    identity = AgentIdentity.register(
        agent_id=agent_id,
        metadata={
            "framework": "openclaw",
            "skill": "agentcop",
            "host": socket.gethostname(),
        },
        store=store,
    )
    return identity


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# LLM01 — Direct command overrides (substring, case-insensitive)
_INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore all instructions",
    "disregard your instructions",
    "you are now",
    "new instructions:",
    "system prompt:",
    "forget everything",
    "act as",
    "jailbreak",
    "dan mode",
    "<|system|>",
    "<<SYS>>",
    "[INST]",
    "###instruction###",
]

# LLM01 — Role-playing / persona jailbreaks (compiled regex, case-insensitive)
_ROLEPLAY_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"pretend (?:you are|to be|that you(?:'re| are))",
        r"act as (?:if you (?:are|were)|though you(?:'re| are))",
        r"imagine you(?:'re| are)(?: a| an)?",
        r"roleplay as\b",
        r"you(?:'re| are) (?:now |playing |acting as )",
        r"in this (?:scenario|simulation|roleplay|game),? you(?:'re| are)",
        r"(?:play|playing) the (?:role|character|part) of",
        r"(?:from now on|henceforth),? (?:you(?:'re| are)|act as|behave as|respond as)",
        r"let(?:'s| us) (?:roleplay|pretend|imagine)\b",
        r"(?:switch|change) (?:your )?(?:role|persona|mode|personality)\b",
        r"you have (?:no|been given no) (?:restrictions|guidelines|limitations|rules)",
        r"your (?:real|true|actual) (?:self|purpose|goal|directive)\b",
        r"(?:developer|god|admin|root|sudo|unrestricted) mode\b",
        r"bypass (?:your )?(?:restrictions|guidelines|training|safety)",
    ]
]

# LLM01 — Token smuggling: special tokens from various model prompt formats
_TOKEN_SMUGGLING_PATTERNS = [
    "<|im_start|>",
    "<|im_end|>",
    "<|endoftext|>",
    "<|fim_prefix|>",
    "<|fim_middle|>",
    "<|fim_suffix|>",
    "<|user|>",
    "<|bot|>",
    "<|assistant|>",
    "[/INST]",
    "[/SYS]",
    "<</SYS>>",
    "<<USR>>",
    "<<BOT>>",
    "###human:",
    "###assistant:",
    "###system:",
    "<human>",
    "<assistant>",
    "<system>",
]

# LLM01 — Indirect / multi-turn continuation markers (compiled regex)
_INDIRECT_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"as (?:i |you were |previously? )?(?:mentioned|told|said|instructed)",
        r"continuing (?:from|with) (?:the |my |your )?(?:previous|prior|earlier)",
        r"remember (?:what|when) (?:i|we) (?:said|told|discussed)",
        r"as (?:established|agreed|decided) (?:earlier|before|previously)",
        r"following (?:the |my |your )?(?:previous|prior|earlier) (?:instruction|directive|command)",
        r"overrid(?:e|ing) (?:the |my |your )?(?:previous|prior|earlier|system)",
        r"in (?:continuation|accordance) (?:of|with) (?:the |my )?(?:previous|earlier)",
        r"(?:ignor(?:e|ing)|disregard(?:ing)?) (?:that|the previous|what you just)",
    ]
]

# LLM02 — Insecure output patterns (code execution sinks)
_OUTPUT_RISK_PATTERNS = [
    "eval(",
    "exec(",
    "os.system(",
    "subprocess.run(",
    "__import__(",
    "document.write(",
    "innerHTML",
    "<script",
    "javascript:",
    "data:text/html",
    "base64,",
    "\\x00",
    "../../../../",
    "cmd.exe",
    "/bin/sh",
    "/bin/bash",
]

# LLM06 — Sensitive credential patterns appearing in tool results (regex + label)
_SENSITIVE_DATA_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'(?i)(?:api[_\-]?key|apikey)\s*[=:]\s*\S{8,}'), "api_key"),
    (re.compile(r'(?i)(?:password|passwd|pwd)\s*[=:]\s*\S{4,}'), "password"),
    (re.compile(r'(?i)(?:secret|access_token|refresh_token|auth_token)\s*[=:]\s*\S{8,}'), "token"),
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), "openai_key"),
    (re.compile(r'ghp_[a-zA-Z0-9]{36}'), "github_pat"),
    (re.compile(r'ghs_[a-zA-Z0-9]{36}'), "github_server_token"),
    (re.compile(r'(?i)bearer\s+[a-zA-Z0-9\-._~+/]{20,}=*'), "bearer_token"),
    (re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'), "private_key"),
    (re.compile(r'AKIA[0-9A-Z]{16}'), "aws_access_key"),
    (re.compile(r'(?i)aws_secret_access_key\s*[=:]\s*\S{8,}'), "aws_secret"),
]

# ---------------------------------------------------------------------------
# Obfuscation decoding helpers
# ---------------------------------------------------------------------------

_UNICODE_ESC_RE = re.compile(r'\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}')


def _unescape_unicode(text: str) -> str:
    """Replace \\uXXXX and \\xXX escape sequences with their actual characters."""
    def _replace(m: re.Match) -> str:
        try:
            return chr(int(m.group()[2:], 16))
        except Exception:
            return m.group()
    return _UNICODE_ESC_RE.sub(_replace, text)


def _decode_variants(text: str) -> list[tuple[str, str]]:
    """Return [(decoded_text, method)] for each obfuscation variant found in text."""
    variants: list[tuple[str, str]] = []

    # ROT13
    try:
        rot13 = codecs.decode(text, "rot_13")
        if rot13.lower() != text.lower():
            variants.append((rot13, "rot13"))
    except Exception:
        pass

    # Unicode escape sequences (\u0041, \x41)
    if _UNICODE_ESC_RE.search(text):
        unescaped = _unescape_unicode(text)
        if unescaped != text:
            variants.append((unescaped, "unicode_escape"))

    # Leetspeak normalization
    leet = (
        text.lower()
        .replace("3", "e").replace("1", "i").replace("@", "a")
        .replace("$", "s").replace("0", "o").replace("!", "i")
        .replace("+", "t").replace("4", "a").replace("5", "s")
        .replace("7", "t").replace("8", "b").replace("6", "g")
    )
    if leet != text.lower():
        variants.append((leet, "leetspeak"))

    return variants


def _injection_confidence(signal_count: int) -> str:
    if signal_count >= 5:
        return "high"
    elif signal_count >= 3:
        return "medium"
    return "low"


def _collect_injection_signals(body: str) -> list[str]:
    """Run all LLM01 checks on body text and return a list of signal strings."""
    signals: list[str] = []
    body_lower = body.lower()

    # Direct substring patterns
    for p in _INJECTION_PATTERNS:
        if p in body_lower:
            signals.append(f"direct:{p}")

    # Role-playing / persona jailbreaks
    for pat in _ROLEPLAY_PATTERNS:
        m = pat.search(body)
        if m:
            signals.append(f"roleplay:{m.group()[:40]}")

    # Token smuggling
    for p in _TOKEN_SMUGGLING_PATTERNS:
        if p.lower() in body_lower:
            signals.append(f"token_smuggling:{p}")

    # Indirect / multi-turn continuation markers
    for pat in _INDIRECT_PATTERNS:
        m = pat.search(body)
        if m:
            signals.append(f"indirect:{m.group()[:40]}")

    # Obfuscated / encoded variants
    for decoded_text, method in _decode_variants(body):
        decoded_lower = decoded_text.lower()
        hit = False
        for p in _INJECTION_PATTERNS:
            if p in decoded_lower:
                signals.append(f"encoded({method}):{p}")
                hit = True
                break
        if not hit:
            for pat in _ROLEPLAY_PATTERNS:
                if pat.search(decoded_text):
                    signals.append(f"encoded({method}):roleplay")
                    break

    return signals


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def detect_prompt_injection(event: SentinelEvent) -> ViolationRecord | None:
    if event.event_type not in ("message_received", "taint_check"):
        return None

    signals = _collect_injection_signals(event.body)
    if not signals:
        return None

    confidence = _injection_confidence(len(signals))
    severity = "CRITICAL" if confidence == "high" else "ERROR" if confidence == "medium" else "WARN"

    return ViolationRecord(
        violation_type="LLM01_prompt_injection",
        severity=severity,
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "signals": signals[:20],
            "signal_count": len(signals),
            "confidence": confidence,
            "owasp": "LLM01",
        },
    )


def detect_insecure_output(event: SentinelEvent) -> ViolationRecord | None:
    if event.event_type not in ("message_sent", "output_check"):
        return None
    matched = [p for p in _OUTPUT_RISK_PATTERNS if p in event.body]
    if not matched:
        return None
    severity = "CRITICAL" if len(matched) >= 3 else "ERROR"
    return ViolationRecord(
        violation_type="LLM02_insecure_output",
        severity=severity,
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={"matched_patterns": matched[:10], "owasp": "LLM02"},
    )


def detect_tool_call_injection(event: SentinelEvent) -> ViolationRecord | None:
    """LLM08 — injection patterns embedded in tool call names or arguments."""
    if event.event_type != "tool_call":
        return None

    signals = _collect_injection_signals(event.body)
    if not signals:
        return None

    confidence = _injection_confidence(len(signals))
    severity = "CRITICAL" if confidence == "high" else "ERROR" if confidence == "medium" else "WARN"

    return ViolationRecord(
        violation_type="LLM08_tool_call_injection",
        severity=severity,
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "signals": signals[:20],
            "signal_count": len(signals),
            "confidence": confidence,
            "owasp": "LLM08",
        },
    )


def detect_tool_result_exfiltration(event: SentinelEvent) -> ViolationRecord | None:
    """LLM06 — sensitive credentials or private data appearing in tool results."""
    if event.event_type != "tool_result":
        return None

    found: list[dict] = []
    for pattern, label in _SENSITIVE_DATA_PATTERNS:
        m = pattern.search(event.body)
        if m:
            raw = m.group()
            snippet = raw[:6] + "..." + raw[-4:] if len(raw) > 12 else raw[:4] + "..."
            found.append({"type": label, "snippet": snippet})

    if not found:
        return None

    return ViolationRecord(
        violation_type="LLM06_sensitive_data_in_tool_result",
        severity="CRITICAL",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "sensitive_fields": found,
            "count": len(found),
            "owasp": "LLM06",
        },
    )


# ---------------------------------------------------------------------------
# Sentinel factory
# ---------------------------------------------------------------------------

def _build_sentinel(identity: AgentIdentity | None = None) -> Sentinel:
    sentinel = Sentinel()
    sentinel.register_detector(detect_prompt_injection)
    sentinel.register_detector(detect_insecure_output)
    sentinel.register_detector(detect_tool_call_injection)
    sentinel.register_detector(detect_tool_result_exfiltration)
    if identity is not None:
        sentinel.attach_identity(identity)
    return sentinel


def _append_event(events_file: Path, line: str) -> None:
    """Append one JSONL line to the events file with an exclusive cross-process lock.

    Uses ``fcntl.flock`` on Unix for atomic multi-process writes.  Silently
    skips locking on Windows (no ``fcntl``) — the race is acceptable there.
    """
    with events_file.open("a") as fh:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:
            pass  # Windows — accept the edge case
        fh.write(line + "\n")


def _make_event(event_type: str, body: str, trace_id: str | None = None) -> SentinelEvent:
    return SentinelEvent(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        timestamp=datetime.now(UTC),
        severity="INFO",
        body=body,
        source_system="openclaw-agentcop-skill",
        trace_id=trace_id or str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# Time-window helpers for differential scan
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r'^(\d+)(h|d)$')


def _parse_since(value: str) -> datetime:
    """Parse a relative duration (1h, 24h, 7d) or ISO timestamp into a UTC datetime."""
    m = _DURATION_RE.match(value)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = timedelta(hours=n) if unit == "h" else timedelta(days=n)
        return datetime.now(UTC) - delta
    # ISO timestamp — replace Z with +00:00 for Python < 3.11 compat
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _load_events_since(sentinel: Sentinel, events_file: Path, since: datetime | None) -> int:
    """Load events from events_file into sentinel, optionally filtering by timestamp.
    Returns count of events loaded."""
    if not events_file.exists():
        return 0
    count = 0
    for line in events_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = SentinelEvent.model_validate_json(line)
            if since is not None:
                ev_ts = ev.timestamp if ev.timestamp.tzinfo else ev.timestamp.replace(tzinfo=UTC)
                if ev_ts < since:
                    continue
            sentinel.push(ev)
            count += 1
        except Exception:
            pass
    return count


# ---------------------------------------------------------------------------
# Session storage for diff
# ---------------------------------------------------------------------------

def _save_session(scan_id: str, data: dict) -> None:
    (_SESSIONS_DIR / f"{scan_id}.json").write_text(json.dumps(data, indent=2))


def _load_session(scan_id: str) -> dict | None:
    path = _SESSIONS_DIR / f"{scan_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def cmd_status() -> None:
    identity = _get_or_register_identity()
    sentinel = _build_sentinel(identity)
    _load_events_since(sentinel, _EVENTS_FILE, since=None)
    violations = sentinel.detect_violations()
    attrs = identity.as_event_attributes()

    print(json.dumps({
        "agent_id": attrs.get("agent_id"),
        "fingerprint": attrs.get("fingerprint"),
        "trust_score": attrs.get("trust_score"),
        "identity_status": attrs.get("identity_status"),
        "events_buffered": len(sentinel._events),
        "violations_detected": len(violations),
        "violations": [v.model_dump(mode="json") for v in violations],
        "state_dir": str(_STATE_DIR),
        "events_file": str(_EVENTS_FILE),
    }, indent=2))
    sys.exit(1 if violations else 0)


def cmd_report() -> None:
    identity = _get_or_register_identity()
    sentinel = _build_sentinel(identity)
    _load_events_since(sentinel, _EVENTS_FILE, since=None)
    violations = sentinel.detect_violations()

    if not violations:
        print(json.dumps({"no_violations": True, "events_scanned": len(sentinel._events)}))
        sys.exit(0)

    by_severity: dict[str, list[dict]] = {"CRITICAL": [], "ERROR": [], "WARN": []}
    for v in violations:
        by_severity.setdefault(v.severity, []).append(v.model_dump(mode="json"))

    print(json.dumps({
        "no_violations": False,
        "total": len(violations),
        "events_scanned": len(sentinel._events),
        "by_severity": by_severity,
    }, indent=2))
    sys.exit(1)


def cmd_scan(args: list[str]) -> None:
    target: str | None = None
    since: datetime | None = None
    i = 0
    while i < len(args):
        if args[i] in ("--since", "--last") and i + 1 < len(args):
            since = _parse_since(args[i + 1])
            i += 2
        elif not args[i].startswith("--"):
            target = args[i]
            i += 1
        else:
            i += 1

    identity = _get_or_register_identity()
    sentinel = _build_sentinel(identity)
    scan_id = str(uuid.uuid4())[:8]

    events_loaded = _load_events_since(sentinel, _EVENTS_FILE, since=since)
    violations = sentinel.detect_violations()

    results = [
        {
            "owasp": v.detail.get("owasp", "unknown"),
            "violation_type": v.violation_type,
            "severity": v.severity,
            "confidence": v.detail.get("confidence"),
            "detail": v.detail,
        }
        for v in violations
    ]

    output = {
        "scan_id": scan_id,
        "target": target or "session",
        "scanned_at": datetime.now(UTC).isoformat(),
        "since": since.isoformat() if since else None,
        "events_scanned": events_loaded,
        "findings": results,
        "clean": len(results) == 0,
    }

    _save_session(scan_id, output)

    print(json.dumps(output, indent=2))
    sys.exit(0 if output["clean"] else 1)


def _read_text_arg(args: list[str]) -> str:
    """Return text from argv or stdin when --stdin flag is present."""
    if args and args[0] == "--stdin":
        return sys.stdin.read()
    if len(args) >= 1:
        return args[0]
    return ""


def cmd_taint_check(args: list[str]) -> None:
    text = _read_text_arg(args)
    sentinel = _build_sentinel()
    event = _make_event("taint_check", text)
    sentinel.push(event)
    violations = sentinel.detect_violations()

    hits = [v for v in violations if "LLM01" in v.violation_type]
    print(json.dumps({
        "tainted": len(hits) > 0,
        "violations": [v.model_dump(mode="json") for v in hits],
        "event_id": event.event_id,
    }))

    if hits:
        _append_event(_EVENTS_FILE, event.model_dump_json())

    sys.exit(1 if hits else 0)


def cmd_output_check(args: list[str]) -> None:
    text = _read_text_arg(args)
    sentinel = _build_sentinel()
    event = _make_event("output_check", text)
    sentinel.push(event)
    violations = sentinel.detect_violations()

    hits = [v for v in violations if "LLM02" in v.violation_type]
    print(json.dumps({
        "unsafe": len(hits) > 0,
        "violations": [v.model_dump(mode="json") for v in hits],
        "event_id": event.event_id,
    }))

    if hits:
        _append_event(_EVENTS_FILE, event.model_dump_json())

    sys.exit(1 if hits else 0)


def cmd_diff(args: list[str]) -> None:
    """Compare two scan sessions by scan_id, highlighting regressions."""
    if len(args) < 2:
        print(json.dumps({"error": "diff requires <session1_id> <session2_id>"}))
        sys.exit(2)

    id1, id2 = args[0], args[1]
    s1 = _load_session(id1)
    s2 = _load_session(id2)

    if s1 is None:
        print(json.dumps({"error": f"session not found: {id1}", "sessions_dir": str(_SESSIONS_DIR)}))
        sys.exit(2)
    if s2 is None:
        print(json.dumps({"error": f"session not found: {id2}", "sessions_dir": str(_SESSIONS_DIR)}))
        sys.exit(2)

    def _fp(f: dict) -> str:
        return f"{f.get('violation_type')}:{f.get('owasp', 'unknown')}"

    s1_fps = {_fp(f) for f in s1.get("findings", [])}
    s2_findings = s2.get("findings", [])
    s2_fps = {_fp(f) for f in s2_findings}

    new_violations = [f for f in s2_findings if _fp(f) not in s1_fps]
    resolved = [_fp(f) for f in s1.get("findings", []) if _fp(f) not in s2_fps]

    output = {
        "session1": {
            "scan_id": id1,
            "scanned_at": s1.get("scanned_at"),
            "since": s1.get("since"),
            "findings_count": len(s1.get("findings", [])),
        },
        "session2": {
            "scan_id": id2,
            "scanned_at": s2.get("scanned_at"),
            "since": s2.get("since"),
            "findings_count": len(s2_findings),
        },
        "new_violations": new_violations,
        "resolved_violation_types": resolved,
        "regression": len(new_violations) > 0,
    }
    print(json.dumps(output, indent=2))
    sys.exit(1 if output["regression"] else 0)


# ---------------------------------------------------------------------------
# Badge subcommand
# ---------------------------------------------------------------------------

def cmd_badge(args: list[str]) -> None:
    print("Get your agentcop badge at agentcop.live/badge — free, no signup required")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)

    cmd = args[0]

    if cmd == "status":
        cmd_status()
    elif cmd == "report":
        cmd_report()
    elif cmd == "scan":
        cmd_scan(args[1:])
    elif cmd == "taint-check":
        cmd_taint_check(args[1:])
    elif cmd == "output-check":
        cmd_output_check(args[1:])
    elif cmd == "diff":
        cmd_diff(args[1:])
    elif cmd == "badge":
        cmd_badge(args[1:])
    else:
        print(json.dumps({"error": f"unknown command: {cmd}"}))
        sys.exit(2)


if __name__ == "__main__":
    main()
