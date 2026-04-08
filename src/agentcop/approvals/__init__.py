"""
Approvals — human-in-the-loop (or programmatic) approval gate.

Block high-risk tool calls until they are explicitly approved or denied.
Ship with auto-approve and auto-deny policies for CI / test environments, and
a callback-based policy for interactive workflows.

Usage::

    from agentcop.approvals import ApprovalGate, ApprovalRequest, AutoApprovePolicy

    gate = ApprovalGate(policy=AutoApprovePolicy(risk_threshold=50))

    req = gate.request("delete_file", {"path": "/data/important.csv"}, risk_score=90)
    if req.approved:
        delete_file(req.args["path"])

Human-in-the-loop example::

    def human_approve(req: ApprovalRequest) -> bool:
        return input(f"Approve {req.tool}({req.args})? [y/N] ").lower() == "y"

    gate = ApprovalGate(policy=CallbackApprovalPolicy(human_approve))
    req = gate.request("send_email", {"to": "ceo@company.com"}, risk_score=70)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "ApprovalRequest",
    "ApprovalStatus",
    "AutoApprovePolicy",
    "AutoDenyPolicy",
    "CallbackApprovalPolicy",
    "ApprovalGate",
    "ApprovalDenied",
    "ApprovalBoundary",
]

_BOUNDARY_SCHEMA_VERSION = 1

# Literal type alias for request status values.
ApprovalStatus = Literal["pending", "approved", "denied"]


# ---------------------------------------------------------------------------
# ApprovalRequest
# ---------------------------------------------------------------------------


@dataclass
class ApprovalRequest:
    """An approval request produced by :meth:`ApprovalGate.request`.

    Attributes:
        request_id:  UUID4 string identifying this request.
        tool:        Name of the tool whose execution requires approval.
        args:        Argument mapping passed to the tool.
        risk_score:  Caller-supplied risk estimate (0–100).
        status:      ``"pending"`` → ``"approved"`` or ``"denied"``.
        reason:      Explanation for the current status (set by the policy or
                     by a manual :meth:`ApprovalGate.approve` / :meth:`~.deny` call).
        created_at:  UTC timestamp when the request was created.
        resolved_at: UTC timestamp when the request was approved or denied.
                     ``None`` while still pending.
    """

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    risk_score: int = 0
    status: ApprovalStatus = "pending"
    reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None

    @property
    def approved(self) -> bool:
        """``True`` when the request has been approved."""
        return self.status == "approved"

    @property
    def denied(self) -> bool:
        """``True`` when the request has been denied."""
        return self.status == "denied"

    @property
    def pending(self) -> bool:
        """``True`` when the request is still awaiting a decision."""
        return self.status == "pending"


# ---------------------------------------------------------------------------
# Approval policies
# ---------------------------------------------------------------------------


class AutoApprovePolicy:
    """Auto-approve requests whose *risk_score* is at or below *risk_threshold*.

    Requests above the threshold are auto-denied.

    Args:
        risk_threshold: 0–100.  Requests with ``risk_score <= risk_threshold``
                        are approved; all others are denied.
    """

    def __init__(self, risk_threshold: int = 50) -> None:
        self._threshold = risk_threshold

    def evaluate(self, request: ApprovalRequest) -> tuple[ApprovalStatus, str]:
        if request.risk_score <= self._threshold:
            return (
                "approved",
                f"risk score {request.risk_score} within threshold {self._threshold}",
            )
        return "denied", (
            f"risk score {request.risk_score} exceeds auto-approve threshold {self._threshold}"
        )


class AutoDenyPolicy:
    """Auto-deny requests whose *risk_score* is at or above *risk_threshold*.

    Requests below the threshold are auto-approved.

    Args:
        risk_threshold: 0–100.  Requests with ``risk_score >= risk_threshold``
                        are denied; all others are approved.
    """

    def __init__(self, risk_threshold: int = 80) -> None:
        self._threshold = risk_threshold

    def evaluate(self, request: ApprovalRequest) -> tuple[ApprovalStatus, str]:
        if request.risk_score >= self._threshold:
            return "denied", (
                f"risk score {request.risk_score} at or above deny threshold {self._threshold}"
            )
        return (
            "approved",
            f"risk score {request.risk_score} below deny threshold {self._threshold}",
        )


class CallbackApprovalPolicy:
    """Delegate approval decisions to a user-supplied callback.

    The callback receives an :class:`ApprovalRequest` and must return ``True``
    to approve or ``False`` to deny.  This is the entry point for
    human-in-the-loop approval workflows.

    Args:
        callback:      ``(ApprovalRequest) -> bool``
        deny_reason:   Reason string attached to denied requests.
        approve_reason: Reason string attached to approved requests.
    """

    def __init__(
        self,
        callback: Callable[[ApprovalRequest], bool],
        deny_reason: str = "denied by callback",
        approve_reason: str = "approved by callback",
    ) -> None:
        self._callback = callback
        self._deny_reason = deny_reason
        self._approve_reason = approve_reason

    def evaluate(self, request: ApprovalRequest) -> tuple[ApprovalStatus, str]:
        if self._callback(request):
            return "approved", self._approve_reason
        return "denied", self._deny_reason


# Union of built-in policy types.
ApprovalPolicy = AutoApprovePolicy | AutoDenyPolicy | CallbackApprovalPolicy


# ---------------------------------------------------------------------------
# ApprovalGate
# ---------------------------------------------------------------------------


class ApprovalDenied(Exception):
    """Raised by :meth:`ApprovalGate.enforce` when a request is denied."""


class ApprovalGate:
    """Policy-driven approval gate with an in-memory request store.

    :meth:`request` creates an :class:`ApprovalRequest`, evaluates the policy
    synchronously, and returns the resolved request.  For async / deferred
    workflows, create requests with a ``CallbackApprovalPolicy`` that signals
    an event, then call :meth:`approve` or :meth:`deny` from another thread.

    Thread-safe.

    Args:
        policy: The :class:`ApprovalPolicy` to evaluate on each :meth:`request` call.
                Defaults to :class:`AutoApprovePolicy` with ``risk_threshold=0``
                (approve everything — safe for tests where you don't want blocks).
    """

    def __init__(self, policy: ApprovalPolicy | None = None) -> None:
        self._policy: ApprovalPolicy = policy or AutoApprovePolicy(risk_threshold=100)
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()

    # ── Request lifecycle ─────────────────────────────────────────────────

    def request(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        risk_score: int = 0,
    ) -> ApprovalRequest:
        """Create, evaluate, and store an :class:`ApprovalRequest`.

        The registered policy is evaluated synchronously.  The returned request
        will have ``status`` set to ``"approved"`` or ``"denied"`` (never
        ``"pending"`` for synchronous policies).

        Args:
            tool:       Name of the tool requiring approval.
            args:       Arguments to be passed to the tool.
            risk_score: Caller's risk estimate (0–100).

        Returns:
            The resolved :class:`ApprovalRequest`.
        """
        req = ApprovalRequest(tool=tool, args=args or {}, risk_score=risk_score)
        status, reason = self._policy.evaluate(req)
        req.status = status
        req.reason = reason
        req.resolved_at = datetime.now(UTC)
        with self._lock:
            self._requests[req.request_id] = req
        return req

    def approve(self, request_id: str, reason: str = "manually approved") -> ApprovalRequest:
        """Manually approve a pending request.

        Raises:
            KeyError:   If *request_id* is unknown.
            ValueError: If the request is not in ``"pending"`` status.
        """
        with self._lock:
            req = self._requests[request_id]
            if req.status != "pending":
                raise ValueError(
                    f"Cannot approve request {request_id!r}: status is {req.status!r}"
                )
            req.status = "approved"
            req.reason = reason
            req.resolved_at = datetime.now(UTC)
        return req

    def deny(self, request_id: str, reason: str = "manually denied") -> ApprovalRequest:
        """Manually deny a pending request.

        Raises:
            KeyError:   If *request_id* is unknown.
            ValueError: If the request is not in ``"pending"`` status.
        """
        with self._lock:
            req = self._requests[request_id]
            if req.status != "pending":
                raise ValueError(f"Cannot deny request {request_id!r}: status is {req.status!r}")
            req.status = "denied"
            req.reason = reason
            req.resolved_at = datetime.now(UTC)
        return req

    def enforce(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        risk_score: int = 0,
    ) -> ApprovalRequest:
        """Like :meth:`request`, but raises :class:`ApprovalDenied` when denied."""
        req = self.request(tool, args, risk_score)
        if not req.approved:
            raise ApprovalDenied(f"Tool {tool!r} was not approved: {req.reason}")
        return req

    # ── Inspection ────────────────────────────────────────────────────────

    def pending(self) -> list[ApprovalRequest]:
        """Return all requests currently in ``"pending"`` status."""
        with self._lock:
            return [r for r in self._requests.values() if r.pending]

    def get(self, request_id: str) -> ApprovalRequest | None:
        """Return the request with *request_id*, or ``None`` if not found."""
        with self._lock:
            return self._requests.get(request_id)

    def all_requests(self) -> list[ApprovalRequest]:
        """Return all requests in insertion order."""
        with self._lock:
            return list(self._requests.values())


# ===========================================================================
# ApprovalBoundary — threshold gate with channel dispatch and audit trail
# ===========================================================================


class ApprovalBoundary:
    """Risk-threshold approval gate with channel dispatch, timeout, and SQLite audit trail.

    Requests whose ``risk_score`` exceeds ``requires_approval_above`` are held
    ``"pending"`` while notifications are dispatched to the configured channels.
    If no decision arrives within ``timeout`` seconds the request is
    auto-denied.  Requests at or below the threshold are auto-approved
    immediately.

    Usage::

        from agentcop.approvals import ApprovalBoundary

        boundary = ApprovalBoundary(
            requires_approval_above=70,
            channels=["webhook"],
            timeout=300,
            webhook_url="https://my-approval-service.example.com/approve",
        )

        req = boundary.submit("delete_file", {"path": "/data/x"}, risk_score=80)
        # req.status == "pending"

        # Later — human calls:
        boundary.approve(req.request_id)
        # Or after 300 s with no response:
        # req.status == "denied"  (auto-denied by timeout)

    Channel entries can be either a string type name (``"cli"``, ``"webhook"``,
    ``"slack"``, ``"email"``) or a dict with ``"type"`` and optional ``"url"``
    keys::

        channels=[{"type": "webhook", "url": "https://..."}]

    Args:
        requires_approval_above: Risk score threshold (0–100).  Requests with
            ``risk_score > requires_approval_above`` require human approval.
        channels:                Notification channels fired when a request
                                 needs human review.
        timeout:                 Seconds before a pending request is auto-denied.
        db_path:                 SQLite path for the persistent audit trail.
                                 ``":memory:"`` = in-process only (default).
        webhook_url:             Fallback URL for ``"webhook"`` / ``"slack"``
                                 channel entries that omit their own URL.
    """

    def __init__(
        self,
        *,
        requires_approval_above: int = 70,
        channels: list[str | dict[str, Any]] | None = None,
        timeout: int = 300,
        db_path: str | Path = ":memory:",
        webhook_url: str | None = None,
    ) -> None:
        self.requires_approval_above = requires_approval_above
        self.channels: list[str | dict[str, Any]] = list(channels or [])
        self.timeout = timeout
        self._webhook_url = webhook_url

        self._requests: dict[str, ApprovalRequest] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

        self._conn = sqlite3.connect(
            str(db_path), check_same_thread=False, isolation_level=None, timeout=30
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
                    CREATE TABLE IF NOT EXISTS audit_trail (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp  TEXT NOT NULL,
                        request_id TEXT NOT NULL,
                        event      TEXT NOT NULL,
                        tool       TEXT NOT NULL,
                        risk_score INTEGER NOT NULL,
                        actor      TEXT,
                        reason     TEXT,
                        detail     TEXT
                    )
                    """
                )
                cursor = self._conn.execute("SELECT version FROM schema_version")
                if cursor.fetchone() is None:
                    self._conn.execute(
                        "INSERT INTO schema_version VALUES (?)", (_BOUNDARY_SCHEMA_VERSION,)
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ── Request lifecycle ─────────────────────────────────────────────────

    def submit(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        risk_score: int = 0,
    ) -> ApprovalRequest:
        """Submit a tool call for approval.

        Auto-approves immediately when ``risk_score <= requires_approval_above``.
        Otherwise dispatches to all configured channels, starts the timeout
        timer, and returns a ``"pending"`` request.

        Args:
            tool:       Name of the tool whose execution requires approval.
            args:       Arguments to be passed to the tool.
            risk_score: Caller-supplied risk estimate (0–100).

        Returns:
            The :class:`ApprovalRequest`.  ``request.approved`` is ``True``
            for auto-approved calls; ``request.pending`` is ``True`` when
            human review is required.
        """
        req = ApprovalRequest(tool=tool, args=args or {}, risk_score=risk_score)
        self._audit("submitted", req)

        if risk_score <= self.requires_approval_above:
            req.status = "approved"
            req.reason = (
                f"risk score {risk_score} within auto-approve threshold "
                f"{self.requires_approval_above}"
            )
            req.resolved_at = datetime.now(UTC)
            with self._lock:
                self._requests[req.request_id] = req
            self._audit("approved", req, actor="auto", reason=req.reason)
        else:
            event = threading.Event()
            with self._lock:
                self._requests[req.request_id] = req
                self._events[req.request_id] = event
            self._dispatch(req)
            timer = threading.Timer(self.timeout, self._on_timeout, args=(req.request_id,))
            timer.daemon = True
            timer.start()
            with self._lock:
                self._timers[req.request_id] = timer

        return req

    def approve(
        self,
        request_id: str,
        *,
        actor: str = "human",
        reason: str = "approved",
    ) -> ApprovalRequest:
        """Approve a pending request.

        Cancels the auto-deny timer and unblocks any thread waiting in
        :meth:`wait_for_decision`.

        Raises:
            KeyError:   Unknown *request_id*.
            ValueError: Request is not ``"pending"``.
        """
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                raise KeyError(request_id)
            if req.status != "pending":
                raise ValueError(f"Cannot approve {request_id!r}: status is {req.status!r}")
            req.status = "approved"
            req.reason = reason
            req.resolved_at = datetime.now(UTC)
            timer = self._timers.pop(request_id, None)
            event = self._events.get(request_id)
        if timer:
            timer.cancel()
        self._audit("approved", req, actor=actor, reason=reason)
        if event:
            event.set()
        return req

    def deny(
        self,
        request_id: str,
        *,
        actor: str = "human",
        reason: str = "denied",
    ) -> ApprovalRequest:
        """Deny a pending request.

        Cancels the auto-deny timer and unblocks any thread waiting in
        :meth:`wait_for_decision`.

        Raises:
            KeyError:   Unknown *request_id*.
            ValueError: Request is not ``"pending"``.
        """
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                raise KeyError(request_id)
            if req.status != "pending":
                raise ValueError(f"Cannot deny {request_id!r}: status is {req.status!r}")
            req.status = "denied"
            req.reason = reason
            req.resolved_at = datetime.now(UTC)
            timer = self._timers.pop(request_id, None)
            event = self._events.get(request_id)
        if timer:
            timer.cancel()
        self._audit("denied", req, actor=actor, reason=reason)
        if event:
            event.set()
        return req

    def _on_timeout(self, request_id: str) -> None:
        """Auto-deny a pending request when the timeout expires."""
        with self._lock:
            req = self._requests.get(request_id)
            if req is None or req.status != "pending":
                return
            req.status = "denied"
            req.reason = f"auto-denied: no decision within {self.timeout}s"
            req.resolved_at = datetime.now(UTC)
            self._timers.pop(request_id, None)
            event = self._events.get(request_id)
        self._audit("timeout", req, actor="system", reason=req.reason)
        if event:
            event.set()

    def wait_for_decision(
        self,
        request_id: str,
        timeout: float | None = None,
    ) -> ApprovalRequest:
        """Block until *request_id* is resolved or *timeout* seconds elapse.

        Args:
            request_id: Request to wait on.
            timeout:    Maximum seconds to block (``None`` = wait indefinitely).

        Returns:
            The :class:`ApprovalRequest` after resolution.  May still be
            ``"pending"`` if *timeout* expired before a decision arrived.

        Raises:
            KeyError: Unknown *request_id*.
        """
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                raise KeyError(request_id)
            if req.status != "pending":
                return req
            event = self._events.get(request_id)
        if event:
            event.wait(timeout=timeout)
        with self._lock:
            return self._requests[request_id]

    # ── Inspection ────────────────────────────────────────────────────────

    def pending_requests(self) -> list[ApprovalRequest]:
        """Return all requests currently in ``"pending"`` status."""
        with self._lock:
            return [r for r in self._requests.values() if r.pending]

    def get(self, request_id: str) -> ApprovalRequest | None:
        """Return the request, or ``None`` if not found."""
        with self._lock:
            return self._requests.get(request_id)

    def all_requests(self) -> list[ApprovalRequest]:
        """Return all submitted requests in submission order."""
        with self._lock:
            return list(self._requests.values())

    # ── Audit trail ───────────────────────────────────────────────────────

    def audit_trail(
        self,
        request_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return audit log entries, newest first.

        Args:
            request_id: Filter to a single request (``None`` = all requests).
            limit:      Maximum number of rows to return.
        """
        if request_id is not None:
            sql = (
                "SELECT timestamp, request_id, event, tool, risk_score, actor, reason, detail"
                " FROM audit_trail WHERE request_id = ? ORDER BY id DESC LIMIT ?"
            )
            params: tuple[Any, ...] = (request_id, limit)
        else:
            sql = (
                "SELECT timestamp, request_id, event, tool, risk_score, actor, reason, detail"
                " FROM audit_trail ORDER BY id DESC LIMIT ?"
            )
            params = (limit,)
        with self._db_lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "timestamp": r[0],
                "request_id": r[1],
                "event": r[2],
                "tool": r[3],
                "risk_score": r[4],
                "actor": r[5],
                "reason": r[6],
                "detail": json.loads(r[7]) if r[7] else {},
            }
            for r in rows
        ]

    def _audit(
        self,
        event: str,
        req: ApprovalRequest,
        *,
        actor: str | None = None,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        ts = datetime.now(UTC).isoformat()
        detail_json = json.dumps(detail) if detail else None
        with self._db_lock:
            self._conn.execute("BEGIN EXCLUSIVE")
            try:
                self._conn.execute(
                    "INSERT INTO audit_trail"
                    " (timestamp, request_id, event, tool, risk_score, actor, reason, detail)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ts,
                        req.request_id,
                        event,
                        req.tool,
                        req.risk_score,
                        actor,
                        reason,
                        detail_json,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ── Channel dispatch ──────────────────────────────────────────────────

    def _dispatch(self, req: ApprovalRequest) -> None:
        for channel in self.channels:
            try:
                self._send_to_channel(channel, req)
                self._audit("dispatch_sent", req, detail={"channel": str(channel)})
            except Exception as exc:
                self._audit(
                    "dispatch_failed",
                    req,
                    detail={"channel": str(channel), "error": str(exc)},
                )

    def _send_to_channel(self, channel: str | dict[str, Any], req: ApprovalRequest) -> None:
        import sys

        ctype = channel if isinstance(channel, str) else channel.get("type", "")
        cconfig = channel if isinstance(channel, dict) else {}

        if ctype == "cli":
            print(
                f"[APPROVAL REQUIRED] tool={req.tool!r} "
                f"risk_score={req.risk_score} request_id={req.request_id}",
                file=sys.stderr,
            )

        elif ctype in ("webhook", "slack"):
            url = cconfig.get("url") or self._webhook_url
            if not url:
                raise ValueError(
                    f"Channel {ctype!r} requires a URL — provide it via a dict "
                    "channel config or the boundary-level webhook_url parameter"
                )
            self._post_json(url, self._channel_payload(req, str(ctype)))

        elif ctype == "email":
            # Placeholder: real email delivery requires SMTP configuration.
            print(
                f"[EMAIL — not configured] Approval needed: {req.tool!r} "
                f"risk_score={req.risk_score} id={req.request_id}",
                file=sys.stderr,
            )

    def _channel_payload(self, req: ApprovalRequest, channel_type: str) -> dict[str, Any]:
        if channel_type == "slack":
            return {
                "text": (
                    f":rotating_light: *Approval required* — `{req.tool}` "
                    f"risk_score={req.risk_score}"
                ),
                "attachments": [{"text": f"Request ID: `{req.request_id}`"}],
            }
        return {
            "request_id": req.request_id,
            "tool": req.tool,
            "args": req.args,
            "risk_score": req.risk_score,
            "status": req.status,
        }

    def _post_json(self, url: str, data: dict[str, Any]) -> None:
        import urllib.request as _ur

        body = json.dumps(data).encode()
        http_req = _ur.Request(url, data=body, method="POST")
        http_req.add_header("Content-Type", "application/json")
        _ur.urlopen(http_req, timeout=10)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Cancel all pending timers and close the database connection."""
        with self._lock:
            for timer in list(self._timers.values()):
                timer.cancel()
            self._timers.clear()
        self._conn.close()
