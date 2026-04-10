"""Tests for agentcop.trust.hierarchy — AgentHierarchy."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from agentcop.trust.hierarchy import AgentHierarchy


def _simple_hierarchy(sentinel=None) -> AgentHierarchy:
    h = AgentHierarchy(sentinel=sentinel)
    h.define(
        supervisor="orchestrator",
        workers=["agent_a", "agent_b"],
        can_delegate=True,
        max_depth=2,
        final_decision_authority="orchestrator",
    )
    return h


class TestAgentHierarchyDefine:
    def test_define_creates_hierarchy(self):
        h = AgentHierarchy()
        h.define("sup", ["w1", "w2"], True, 3, "sup")
        assert "sup" in h._defs

    def test_define_registers_worker_to_supervisor(self):
        h = AgentHierarchy()
        h.define("sup", ["w1"], True, 3, "sup")
        assert h._worker_to_sup["w1"] == "sup"


class TestAgentHierarchyCanCall:
    def test_supervisor_can_call_worker(self):
        h = _simple_hierarchy()
        assert h.can_call("orchestrator", "agent_a") is True

    def test_supervisor_can_call_all_workers(self):
        h = _simple_hierarchy()
        assert h.can_call("orchestrator", "agent_b") is True

    def test_worker_can_call_supervisor(self):
        h = _simple_hierarchy()
        assert h.can_call("agent_a", "orchestrator") is True

    def test_peers_can_call_each_other(self):
        h = _simple_hierarchy()
        assert h.can_call("agent_a", "agent_b") is True

    def test_agents_outside_hierarchy_can_call(self):
        h = _simple_hierarchy()
        assert h.can_call("outsider_x", "outsider_y") is True

    def test_agent_in_hierarchy_cannot_call_unrelated_in_hierarchy(self):
        h = AgentHierarchy()
        h.define("sup1", ["w1"], True, 2, "sup1")
        h.define("sup2", ["w2"], True, 2, "sup2")
        # w1 and w2 are both in hierarchies but unrelated
        assert h.can_call("w1", "w2") is False

    def test_unrelated_fires_sentinel_event(self):
        sentinel = MagicMock()
        h = AgentHierarchy(sentinel=sentinel)
        h.define("sup1", ["w1"], True, 2, "sup1")
        h.define("sup2", ["w2"], True, 2, "sup2")
        h.can_call("w1", "w2")
        sentinel.push.assert_called_once()

    def test_sentinel_event_is_delegation_violation(self):
        sentinel = MagicMock()
        h = AgentHierarchy(sentinel=sentinel)
        h.define("sup1", ["w1"], True, 2, "sup1")
        h.define("sup2", ["w2"], True, 2, "sup2")
        h.can_call("w1", "w2")
        event = sentinel.push.call_args[0][0]
        assert event.event_type == "delegation_violation"
        assert event.severity == "ERROR"


class TestAgentHierarchyCanDelegate:
    def test_supervisor_with_can_delegate_true(self):
        h = AgentHierarchy()
        h.define("sup", ["w1"], can_delegate=True, max_depth=2, final_decision_authority="sup")
        assert h.can_delegate("sup") is True

    def test_supervisor_with_can_delegate_false(self):
        h = AgentHierarchy()
        h.define("sup", ["w1"], can_delegate=False, max_depth=2, final_decision_authority="sup")
        assert h.can_delegate("sup") is False

    def test_worker_inherits_can_delegate(self):
        h = AgentHierarchy()
        h.define("sup", ["w1"], can_delegate=True, max_depth=2, final_decision_authority="sup")
        assert h.can_delegate("w1") is True

    def test_worker_inherits_cannot_delegate(self):
        h = AgentHierarchy()
        h.define("sup", ["w1"], can_delegate=False, max_depth=2, final_decision_authority="sup")
        assert h.can_delegate("w1") is False

    def test_unknown_agent_can_delegate_by_default(self):
        h = AgentHierarchy()
        assert h.can_delegate("unknown-agent") is True


class TestAgentHierarchyDecisionAuthority:
    def test_get_decision_authority(self):
        h = _simple_hierarchy()
        authority = h.get_decision_authority("chain-1")
        assert authority == "orchestrator"

    def test_get_decision_authority_no_hierarchy(self):
        h = AgentHierarchy()
        assert h.get_decision_authority("chain-1") == "unknown"


class TestAgentHierarchyDelegationDepth:
    def test_initial_depth_zero(self):
        h = AgentHierarchy()
        assert h.check_delegation_depth("chain-1") == 0

    def test_increment_depth(self):
        h = AgentHierarchy()
        depth = h.increment_depth("chain-1")
        assert depth == 1
        assert h.check_delegation_depth("chain-1") == 1

    def test_increment_multiple_times(self):
        h = AgentHierarchy()
        for _ in range(5):
            h.increment_depth("chain-x")
        assert h.check_delegation_depth("chain-x") == 5

    def test_independent_chains(self):
        h = AgentHierarchy()
        h.increment_depth("chain-a")
        h.increment_depth("chain-a")
        h.increment_depth("chain-b")
        assert h.check_delegation_depth("chain-a") == 2
        assert h.check_delegation_depth("chain-b") == 1


class TestAgentHierarchyVeto:
    def test_grant_veto(self):
        h = AgentHierarchy()
        h.grant_veto("sup", "agent_a")
        assert h.has_veto("sup", "agent_a") is True

    def test_no_veto_by_default(self):
        h = AgentHierarchy()
        assert h.has_veto("sup", "agent_a") is False

    def test_veto_is_directional(self):
        h = AgentHierarchy()
        h.grant_veto("sup", "agent_a")
        assert h.has_veto("agent_a", "sup") is False

    def test_multiple_veto_targets(self):
        h = AgentHierarchy()
        h.grant_veto("sup", "agent_a")
        h.grant_veto("sup", "agent_b")
        assert h.has_veto("sup", "agent_a") is True
        assert h.has_veto("sup", "agent_b") is True


class TestAgentHierarchyQuorum:
    def test_set_and_check_quorum(self):
        h = AgentHierarchy()
        h.set_quorum("chain-1", 3)
        assert h.check_quorum("chain-1", ["a", "b", "c"]) is True

    def test_insufficient_votes(self):
        h = AgentHierarchy()
        h.set_quorum("chain-1", 3)
        assert h.check_quorum("chain-1", ["a", "b"]) is False

    def test_default_quorum_is_one(self):
        h = AgentHierarchy()
        assert h.check_quorum("chain-1", ["a"]) is True

    def test_quorum_deduplicates_votes(self):
        h = AgentHierarchy()
        h.set_quorum("chain-1", 3)
        # Same agent voting three times counts as 1 unique vote
        assert h.check_quorum("chain-1", ["a", "a", "a"]) is False

    def test_quorum_exactly_met(self):
        h = AgentHierarchy()
        h.set_quorum("chain-1", 2)
        assert h.check_quorum("chain-1", ["a", "b"]) is True


class TestAgentHierarchyMultiple:
    def test_multiple_hierarchies(self):
        h = AgentHierarchy()
        h.define("sup1", ["w1a", "w1b"], True, 2, "sup1")
        h.define("sup2", ["w2a", "w2b"], False, 1, "sup2")
        assert h.can_call("sup1", "w1a") is True
        assert h.can_call("sup2", "w2a") is True
        assert h.can_delegate("sup1") is True
        assert h.can_delegate("sup2") is False


class TestAgentHierarchyThreadSafety:
    def test_concurrent_define_and_can_call(self):
        h = AgentHierarchy()
        errors: list[Exception] = []

        def writer(i: int) -> None:
            try:
                h.define(f"sup{i}", [f"w{i}a"], True, 2, f"sup{i}")
            except Exception as exc:
                errors.append(exc)

        def reader(i: int) -> None:
            try:
                h.can_call(f"sup{i}", f"w{i}a")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)] + [
            threading.Thread(target=reader, args=(i,)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
