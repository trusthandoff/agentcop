"""
ToolTrustBoundary — declare and enforce trust boundaries between tools.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .models import BoundaryViolationError  # noqa: F401  re-exported

_log = logging.getLogger(__name__)


@dataclass
class BoundaryResult:
    """Result of a trust boundary check."""

    allowed: bool
    reason: str
    from_tool: str
    to_tool: str


class ToolTrustBoundary:
    """
    Declares and enforces trust boundaries between tools.

    Boundaries are stored as a dict for O(1) lookup. Fires a SentinelEvent
    (via an optionally-injected sentinel) whenever a boundary violation occurs.

    Thread-safe.
    """

    def __init__(self, sentinel: Any | None = None) -> None:
        # (from_tool, to_tool) → (allowed, reason)
        self._boundaries: dict[tuple[str, str], tuple[bool, str]] = {}
        self._sentinel = sentinel
        self._lock = threading.Lock()

    def declare_boundary(
        self,
        from_tool: str,
        to_tool: str,
        allowed: bool,
        reason: str,
    ) -> None:
        """Declare whether ``from_tool`` is allowed to call ``to_tool``."""
        with self._lock:
            self._boundaries[(from_tool, to_tool)] = (allowed, reason)

    def check(
        self,
        from_tool: str,
        to_tool: str,
        context_hash: str = "",
    ) -> BoundaryResult:
        """
        Check whether the boundary between ``from_tool`` and ``to_tool`` is traversable.

        If a boundary is denied and a sentinel was provided, a SentinelEvent is pushed.
        Returns a BoundaryResult; never raises.
        """
        with self._lock:
            entry = self._boundaries.get((from_tool, to_tool))

        if entry is None:
            return BoundaryResult(
                allowed=True,
                reason="no boundary declared",
                from_tool=from_tool,
                to_tool=to_tool,
            )

        allowed, reason = entry
        if not allowed:
            self._fire_violation(from_tool, to_tool, reason, context_hash)

        return BoundaryResult(
            allowed=allowed,
            reason=reason,
            from_tool=from_tool,
            to_tool=to_tool,
        )

    def _fire_violation(
        self,
        from_tool: str,
        to_tool: str,
        reason: str,
        context_hash: str,
    ) -> None:
        if self._sentinel is None:
            return
        try:
            # Import inside function to avoid circular imports at module level
            from agentcop.event import SentinelEvent

            event = SentinelEvent(
                event_id=str(uuid.uuid4()),
                event_type="boundary_violation",
                timestamp=datetime.now(UTC),
                severity="ERROR",
                producer_id=from_tool,
                body=f"Tool boundary violation: {from_tool} → {to_tool}. {reason}",
                attributes={
                    "from_tool": from_tool,
                    "to_tool": to_tool,
                    "reason": reason,
                    "context_hash": context_hash,
                    "trust.violation_type": "boundary_violation",
                },
                source_system="trust.boundaries",
            )
            self._sentinel.push(event)
        except Exception as exc:
            _log.debug("Failed to fire boundary violation SentinelEvent: %s", exc)
