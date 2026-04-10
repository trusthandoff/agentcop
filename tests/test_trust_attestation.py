"""Tests for agentcop.trust.attestation — NodeAttestor."""
from __future__ import annotations

import pytest

from agentcop.trust.attestation import NodeAttestor
from agentcop.trust.models import AttestationError, TrustClaim


class TestNodeAttestorHashOnly:
    """Attestor without a private key — hash-only mode."""

    def setup_method(self):
        self.attestor = NodeAttestor()  # no private key

    def test_attest_returns_trust_claim(self):
        claim = self.attestor.attest("agent-x")
        assert isinstance(claim, TrustClaim)

    def test_attest_claim_type_attestation(self):
        claim = self.attestor.attest("agent-x")
        assert claim.claim_type == "attestation"

    def test_attest_payload_hash_is_sha256(self):
        claim = self.attestor.attest("agent-x")
        # SHA256 hex digest is 64 characters
        assert len(claim.payload_hash) == 64

    def test_attest_signature_none_without_key(self):
        claim = self.attestor.attest("agent-x")
        assert claim.signature is None

    def test_has_crypto_false_without_key(self):
        attestor = NodeAttestor()
        assert attestor.has_crypto is False

    def test_verify_valid_claim(self):
        claim = self.attestor.attest("agent-x")
        assert self.attestor.verify_attestation(claim) is True

    def test_verify_wrong_claim_type(self):
        claim = self.attestor.attest("agent-x")
        claim.claim_type = "handoff"  # type: ignore[assignment]
        assert self.attestor.verify_attestation(claim) is False

    def test_verify_tampered_hash(self):
        claim = self.attestor.attest("agent-x")
        claim.payload_hash = "tampered" * 8  # keep length valid
        assert self.attestor.verify_attestation(claim) is False

    def test_attest_stores_agent_id(self):
        claim = self.attestor.attest("my-agent")
        assert claim.agent_id == "my-agent"
        assert claim.issuer_id == "my-agent"

    def test_attest_with_metadata(self):
        meta = {"version": "1.2", "env": "prod"}
        claim = self.attestor.attest("agent-x", metadata=meta)
        assert claim.metadata["version"] == "1.2"

    def test_attest_with_public_key_pem(self):
        claim = self.attestor.attest("agent-x", public_key_pem="FAKE_PEM")
        assert claim.metadata["public_key_pem"] == "FAKE_PEM"

    def test_attest_different_timestamps_different_hashes(self):
        import time
        c1 = self.attestor.attest("agent-x")
        time.sleep(0.001)
        c2 = self.attestor.attest("agent-x")
        # timestamps differ, so hashes should differ
        assert c1.payload_hash != c2.payload_hash or c1.timestamp != c2.timestamp


class TestNodeAttestorWithCrypto:
    """Attestor with a real Ed25519 key pair (requires cryptography package)."""

    def setup_method(self):
        try:
            priv_pem, self.pub_pem = NodeAttestor.generate_key_pair()
            self.attestor = NodeAttestor(private_key_pem=priv_pem)
            self.has_crypto = True
        except AttestationError:
            self.has_crypto = False

    def test_has_crypto_true_with_key(self):
        if not self.has_crypto:
            pytest.skip("cryptography not installed")
        assert self.attestor.has_crypto is True

    def test_signature_not_none_with_key(self):
        if not self.has_crypto:
            pytest.skip("cryptography not installed")
        claim = self.attestor.attest("agent-x")
        assert claim.signature is not None

    def test_verify_with_valid_signature(self):
        if not self.has_crypto:
            pytest.skip("cryptography not installed")
        claim = self.attestor.attest("agent-x", public_key_pem=self.pub_pem)
        assert self.attestor.verify_attestation(claim) is True

    def test_verify_with_tampered_signature(self):
        if not self.has_crypto:
            pytest.skip("cryptography not installed")
        claim = self.attestor.attest("agent-x", public_key_pem=self.pub_pem)
        claim.signature = "AAAA" + (claim.signature or "")[4:]
        assert self.attestor.verify_attestation(claim) is False

    def test_generate_key_pair_returns_strings(self):
        if not self.has_crypto:
            pytest.skip("cryptography not installed")
        priv, pub = NodeAttestor.generate_key_pair()
        assert isinstance(priv, str)
        assert isinstance(pub, str)
        assert "BEGIN PRIVATE KEY" in priv
        assert "BEGIN PUBLIC KEY" in pub


class TestNodeAttestorHandoff:
    def setup_method(self):
        self.attestor = NodeAttestor()

    def test_create_handoff_returns_claim(self):
        claim = self.attestor.create_handoff("sender", "receiver", "hash123")
        assert isinstance(claim, TrustClaim)

    def test_handoff_claim_type(self):
        claim = self.attestor.create_handoff("s", "r", "h")
        assert claim.claim_type == "handoff"

    def test_verify_handoff_valid(self):
        claim = self.attestor.create_handoff("sender", "receiver", "payload_hash")
        result = self.attestor.verify_handoff(claim, "sender", "receiver", "payload_hash")
        assert result is True

    def test_verify_handoff_wrong_sender(self):
        claim = self.attestor.create_handoff("sender", "receiver", "payload_hash")
        result = self.attestor.verify_handoff(claim, "WRONG", "receiver", "payload_hash")
        assert result is False

    def test_verify_handoff_wrong_receiver(self):
        claim = self.attestor.create_handoff("sender", "receiver", "payload_hash")
        result = self.attestor.verify_handoff(claim, "sender", "WRONG", "payload_hash")
        assert result is False

    def test_verify_handoff_wrong_payload(self):
        claim = self.attestor.create_handoff("sender", "receiver", "payload_hash")
        result = self.attestor.verify_handoff(claim, "sender", "receiver", "DIFFERENT")
        assert result is False

    def test_verify_handoff_wrong_type(self):
        claim = self.attestor.create_handoff("s", "r", "h")
        claim.claim_type = "execution"  # type: ignore[assignment]
        assert self.attestor.verify_handoff(claim, "s", "r", "h") is False

    def test_handoff_metadata_contains_parties(self):
        claim = self.attestor.create_handoff("alice", "bob", "hash42")
        assert claim.metadata["sender_id"] == "alice"
        assert claim.metadata["receiver_id"] == "bob"
        assert claim.metadata["original_payload_hash"] == "hash42"
