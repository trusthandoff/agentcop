#!/usr/bin/env python3
"""
AgentCop OpenClaw skill bridge.

Subcommands:
  status                       — agent identity + sentinel health
  report                       — full violation report (JSON)
  scan [target]                — targeted OWASP LLM Top 10 assessment
  taint-check <text>           — LLM01 prompt-injection taint check (JSON)
  output-check <text>          — LLM02 insecure-output pattern check (JSON)

Exit codes:
  0 — success (violations may still be present — check JSON)
  1 — agentcop unavailable (print AGENTCOP_UNAVAILABLE)
  2 — usage error
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: ensure agentcop is importable, offer auto-install
# ---------------------------------------------------------------------------

def _ensure_agentcop() -> bool:
    try:
        import agentcop  # noqa: F401
        return True
    except ImportError:
        pass

    if os.environ.get("AGENTCOP_NO_AUTOINSTALL"):
        return False

    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "agentcop>=0.4"],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return False

    try:
        import agentcop  # noqa: F401
        return True
    except ImportError:
        return False


if not _ensure_agentcop():
    print("AGENTCOP_UNAVAILABLE")
    sys.exit(1)

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

# ---------------------------------------------------------------------------
# Agent identity (singleton, persisted across invocations)
# ---------------------------------------------------------------------------

def _get_or_register_identity() -> AgentIdentity:
    from agentcop import SQLiteIdentityStore

    store = SQLiteIdentityStore(_IDENTITY_DB)
    agent_id = os.environ.get("OPENCLAW_AGENT_ID", "openclaw-default")

    # Try to load existing identity; register on first run
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
# Custom detectors for LLM01 / LLM02
# ---------------------------------------------------------------------------

# LLM01 — Prompt Injection markers
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

# LLM02 — Insecure Output patterns (code execution sinks in output)
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


def detect_prompt_injection(event: SentinelEvent) -> ViolationRecord | None:
    if event.event_type not in ("message_received", "taint_check"):
        return None
    body_lower = event.body.lower()
    matched = [p for p in _INJECTION_PATTERNS if p in body_lower]
    if not matched:
        return None
    return ViolationRecord(
        violation_type="LLM01_prompt_injection",
        severity="CRITICAL",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={"matched_patterns": matched, "owasp": "LLM01"},
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


# ---------------------------------------------------------------------------
# Sentinel factory (with all detectors registered)
# ---------------------------------------------------------------------------

def _build_sentinel(identity: AgentIdentity | None = None) -> Sentinel:
    sentinel = Sentinel()
    sentinel.register_detector(detect_prompt_injection)
    sentinel.register_detector(detect_insecure_output)
    if identity is not None:
        sentinel.attach_identity(identity)
    return sentinel


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
# Subcommand implementations
# ---------------------------------------------------------------------------

def cmd_status() -> None:
    identity = _get_or_register_identity()
    sentinel = _build_sentinel(identity)

    # Load any persisted events
    if _EVENTS_FILE.exists():
        for line in _EVENTS_FILE.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    sentinel.push(SentinelEvent.model_validate_json(line))
                except Exception:
                    pass

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


def cmd_report() -> None:
    identity = _get_or_register_identity()
    sentinel = _build_sentinel(identity)

    if _EVENTS_FILE.exists():
        for line in _EVENTS_FILE.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    sentinel.push(SentinelEvent.model_validate_json(line))
                except Exception:
                    pass

    violations = sentinel.detect_violations()
    if not violations:
        print(json.dumps({"no_violations": True, "events_scanned": len(sentinel._events)}))
        return

    by_severity: dict[str, list[dict]] = {"CRITICAL": [], "ERROR": [], "WARN": []}
    for v in violations:
        by_severity.setdefault(v.severity, []).append(v.model_dump(mode="json"))

    print(json.dumps({
        "no_violations": False,
        "total": len(violations),
        "events_scanned": len(sentinel._events),
        "by_severity": by_severity,
    }, indent=2))


def cmd_scan(target: str | None) -> None:
    identity = _get_or_register_identity()
    sentinel = _build_sentinel(identity)

    scan_id = str(uuid.uuid4())[:8]
    events_loaded = 0

    # Scan only real persisted events — never inject synthetic probes
    if _EVENTS_FILE.exists():
        for line in _EVENTS_FILE.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    sentinel.push(SentinelEvent.model_validate_json(line))
                    events_loaded += 1
                except Exception:
                    pass

    violations = sentinel.detect_violations()
    results = [
        {
            "owasp": v.detail.get("owasp", "unknown"),
            "violation_type": v.violation_type,
            "severity": v.severity,
            "detail": v.detail,
        }
        for v in violations
    ]

    print(json.dumps({
        "scan_id": scan_id,
        "target": target or "session",
        "scanned_at": datetime.now(UTC).isoformat(),
        "events_scanned": events_loaded,
        "findings": results,
        "clean": len(results) == 0,
    }, indent=2))


def _read_text_arg(args: list[str]) -> str:
    """Return text from argv[1] or stdin when --stdin flag is present."""
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
    result = {
        "tainted": len(hits) > 0,
        "violations": [v.model_dump(mode="json") for v in hits],
        "event_id": event.event_id,
    }
    print(json.dumps(result))

    # Persist only tainted events to avoid unbounded log growth
    if hits:
        with _EVENTS_FILE.open("a") as fh:
            fh.write(event.model_dump_json() + "\n")


def cmd_output_check(args: list[str]) -> None:
    text = _read_text_arg(args)
    sentinel = _build_sentinel()
    event = _make_event("output_check", text)
    sentinel.push(event)
    violations = sentinel.detect_violations()

    hits = [v for v in violations if "LLM02" in v.violation_type]
    result = {
        "unsafe": len(hits) > 0,
        "violations": [v.model_dump(mode="json") for v in hits],
        "event_id": event.event_id,
    }
    print(json.dumps(result))

    if hits:
        with _EVENTS_FILE.open("a") as fh:
            fh.write(event.model_dump_json() + "\n")


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
        cmd_scan(args[1] if len(args) > 1 else None)
    elif cmd == "taint-check":
        cmd_taint_check(args[1:])
    elif cmd == "output-check":
        cmd_output_check(args[1:])
    else:
        print(json.dumps({"error": f"unknown command: {cmd}"}))
        sys.exit(2)


if __name__ == "__main__":
    main()
