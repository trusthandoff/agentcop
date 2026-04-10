"""
AgentHierarchy — define and enforce agent authority and delegation rules.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .models import DelegationViolationError  # noqa: F401  re-exported

_log = logging.getLogger(__name__)


@dataclass
class HierarchyDefinition:
    """Definition of a supervisor–worker relationship."""

    supervisor: str
    workers: list[str]
    can_delegate: bool
    max_depth: int
    final_decision_authority: str


class AgentHierarchy:
    """
    Defines and enforces agent authority and delegation rules.

    Features:
    - Supervisor / worker relationships with depth limits
    - Veto rights (an agent can cancel another's actions)
    - Quorum voting (N/M agent agreement required for a chain)
    - SentinelEvent firing on delegation violations

    Thread-safe.
    """

    def __init__(self, sentinel: Any | None = None) -> None:
        self._defs: dict[str, HierarchyDefinition] = {}         # supervisor → def
        self._worker_to_sup: dict[str, str] = {}                # worker → supervisor
        self._veto_rights: dict[str, set[str]] = {}             # vetoing_agent → targets
        self._quorum: dict[str, int] = {}                       # chain_id → required votes
        self._delegation_depth: dict[str, int] = {}             # chain_id → current depth
        self._sentinel = sentinel
        self._lock = threading.Lock()

    def define(
        self,
        supervisor: str,
        workers: list[str],
        can_delegate: bool,
        max_depth: int,
        final_decision_authority: str,
    ) -> None:
        """
        Define a supervisor–worker hierarchy.

        If a worker already belongs to another supervisor, the latest call wins.
        """
        with self._lock:
            self._defs[supervisor] = HierarchyDefinition(
                supervisor=supervisor,
                workers=list(workers),
                can_delegate=can_delegate,
                max_depth=max_depth,
                final_decision_authority=final_decision_authority,
            )
            for w in workers:
                self._worker_to_sup[w] = supervisor

    def can_call(self, caller_id: str, callee_id: str) -> bool:
        """
        Return True if ``caller_id`` is authorised to call ``callee_id``.

        Allowed relationships:
        - Supervisor → any of its workers
        - Worker → its supervisor (escalation)
        - Peer workers sharing the same supervisor
        - Either agent outside any defined hierarchy

        Denied (and SentinelEvent fired):
        - Two agents both *in* the hierarchy but with no defined relationship
        """
        with self._lock:
            # Supervisor calling a worker
            defn = self._defs.get(caller_id)
            if defn and callee_id in defn.workers:
                return True

            # Worker escalating to its supervisor
            sup = self._worker_to_sup.get(caller_id)
            if sup and sup == callee_id:
                return True

            # Peers: same supervisor
            sup_caller = self._worker_to_sup.get(caller_id)
            sup_callee = self._worker_to_sup.get(callee_id)
            if sup_caller and sup_caller == sup_callee:
                return True

            # Determine if either agent is part of a hierarchy
            caller_in = (caller_id in self._defs) or (caller_id in self._worker_to_sup)
            callee_in = (callee_id in self._defs) or (callee_id in self._worker_to_sup)

        if caller_in and callee_in:
            # Both known to the hierarchy but no relationship → deny
            self._fire_delegation_violation(
                caller_id, callee_id, "no authorised relationship defined"
            )
            return False

        return True  # At least one agent outside hierarchy → open by default

    def can_delegate(self, agent_id: str, task_type: str = "") -> bool:
        """Return True if ``agent_id`` is permitted to delegate tasks."""
        with self._lock:
            # Direct supervisor lookup
            defn = self._defs.get(agent_id)
            if defn is not None:
                return defn.can_delegate

            # Workers inherit delegation permission from their supervisor
            sup = self._worker_to_sup.get(agent_id)
            if sup:
                sup_def = self._defs.get(sup)
                if sup_def is not None:
                    return sup_def.can_delegate

        return True  # Unknown agents: allow by default

    def get_decision_authority(self, chain_id: str) -> str:
        """
        Return the agent_id with final decision authority.

        Returns the first defined authority; returns ``"unknown"`` if no
        hierarchy has been defined yet.
        """
        with self._lock:
            for defn in self._defs.values():
                return defn.final_decision_authority
        return "unknown"

    def check_delegation_depth(self, chain_id: str) -> int:
        """Return the current delegation depth for a chain (0 if not started)."""
        with self._lock:
            return self._delegation_depth.get(chain_id, 0)

    def increment_depth(self, chain_id: str) -> int:
        """Increment delegation depth and return the new value."""
        with self._lock:
            depth = self._delegation_depth.get(chain_id, 0) + 1
            self._delegation_depth[chain_id] = depth
            # Warn when any hierarchy's max_depth is exceeded
            for defn in self._defs.values():
                if depth > defn.max_depth:
                    _log.warning(
                        "Delegation depth %d exceeds max_depth=%d for chain %s",
                        depth,
                        defn.max_depth,
                        chain_id,
                    )
            return depth

    def grant_veto(self, agent_id: str, target_agent_id: str) -> None:
        """Grant ``agent_id`` the right to veto actions by ``target_agent_id``."""
        with self._lock:
            self._veto_rights.setdefault(agent_id, set()).add(target_agent_id)

    def has_veto(self, agent_id: str, target_agent_id: str) -> bool:
        """Return True if ``agent_id`` holds veto rights over ``target_agent_id``."""
        with self._lock:
            return target_agent_id in self._veto_rights.get(agent_id, set())

    def set_quorum(self, chain_id: str, required: int) -> None:
        """Set the required quorum (minimum unique agreeing agents) for a chain."""
        with self._lock:
            self._quorum[chain_id] = required

    def check_quorum(self, chain_id: str, votes: list[str]) -> bool:
        """
        Return True if the number of unique votes meets or exceeds the quorum.

        ``votes`` is a list of agent_id strings (duplicates are ignored).
        """
        with self._lock:
            required = self._quorum.get(chain_id, 1)
        unique_votes = len(set(votes))
        return unique_votes >= required

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fire_delegation_violation(
        self,
        caller_id: str,
        callee_id: str,
        reason: str,
    ) -> None:
        if self._sentinel is None:
            return
        try:
            from agentcop.event import SentinelEvent

            event = SentinelEvent(
                event_id=str(uuid.uuid4()),
                event_type="delegation_violation",
                timestamp=datetime.now(UTC),
                severity="ERROR",
                producer_id=caller_id,
                body=f"Delegation violation: {caller_id} → {callee_id}. {reason}",
                attributes={
                    "caller_id": caller_id,
                    "callee_id": callee_id,
                    "reason": reason,
                    "trust.violation_type": "delegation_violation",
                },
                source_system="trust.hierarchy",
            )
            self._sentinel.push(event)
        except Exception as exc:
            _log.debug("Failed to fire delegation violation SentinelEvent: %s", exc)
