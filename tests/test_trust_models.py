"""Tests for agentcop.trust.models — pure dataclasses and exceptions."""
from __future__ import annotations

import uuid

import pytest

from agentcop.trust.models import (
    AttestationError,
    BoundaryViolationError,
    DelegationViolationError,
    ExecutionNode,
    TrustChain,
    TrustClaim,
    TrustError,
    make_uuid,
)

# ---------------------------------------------------------------------------
# TrustClaim
# ---------------------------------------------------------------------------


class TestTrustClaim:
    def test_create_all_fields(self):
        claim = TrustClaim(
            claim_id="cid",
            agent_id="agent-1",
            claim_type="execution",
            payload_hash="abc123",
            issuer_id="issuer-1",
            timestamp=1.0,
            signature="sig",
            metadata={"k": "v"},
        )
        assert claim.claim_id == "cid"
        assert claim.agent_id == "agent-1"
        assert claim.signature == "sig"
        assert claim.metadata == {"k": "v"}

    def test_default_signature_none(self):
        claim = TrustClaim(
            claim_id="c",
            agent_id="a",
            claim_type="attestation",
            payload_hash="h",
            issuer_id="i",
            timestamp=0.0,
        )
        assert claim.signature is None

    def test_default_metadata_empty(self):
        claim = TrustClaim(
            claim_id="c",
            agent_id="a",
            claim_type="handoff",
            payload_hash="h",
            issuer_id="i",
            timestamp=0.0,
        )
        assert claim.metadata == {}

    def test_all_claim_types(self):
        for ct in ("attestation", "handoff", "execution", "rag", "memory"):
            c = TrustClaim(
                claim_id="c",
                agent_id="a",
                claim_type=ct,  # type: ignore[arg-type]
                payload_hash="h",
                issuer_id="i",
                timestamp=0.0,
            )
            assert c.claim_type == ct

    def test_metadata_is_mutable_per_instance(self):
        c1 = TrustClaim("c1", "a", "rag", "h", "i", 0.0)
        c2 = TrustClaim("c2", "a", "rag", "h", "i", 0.0)
        c1.metadata["x"] = 1
        assert "x" not in c2.metadata


# ---------------------------------------------------------------------------
# TrustChain
# ---------------------------------------------------------------------------


class TestTrustChain:
    def test_create(self):
        chain = TrustChain(chain_id="ch", root_claim_id="rc", claims=[])
        assert chain.chain_id == "ch"
        assert chain.root_claim_id == "rc"

    def test_default_broken_at_none(self):
        chain = TrustChain(chain_id="ch", root_claim_id="rc", claims=[])
        assert chain.broken_at is None

    def test_default_verified_false(self):
        chain = TrustChain(chain_id="ch", root_claim_id="rc", claims=[])
        assert chain.verified is False

    def test_verified_true(self):
        chain = TrustChain(chain_id="ch", root_claim_id="rc", claims=[], verified=True)
        assert chain.verified is True


# ---------------------------------------------------------------------------
# ExecutionNode
# ---------------------------------------------------------------------------


class TestExecutionNode:
    def test_create(self):
        node = ExecutionNode(
            node_id="n1",
            agent_id="agent-a",
            tool_calls=["tool1", "tool2"],
            context_hash="ctx",
            output_hash="out",
            duration_ms=100,
        )
        assert node.node_id == "n1"
        assert node.tool_calls == ["tool1", "tool2"]

    def test_default_attestation_none(self):
        node = ExecutionNode("n", "a", [], "ctx", "out", 0)
        assert node.attestation is None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TestExceptions:
    def test_trust_error_is_exception(self):
        assert issubclass(TrustError, Exception)

    def test_attestation_error_is_trust_error(self):
        assert issubclass(AttestationError, TrustError)

    def test_boundary_violation_error_is_trust_error(self):
        assert issubclass(BoundaryViolationError, TrustError)

    def test_delegation_violation_error_is_trust_error(self):
        assert issubclass(DelegationViolationError, TrustError)

    def test_raise_trust_error(self):
        with pytest.raises(TrustError):
            raise TrustError("boom")

    def test_raise_attestation_error(self):
        with pytest.raises(TrustError):
            raise AttestationError("bad attest")


# ---------------------------------------------------------------------------
# make_uuid
# ---------------------------------------------------------------------------


class TestMakeUuid:
    def test_returns_string(self):
        assert isinstance(make_uuid(), str)

    def test_valid_uuid(self):
        u = make_uuid()
        parsed = uuid.UUID(u)
        assert str(parsed) == u

    def test_unique(self):
        assert make_uuid() != make_uuid()
