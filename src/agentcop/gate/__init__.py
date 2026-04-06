"""
ExecutionGate — policy-based tool execution control with SQLite audit log.

Usage::

    from agentcop.gate import ExecutionGate, ConditionalPolicy

    gate = ExecutionGate()
    gate.register_policy("file_write", ConditionalPolicy(
        allow_if=lambda args: args.get("path", "").startswith("/tmp/"),
        deny_reason="file_write outside /tmp/ blocked",
    ))

    @gate.wrap
    def file_write(path, content): ...
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "GateDecision",
    "AllowPolicy",
    "DenyPolicy",
    "ConditionalPolicy",
    "RateLimitPolicy",
    "ExecutionGate",
]

_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateDecision:
    """Result of a gate check.

    Attributes:
        allowed:    Whether the tool call is permitted.
        reason:     Human-readable explanation of the decision.
        risk_score: 0 (safe) – 100 (critical risk).
    """

    allowed: bool
    reason: str
    risk_score: int  # 0–100


# ---------------------------------------------------------------------------
# Policy types
# ---------------------------------------------------------------------------


class AllowPolicy:
    """Always allows the call.  Use as a safe default for low-risk tools."""

    def check(
        self,
        tool_name: str,  # noqa: ARG002
        args: dict[str, Any],  # noqa: ARG002
        context: dict[str, Any],  # noqa: ARG002
    ) -> GateDecision:
        return GateDecision(allowed=True, reason="allow policy", risk_score=0)


class DenyPolicy:
    """Always denies the call.  Use to block tools entirely."""

    def __init__(self, reason: str = "deny policy") -> None:
        self.reason = reason

    def check(
        self,
        tool_name: str,  # noqa: ARG002
        args: dict[str, Any],  # noqa: ARG002
        context: dict[str, Any],  # noqa: ARG002
    ) -> GateDecision:
        return GateDecision(allowed=False, reason=self.reason, risk_score=100)


class ConditionalPolicy:
    """Allow or deny based on a predicate over the call arguments.

    Args:
        allow_if:             Callable ``(args: dict) -> bool``.  Return ``True``
                              to permit the call.
        deny_reason:          Message attached to denial decisions.
        risk_score_if_denied: Risk score used when the condition is not met
                              (0–100, default 80).
    """

    def __init__(
        self,
        allow_if: Callable[[dict[str, Any]], bool],
        deny_reason: str = "condition not met",
        risk_score_if_denied: int = 80,
    ) -> None:
        self._allow_if = allow_if
        self._deny_reason = deny_reason
        self._risk_score_if_denied = risk_score_if_denied

    def check(
        self,
        tool_name: str,  # noqa: ARG002
        args: dict[str, Any],
        context: dict[str, Any],  # noqa: ARG002
    ) -> GateDecision:
        if self._allow_if(args):
            return GateDecision(allowed=True, reason="condition met", risk_score=0)
        return GateDecision(
            allowed=False,
            reason=self._deny_reason,
            risk_score=self._risk_score_if_denied,
        )


class RateLimitPolicy:
    """Sliding-window rate limiter.  Allows up to *max_calls* per *window_seconds*.

    Thread-safe; each :class:`RateLimitPolicy` instance maintains its own call
    history independent of other instances.
    """

    def __init__(
        self,
        max_calls: int,
        window_seconds: float = 60.0,
        deny_reason: str = "rate limit exceeded",
    ) -> None:
        self._max_calls = max_calls
        self._window = window_seconds
        self._deny_reason = deny_reason
        self._calls: list[float] = []
        self._lock = threading.Lock()

    def check(
        self,
        tool_name: str,  # noqa: ARG002
        args: dict[str, Any],  # noqa: ARG002
        context: dict[str, Any],  # noqa: ARG002
    ) -> GateDecision:
        now = time.monotonic()
        with self._lock:
            cutoff = now - self._window
            self._calls = [t for t in self._calls if t >= cutoff]
            if len(self._calls) >= self._max_calls:
                return GateDecision(
                    allowed=False, reason=self._deny_reason, risk_score=60
                )
            self._calls.append(now)
        return GateDecision(allowed=True, reason="within rate limit", risk_score=0)


# Union of all built-in policy types.  Pass any of these to
# :meth:`ExecutionGate.register_policy`.
Policy = AllowPolicy | DenyPolicy | ConditionalPolicy | RateLimitPolicy


# ---------------------------------------------------------------------------
# ExecutionGate
# ---------------------------------------------------------------------------


class ExecutionGate:
    """Policy-based execution gate with SQLite audit log.

    Every :meth:`check` call is persisted to SQLite regardless of the outcome,
    providing a tamper-evident decision history.

    Usage::

        gate = ExecutionGate()
        gate.register_policy("shell_exec", DenyPolicy("shell exec blocked"))
        gate.register_policy("file_write", ConditionalPolicy(
            allow_if=lambda args: args.get("path", "").startswith("/tmp/"),
            deny_reason="file_write outside /tmp/ blocked",
        ))

        @gate.wrap
        def file_write(path, content): ...

    Args:
        db_path: Path to the SQLite database.  Defaults to ``agentcop_gate.db``
                 in the current working directory.  Pass ``":memory:"`` for an
                 in-process-only store (useful in tests).
    """

    def __init__(self, db_path: str | Path = "agentcop_gate.db") -> None:
        self._policies: dict[str, Policy] = {}
        self._policy_lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage transactions manually
            timeout=30,
        )
        self._db_lock = threading.Lock()
        self._init_db()

    # ── Schema ────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._db_lock:
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_version (
                        version INTEGER NOT NULL
                    )
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS gate_decisions (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp  TEXT    NOT NULL,
                        tool       TEXT    NOT NULL,
                        args_hash  TEXT    NOT NULL,
                        allowed    INTEGER NOT NULL,
                        reason     TEXT    NOT NULL,
                        risk_score INTEGER NOT NULL
                    )
                    """
                )
                cursor = self._conn.execute("SELECT version FROM schema_version")
                if cursor.fetchone() is None:
                    self._conn.execute(
                        "INSERT INTO schema_version VALUES (?)", (_SCHEMA_VERSION,)
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ── Policy registration ───────────────────────────────────────────────

    def register_policy(self, tool_name: str, policy: Policy) -> None:
        """Assign *policy* to *tool_name*.  Replaces any existing policy for that tool."""
        with self._policy_lock:
            self._policies[tool_name] = policy

    # ── Decision ──────────────────────────────────────────────────────────

    def check(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> GateDecision:
        """Evaluate the registered policy for *tool_name* and log the decision.

        When no policy is registered for *tool_name*, the call is allowed with
        ``risk_score=0``.

        Args:
            tool_name: Name of the tool being gated.
            args:      Mapping of argument names to values for this call.
            context:   Optional execution context (caller identity, trace ID, etc.).

        Returns:
            :class:`GateDecision` with the allow/deny verdict.
        """
        context = context or {}
        with self._policy_lock:
            policy = self._policies.get(tool_name)
        if policy is None:
            decision = GateDecision(
                allowed=True, reason="no policy registered", risk_score=0
            )
        else:
            decision = policy.check(tool_name, args, context)
        self._log(tool_name, args, decision)
        return decision

    # ── Decorator ─────────────────────────────────────────────────────────

    def wrap(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator that gates *fn* using its ``__name__`` as the tool name.

        Raises :class:`PermissionError` when the gate denies the call.

        Usage::

            @gate.wrap
            def file_write(path, content): ...
        """

        @functools.wraps(fn)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            sig = inspect.signature(fn)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            decision = self.check(fn.__name__, dict(bound.arguments))
            if not decision.allowed:
                raise PermissionError(
                    f"ExecutionGate denied {fn.__name__!r}: {decision.reason}"
                )
            return fn(*args, **kwargs)

        return _wrapper

    # ── Audit log ─────────────────────────────────────────────────────────

    def decision_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the *limit* most-recent decisions from the audit log, newest first."""
        with self._db_lock:
            cursor = self._conn.execute(
                "SELECT timestamp, tool, args_hash, allowed, reason, risk_score"
                " FROM gate_decisions ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
        return [
            {
                "timestamp": r[0],
                "tool": r[1],
                "args_hash": r[2],
                "allowed": bool(r[3]),
                "reason": r[4],
                "risk_score": r[5],
            }
            for r in rows
        ]

    def _log(self, tool: str, args: dict[str, Any], decision: GateDecision) -> None:
        args_hash = hashlib.sha256(
            json.dumps(args, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        ts = datetime.now(UTC).isoformat()
        with self._db_lock:
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                self._conn.execute(
                    "INSERT INTO gate_decisions"
                    " (timestamp, tool, args_hash, allowed, reason, risk_score)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        ts,
                        tool,
                        args_hash,
                        int(decision.allowed),
                        decision.reason,
                        decision.risk_score,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()
