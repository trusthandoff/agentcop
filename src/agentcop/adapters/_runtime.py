"""
Shared runtime security helpers for agentcop adapters.

Provides tool call interception (gate, permissions, approvals) and
sandbox wrapping that every adapter can import and use.  Not part of
the public API — import paths inside adapters only.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agentcop.event import SentinelEvent

if TYPE_CHECKING:
    pass


def _args_hash(args: dict[str, Any]) -> str:
    """SHA-256 hex digest (first 16 chars) of a sorted JSON representation of *args*."""
    payload = json.dumps(args, default=str, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def fire_security_event(
    adapter: Any,
    event_type: str,
    tool_name: str,
    args_hash: str,
    reason: str,
    severity: str = "CRITICAL",
) -> None:
    """Buffer a runtime-security SentinelEvent on *adapter*.

    Works for any adapter that has ``_buffer`` (list) and ``_lock`` (Lock).
    Adapters without a buffer silently drop the event.
    """
    source = getattr(adapter, "source_system", "unknown")
    trace_id = getattr(adapter, "_run_id", None) or getattr(adapter, "_thread_id", None)
    event = SentinelEvent(
        event_id=f"{source}-sec-{uuid.uuid4()}",
        event_type=event_type,
        timestamp=datetime.now(UTC),
        severity=severity,
        body=f"{event_type}: tool='{tool_name}' reason='{reason}'",
        source_system=source,
        trace_id=trace_id,
        attributes={
            "tool_name": tool_name,
            "args_hash": args_hash,
            "reason": reason,
        },
    )
    buf = getattr(adapter, "_buffer", None)
    lock = getattr(adapter, "_lock", None)
    if buf is not None and lock is not None:
        with lock:
            buf.append(event)


def check_tool_call(
    adapter: Any,
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    context: dict[str, Any] | None = None,
    agent_id: str = "default",
) -> None:
    """Check a tool call against gate / permissions / approvals.

    Fires SentinelEvents for every security decision and raises
    :class:`PermissionError` if the call should be blocked.

    Parameters
    ----------
    adapter:
        The adapter instance.  Must carry ``_gate``, ``_permissions``,
        ``_sandbox``, ``_approvals``, and ``_identity`` attributes
        (all default ``None`` when not provided).
    tool_name:
        Name of the tool being called.
    args:
        Arguments dict for the tool call.
    context:
        Extra key/value context forwarded to the gate policy.
    agent_id:
        Agent identifier for the ToolPermissionLayer lookup.
    """
    args = args or {}
    ctx: dict[str, Any] = dict(context or {})
    ah = _args_hash(args)

    # AgentIdentity trust_score → gate context
    identity = getattr(adapter, "_identity", None)
    if identity is not None:
        trust_score = getattr(identity, "trust_score", 100)
        ctx["trust_score"] = trust_score

    # 1. ToolPermissionLayer check ─────────────────────────────────────────
    permissions = getattr(adapter, "_permissions", None)
    if permissions is not None:
        result = permissions.verify(agent_id, tool_name, args)
        if not result.granted:
            fire_security_event(
                adapter, "permission_violation", tool_name, ah, result.reason, "CRITICAL"
            )
            raise PermissionError(
                f"Tool '{tool_name}' denied by permissions: {result.reason}"
            )

    # 2. ExecutionGate check ───────────────────────────────────────────────
    gate = getattr(adapter, "_gate", None)
    decision = None
    if gate is not None:
        decision = gate.check(tool_name, args, ctx)
        if not decision.allowed:
            fire_security_event(
                adapter, "gate_denied", tool_name, ah, decision.reason, "CRITICAL"
            )
            raise PermissionError(
                f"Tool '{tool_name}' denied by gate: {decision.reason}"
            )

    # 3. ApprovalBoundary check ────────────────────────────────────────────
    approvals = getattr(adapter, "_approvals", None)
    if approvals is not None:
        risk_score = decision.risk_score if decision is not None else 0
        threshold = getattr(approvals, "requires_approval_above", 70)
        if risk_score > threshold:
            req = approvals.submit(tool_name, args, risk_score)
            fire_security_event(
                adapter,
                "approval_requested",
                tool_name,
                ah,
                f"risk_score={risk_score}",
                "WARN",
            )
            resolved = approvals.wait_for_decision(req.request_id)
            if resolved.denied:
                fire_security_event(
                    adapter, "gate_denied", tool_name, ah, "approval denied", "CRITICAL"
                )
                raise PermissionError(
                    f"Tool '{tool_name}' denied by approval boundary: {resolved.reason}"
                )
