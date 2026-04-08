"""
AgentCop — unified runtime enforcement orchestrator.

Chains ExecutionGate, ToolPermissionLayer, AgentSandbox, and ApprovalBoundary
into a single protection wrapper around any agent object.

Usage::

    from agentcop import AgentCop
    from agentcop.gate import ExecutionGate
    from agentcop.permissions import ToolPermissionLayer
    from agentcop.sandbox import AgentSandbox
    from agentcop.approvals import ApprovalBoundary

    cop = AgentCop(
        gate=ExecutionGate(),
        permissions=ToolPermissionLayer(),
        sandbox=AgentSandbox(allowed_paths=["/tmp/*"]),
        approvals=ApprovalBoundary(requires_approval_above=70),
    )
    protected = cop.protect(my_agent)
    result = protected.run(task)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcop.approvals import ApprovalBoundary
    from agentcop.gate import ExecutionGate
    from agentcop.identity import AgentIdentity
    from agentcop.permissions import ToolPermissionLayer
    from agentcop.sandbox import AgentSandbox
    from agentcop.sentinel import Sentinel

__all__ = ["AgentCop"]

# Trust score below this threshold → all executions blocked.
_CRITICAL_TRUST_THRESHOLD = 30.0


class AgentCop:
    """Unified runtime enforcement orchestrator.

    Chains up to four enforcement layers around any agent object:

    1. **Trust guard** — denies execution when the attached identity's
       ``trust_score`` drops below :data:`_CRITICAL_TRUST_THRESHOLD` (30).
    2. **ExecutionGate** — policy-based allow/deny with SQLite audit log.
    3. **ToolPermissionLayer** — capability-based access control per agent.
    4. **ApprovalBoundary** — risk-threshold human-in-the-loop gate with
       timeout-based auto-deny.
    5. **AgentSandbox** — syscall interception wrapping the execution block.

    A :class:`~agentcop.Sentinel` can be attached to collect
    ``SentinelEvent`` s for every gate denial, permission violation, sandbox
    escape, and approval decision.

    All four layers are optional — configure only what you need.

    Args:
        gate:        :class:`~agentcop.gate.ExecutionGate` for policy-based
                     execution control.
        permissions: :class:`~agentcop.permissions.ToolPermissionLayer` for
                     capability-scoped access control.
        sandbox:     :class:`~agentcop.sandbox.AgentSandbox` for syscall
                     interception and timeout enforcement.
        approvals:   :class:`~agentcop.approvals.ApprovalBoundary` for
                     risk-threshold human approval.
        sentinel:    :class:`~agentcop.Sentinel` for event collection.
                     Attach to capture all enforcement events as forensic
                     :class:`~agentcop.SentinelEvent` s.
        agent_id:    Identifier used for :class:`~agentcop.permissions.ToolPermissionLayer`
                     lookups.
        identity:    :class:`~agentcop.AgentIdentity` whose ``trust_score``
                     influences the risk score passed to the approval boundary
                     and gates.
    """

    def __init__(
        self,
        *,
        gate: ExecutionGate | None = None,
        permissions: ToolPermissionLayer | None = None,
        sandbox: AgentSandbox | None = None,
        approvals: ApprovalBoundary | None = None,
        sentinel: Sentinel | None = None,
        agent_id: str | None = None,
        identity: AgentIdentity | None = None,
    ) -> None:
        self.gate = gate
        self.permissions = permissions
        self.sandbox = sandbox
        self.approvals = approvals
        self.sentinel = sentinel
        self.agent_id = agent_id
        self.identity = identity

    # ── Pipeline state ────────────────────────────────────────────────────

    @property
    def all_layers_active(self) -> bool:
        """``True`` when all four enforcement layers are configured."""
        return (
            self.gate is not None
            and self.permissions is not None
            and self.sandbox is not None
            and self.approvals is not None
        )

    @property
    def active_layer_count(self) -> int:
        """Number of enforcement layers currently configured (0–4)."""
        return sum(
            [
                self.gate is not None,
                self.permissions is not None,
                self.sandbox is not None,
                self.approvals is not None,
            ]
        )

    # ── Agent wrapping ────────────────────────────────────────────────────

    def protect(self, agent: Any) -> _ProtectedAgent:
        """Wrap *agent* so that every ``run()`` call goes through the enforcement pipeline.

        The returned :class:`_ProtectedAgent` proxies all attribute access to
        the original agent except for ``run()``, which is intercepted.

        Args:
            agent: Any object with a ``run()`` method, or any callable.

        Returns:
            :class:`_ProtectedAgent` wrapping *agent*.
        """
        return _ProtectedAgent(agent, self)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _fire(
        self,
        event_type: str,
        severity: str,
        body: str,
        **attributes: Any,
    ) -> None:
        """Push a :class:`~agentcop.SentinelEvent` to the attached Sentinel."""
        if self.sentinel is None:
            return
        from agentcop.event import SentinelEvent

        event = SentinelEvent(
            event_id=f"agentcop-{uuid.uuid4()}",
            event_type=event_type,
            timestamp=datetime.now(UTC),
            severity=severity,
            body=body,
            source_system="agentcop.cop",
            attributes={"agent_id": self.agent_id, **attributes},
        )
        self.sentinel.push(event)

    def _risk_score(self) -> int:
        """Risk score (0–100) inversely proportional to trust_score.

        ``trust_score=100`` → ``risk_score=0``  (fully trusted)
        ``trust_score=50``  → ``risk_score=50``  (neutral)
        ``trust_score=0``   → ``risk_score=100`` (untrusted)
        """
        if self.identity is not None:
            return int(max(0, min(100, 100 - self.identity.trust_score)))
        return 50


# ---------------------------------------------------------------------------
# Protected agent wrapper
# ---------------------------------------------------------------------------


class _ProtectedAgent:
    """Thin wrapper returned by :meth:`AgentCop.protect`.

    Routes ``run()`` calls through the five-stage enforcement pipeline.
    All other attribute accesses are transparently proxied to the original
    agent.

    Not intended for direct instantiation.
    """

    def __init__(self, agent: Any, cop: AgentCop) -> None:
        # Use object.__setattr__ to avoid triggering our __getattr__ override.
        object.__setattr__(self, "_agent", agent)
        object.__setattr__(self, "_cop", cop)

    # ── Enforcement pipeline ──────────────────────────────────────────────

    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the agent through the full enforcement pipeline.

        Stage order:

        1. **Trust guard** — raises :class:`PermissionError` if trust < 30.
        2. **ExecutionGate** — checks registered policy for ``"run"``.
        3. **ToolPermissionLayer** — verifies agent capabilities.
        4. **ApprovalBoundary** — submits approval request; denies if not approved.
        5. **AgentSandbox** — wraps ``agent.run()`` with syscall interception.

        Raises:
            PermissionError: When any enforcement layer blocks execution.
            :class:`~agentcop.sandbox.SandboxViolation`: When the sandbox
                intercepts a policy-violating syscall (re-raised after firing
                a ``sandbox_violation`` SentinelEvent).
        """
        cop: AgentCop = object.__getattribute__(self, "_cop")
        agent: Any = object.__getattribute__(self, "_agent")

        # Build a stable, serialisable view of the call for policy checks.
        call_args = {
            "args": [str(a) for a in args],
            "kwargs": {k: str(v) for k, v in kwargs.items()},
        }
        risk_score = cop._risk_score()

        # ── Stage 1: Trust guard ──────────────────────────────────────────
        if cop.identity is not None:
            trust = cop.identity.trust_score
            if trust < _CRITICAL_TRUST_THRESHOLD:
                cop._fire(
                    "gate_denied",
                    "CRITICAL",
                    f"Execution blocked: trust score {trust:.1f} below critical threshold "
                    f"{_CRITICAL_TRUST_THRESHOLD}",
                    trust_score=trust,
                    reason="trust_too_low",
                )
                raise PermissionError(
                    f"AgentCop blocked execution: trust score {trust:.1f} is below "
                    f"the minimum threshold of {_CRITICAL_TRUST_THRESHOLD}"
                )

        # ── Stage 2: ExecutionGate ────────────────────────────────────────
        if cop.gate is not None:
            decision = cop.gate.check(
                "run",
                call_args,
                context={"risk_score": risk_score, "agent_id": cop.agent_id},
            )
            if not decision.allowed:
                cop._fire(
                    "gate_denied",
                    "ERROR",
                    f"Gate denied 'run': {decision.reason}",
                    reason=decision.reason,
                    risk_score=decision.risk_score,
                )
                raise PermissionError(f"AgentCop gate denied execution: {decision.reason}")

        # ── Stage 3: ToolPermissionLayer ──────────────────────────────────
        if cop.permissions is not None and cop.agent_id is not None:
            result = cop.permissions.verify(cop.agent_id, "run", call_args)
            if not result.granted:
                cop._fire(
                    "permission_violation",
                    "ERROR",
                    f"Permission denied for agent {cop.agent_id!r}: {result.reason}",
                    reason=result.reason,
                )
                raise PermissionError(f"AgentCop permission denied: {result.reason}")

        # ── Stage 4: ApprovalBoundary ─────────────────────────────────────
        if cop.approvals is not None:
            req = cop.approvals.submit("run", call_args, risk_score=risk_score)
            cop._fire(
                "approval_requested",
                "INFO",
                f"Approval {'granted' if req.approved else req.status} for 'run'",
                request_id=req.request_id,
                status=req.status,
                risk_score=risk_score,
            )
            if not req.approved:
                cop._fire(
                    "approval_denied",
                    "WARN",
                    f"Approval denied for 'run': {req.reason}",
                    request_id=req.request_id,
                    reason=req.reason,
                )
                raise PermissionError(f"AgentCop approval denied: {req.reason}")

        # ── Stage 5: Sandbox + execution ──────────────────────────────────
        from agentcop.sandbox import SandboxViolation

        try:
            if cop.sandbox is not None:
                with cop.sandbox:
                    return agent.run(*args, **kwargs)
            else:
                return agent.run(*args, **kwargs)
        except SandboxViolation as exc:
            cop._fire(
                "sandbox_violation",
                "ERROR",
                f"Sandbox blocked agent execution: {exc}",
                violation_type=exc.violation_type,
                detail=str(exc.detail),
            )
            raise

    # ── Transparent proxy ─────────────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        agent = object.__getattribute__(self, "_agent")
        return getattr(agent, name)

    def __repr__(self) -> str:
        agent = object.__getattribute__(self, "_agent")
        cop = object.__getattribute__(self, "_cop")
        return f"_ProtectedAgent(agent={agent!r}, layers={cop.active_layer_count}/4)"
