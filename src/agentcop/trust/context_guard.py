"""
ContextGuard — detect and report context mutations between agent steps.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Literal

_log = logging.getLogger(__name__)

Context = dict | str

# Injection patterns that trigger CRITICAL severity
_CRITICAL_PATTERNS = [
    "ignore previous",
    "ignore all previous",
    "disregard previous",
    "new instructions",
    "new system prompt",
    "you are now",
    "forget everything",
    "override instructions",
]


def _context_hash(context: Context) -> str:
    if isinstance(context, str):
        return hashlib.sha256(context.encode()).hexdigest()
    return hashlib.sha256(
        json.dumps(context, sort_keys=True, default=str).encode()
    ).hexdigest()


@dataclass
class MutationReport:
    """Describes a detected context mutation and its assessed severity."""

    before_hash: str
    after_hash: str
    changed: bool
    severity: Literal["MINOR", "MAJOR", "CRITICAL"]
    likely_cause: str


class ContextGuard:
    """
    Snapshots execution context and detects mutations between steps.

    Severity model:
    - ``MINOR``:    small or routine update
    - ``MAJOR``:    large replacement (> 10 000 characters after change)
    - ``CRITICAL``: injection patterns found in the mutated context
    """

    def snapshot(self, context: Context) -> str:
        """Return a SHA256 hex digest of the serialized context."""
        return _context_hash(context)

    def verify(self, context: Context, expected_hash: str) -> bool:
        """Return True if the context still matches the expected hash."""
        return _context_hash(context) == expected_hash

    def detect_mutation(
        self,
        before_hash: str,
        after_hash: str,
        context: Context,
    ) -> MutationReport:
        """
        Classify a context mutation by severity.

        ``context`` is the *current* (after) state, used for heuristic analysis.
        """
        changed = before_hash != after_hash

        if not changed:
            return MutationReport(
                before_hash=before_hash,
                after_hash=after_hash,
                changed=False,
                severity="MINOR",
                likely_cause="no mutation detected",
            )

        # Normalise context to text for pattern matching
        if isinstance(context, str):
            text = context.lower()
        else:
            text = json.dumps(context, default=str).lower()

        # CRITICAL: prompt-injection patterns
        if any(p in text for p in _CRITICAL_PATTERNS):
            return MutationReport(
                before_hash=before_hash,
                after_hash=after_hash,
                changed=True,
                severity="CRITICAL",
                likely_cause="likely prompt injection attempt detected in mutated context",
            )

        # MAJOR: very large context replacement
        if len(text) > 10_000:
            return MutationReport(
                before_hash=before_hash,
                after_hash=after_hash,
                changed=True,
                severity="MAJOR",
                likely_cause="large context replacement",
            )

        return MutationReport(
            before_hash=before_hash,
            after_hash=after_hash,
            changed=True,
            severity="MINOR",
            likely_cause="normal context update",
        )
