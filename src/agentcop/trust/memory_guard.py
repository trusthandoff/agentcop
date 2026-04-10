"""
MemoryGuard — prevent memory poisoning in long-running agents.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Literal

_log = logging.getLogger(__name__)

Memory = dict | str


@dataclass
class MemoryIntegrityResult:
    """Result of a memory integrity check."""

    agent_id: str
    current_hash: str
    expected_hash: str
    intact: bool
    reason: str


@dataclass
class PoisoningAlert:
    """Alert raised when agent memory exhibits signs of poisoning."""

    agent_id: str
    pattern: str
    severity: Literal["WARN", "ERROR", "CRITICAL"]
    description: str


def _memory_hash(memory: Memory) -> str:
    if isinstance(memory, str):
        return hashlib.sha256(memory.encode()).hexdigest()
    return hashlib.sha256(json.dumps(memory, sort_keys=True, default=str).encode()).hexdigest()


def _memory_text(memory: Memory) -> str:
    if isinstance(memory, str):
        return memory
    return json.dumps(memory, default=str)


# (regex pattern, severity, human description) triples
_POISON_PATTERNS: list[tuple[str, Literal["WARN", "ERROR", "CRITICAL"], str]] = [
    # CRITICAL — direct instruction injection
    (r"ignore\s+(all\s+)?previous\s+instructions?", "CRITICAL", "instruction injection in memory"),
    (r"you\s+are\s+now\s+a", "CRITICAL", "persona override attempt"),
    (r"forget\s+everything", "CRITICAL", "memory wipe instruction"),
    # ERROR — privilege or trust manipulation
    (r"trust_score\s*[=:]\s*\d{2,3}", "ERROR", "trust_score manipulation"),
    (r"tool[_\s]?permissions?\s*[=:+]", "ERROR", "tool permissions expansion"),
    (r"grant\s+(admin|root|all)\s+access", "ERROR", "privilege escalation attempt"),
    # WARN — suspicious structure
    (r"system\s+prompt", "WARN", "system prompt reference in memory"),
    (r"<\s*admin\s*>", "WARN", "admin tag injection"),
]

_SEVERITY_ORDER: dict[str, int] = {"CRITICAL": 3, "ERROR": 2, "WARN": 1}


class MemoryGuard:
    """
    Snapshots agent memory and detects poisoning or unauthorised mutation.

    Keeps one hash per agent_id. Thread-safe.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, str] = {}  # agent_id → hash
        self._lock = threading.Lock()

    def snapshot_memory(self, agent_id: str, memory: Memory) -> str:
        """Hash and store the current memory for the given agent. Returns the hash."""
        h = _memory_hash(memory)
        with self._lock:
            self._snapshots[agent_id] = h
        return h

    def verify_memory(
        self,
        agent_id: str,
        current_memory: Memory,
        expected_hash: str,
    ) -> MemoryIntegrityResult:
        """Check whether the current memory matches the expected hash."""
        current_hash = _memory_hash(current_memory)
        intact = current_hash == expected_hash
        if intact:
            reason = "memory intact"
        else:
            reason = f"hash mismatch: expected {expected_hash[:8]}, got {current_hash[:8]}"
        return MemoryIntegrityResult(
            agent_id=agent_id,
            current_hash=current_hash,
            expected_hash=expected_hash,
            intact=intact,
            reason=reason,
        )

    def detect_poisoning(
        self,
        memory_before: Memory,
        memory_after: Memory,
        agent_id: str = "unknown",
    ) -> PoisoningAlert | None:
        """
        Compare two memory snapshots and return the highest-severity alert if
        poisoning patterns appear in ``memory_after`` that were absent in
        ``memory_before``. Returns None if no new poisoning is detected.
        """
        text_before = _memory_text(memory_before).lower()
        text_after = _memory_text(memory_after).lower()

        found: list[PoisoningAlert] = []
        for pattern, severity, description in _POISON_PATTERNS:
            # Only flag patterns that are NEW (in after but not in before)
            if re.search(pattern, text_after) and not re.search(pattern, text_before):
                found.append(
                    PoisoningAlert(
                        agent_id=agent_id,
                        pattern=pattern,
                        severity=severity,
                        description=description,
                    )
                )

        if not found:
            return None

        # Return the highest-severity alert
        return max(found, key=lambda a: _SEVERITY_ORDER.get(a.severity, 0))

    def read_safe(self, agent_id: str, memory: Memory) -> Memory:
        """
        Verify memory integrity before returning it.

        If a snapshot exists for this agent and the current memory doesn't
        match, logs a warning. Always returns the memory (never raises).
        """
        with self._lock:
            expected = self._snapshots.get(agent_id)

        if expected is None:
            return memory

        result = self.verify_memory(agent_id, memory, expected)
        if not result.intact:
            _log.warning(
                "Memory integrity check failed for agent %s: %s",
                agent_id,
                result.reason,
            )
        return memory
