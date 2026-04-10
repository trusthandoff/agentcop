"""Tests for agentcop.trust.chain — TrustChainBuilder."""
from __future__ import annotations

import json
import threading

from agentcop.trust.chain import TrustChainBuilder, _hash_node
from agentcop.trust.models import ExecutionNode, TrustClaim


def _node(node_id: str = "n1", agent_id: str = "agent-a") -> ExecutionNode:
    return ExecutionNode(
        node_id=node_id,
        agent_id=agent_id,
        tool_calls=["search"],
        context_hash="ctx" + node_id,
        output_hash="out" + node_id,
        duration_ms=50,
    )


class TestTrustChainBuilderBasic:
    def test_empty_chain_is_verified(self):
        b = TrustChainBuilder(agent_id="a")
        chain = b.verify_chain()
        assert chain.verified is True
        assert chain.claims == []
        assert chain.broken_at is None

    def test_single_node_verified(self):
        b = TrustChainBuilder(agent_id="a")
        b.add_node(_node())
        chain = b.verify_chain()
        assert chain.verified is True
        assert len(chain.claims) == 1

    def test_multi_node_verified(self):
        b = TrustChainBuilder(agent_id="a")
        for i in range(5):
            b.add_node(_node(f"n{i}"))
        chain = b.verify_chain()
        assert chain.verified is True
        assert len(chain.claims) == 5

    def test_add_node_returns_trust_claim(self):
        b = TrustChainBuilder(agent_id="a")
        claim = b.add_node(_node())
        assert isinstance(claim, TrustClaim)

    def test_add_node_sets_execution_claim_type(self):
        b = TrustChainBuilder(agent_id="a")
        claim = b.add_node(_node())
        assert claim.claim_type == "execution"

    def test_add_node_sets_issuer_to_builder_agent_id(self):
        b = TrustChainBuilder(agent_id="my-orchestrator")
        claim = b.add_node(_node())
        assert claim.issuer_id == "my-orchestrator"

    def test_root_claim_id_is_first_claim(self):
        b = TrustChainBuilder(agent_id="a")
        c1 = b.add_node(_node("n1"))
        b.add_node(_node("n2"))
        chain = b.verify_chain()
        assert chain.root_claim_id == c1.claim_id

    def test_unique_chain_ids(self):
        b1 = TrustChainBuilder(agent_id="a")
        b2 = TrustChainBuilder(agent_id="a")
        assert b1._chain_id != b2._chain_id


class TestTrustChainVerification:
    def test_broken_chain_detected(self):
        b = TrustChainBuilder(agent_id="a")
        b.add_node(_node("n1"))
        claim2 = b.add_node(_node("n2"))
        # Tamper with the second claim's hash
        claim2.payload_hash = "tampered"
        chain = b.verify_chain()
        assert chain.verified is False
        assert chain.broken_at == claim2.claim_id

    def test_broken_at_first_node(self):
        b = TrustChainBuilder(agent_id="a")
        claim = b.add_node(_node("n1"))
        claim.payload_hash = "bad"
        chain = b.verify_chain()
        assert chain.verified is False
        assert chain.broken_at == claim.claim_id

    def test_broken_at_middle_node(self):
        b = TrustChainBuilder(agent_id="a")
        b.add_node(_node("n1"))
        claim_mid = b.add_node(_node("n2"))
        b.add_node(_node("n3"))
        claim_mid.payload_hash = "x"
        chain = b.verify_chain()
        assert chain.verified is False
        assert chain.broken_at == claim_mid.claim_id

    def test_chain_links_via_previous_claim_id(self):
        b = TrustChainBuilder(agent_id="a")
        node1 = _node("n1")
        node2 = _node("n2")
        c1 = b.add_node(node1)
        c2 = b.add_node(node2)
        # c2's payload_hash must encode c1's claim_id
        expected = _hash_node(c1.claim_id, node2)
        assert c2.payload_hash == expected

    def test_empty_tool_calls_node(self):
        b = TrustChainBuilder(agent_id="a")
        node = ExecutionNode("n", "a", [], "ctx", "out", 0)
        b.add_node(node)
        chain = b.verify_chain()
        assert chain.verified is True


class TestTrustChainLineage:
    def test_get_lineage_returns_nodes_in_order(self):
        b = TrustChainBuilder(agent_id="a")
        nodes = [_node(f"n{i}") for i in range(4)]
        for n in nodes:
            b.add_node(n)
        lineage = b.get_lineage()
        assert [n.node_id for n in lineage] == ["n0", "n1", "n2", "n3"]

    def test_get_lineage_empty(self):
        b = TrustChainBuilder(agent_id="a")
        assert b.get_lineage() == []


class TestTrustChainExport:
    def test_export_json_format(self):
        b = TrustChainBuilder(agent_id="a")
        b.add_node(_node("n1", "agent-x"))
        out = b.export_chain(format="json")
        data = json.loads(out)
        assert "chain_id" in data
        assert "claims" in data
        assert data["claims"][0]["claim_type"] == "execution"

    def test_export_json_has_verified_field(self):
        b = TrustChainBuilder(agent_id="a")
        b.add_node(_node())
        data = json.loads(b.export_chain("json"))
        assert "verified" in data

    def test_export_compact_arrow_separator(self):
        b = TrustChainBuilder(agent_id="a")
        b.add_node(_node("n1", "agent-x"))
        b.add_node(_node("n2", "agent-y"))
        out = b.export_chain("compact")
        assert "→" in out

    def test_export_compact_shows_verified_true(self):
        b = TrustChainBuilder(agent_id="a")
        b.add_node(_node())
        out = b.export_chain("compact")
        assert "[verified:true]" in out

    def test_export_compact_shows_hash(self):
        b = TrustChainBuilder(agent_id="a")
        b.add_node(_node())
        out = b.export_chain("compact")
        assert "[hash:" in out

    def test_export_compact_empty_chain(self):
        b = TrustChainBuilder(agent_id="a")
        out = b.export_chain("compact")
        assert "(empty)" in out
        assert "[verified:true]" in out

    def test_export_compact_deduplicates_agent_ids(self):
        b = TrustChainBuilder(agent_id="a")
        b.add_node(_node("n1", "agent-x"))
        b.add_node(_node("n2", "agent-x"))
        b.add_node(_node("n3", "agent-y"))
        out = b.export_chain("compact")
        # agent-x should appear only once
        assert out.count("agent-x") == 1


class TestTrustChainContextManager:
    def test_context_manager(self):
        with TrustChainBuilder(agent_id="a") as b:
            b.add_node(_node())
        chain = b.verify_chain()
        assert chain.verified is True


class TestTrustChainThreadSafety:
    def test_concurrent_add_node(self):
        b = TrustChainBuilder(agent_id="a")
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                b.add_node(_node(f"n{i}", f"agent-{i}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(b.get_lineage()) == 20
