"""Tests for agentcop.trust.interop — TrustInterop."""
from __future__ import annotations

import json

import pytest

from agentcop.trust.interop import _PREFIX, TrustInterop
from agentcop.trust.models import TrustClaim, TrustError


def _sample_claim(**kwargs) -> TrustClaim:
    defaults: dict = {
        "claim_id": "test-claim-id",
        "agent_id": "agent-x",
        "claim_type": "execution",
        "payload_hash": "a" * 64,
        "issuer_id": "issuer-1",
        "timestamp": 1_700_000_000.0,
        "signature": None,
        "metadata": {"env": "test"},
    }
    defaults.update(kwargs)
    return TrustClaim(**defaults)  # type: ignore[arg-type]


class TestExportPortableClaim:
    def test_returns_string_with_prefix(self):
        claim = _sample_claim()
        portable = TrustInterop.export_portable_claim(claim)
        assert portable.startswith(_PREFIX)

    def test_export_is_string(self):
        claim = _sample_claim()
        assert isinstance(TrustInterop.export_portable_claim(claim), str)

    def test_export_includes_agent_id(self):
        claim = _sample_claim(agent_id="special-agent")
        portable = TrustInterop.export_portable_claim(claim)
        # Should be decodable and contain the agent_id
        imported = TrustInterop.import_claim(portable)
        assert imported.agent_id == "special-agent"

    def test_export_preserves_all_fields(self):
        claim = _sample_claim(
            claim_id="cid-123",
            claim_type="attestation",
            payload_hash="b" * 64,
            metadata={"key": "val"},
        )
        portable = TrustInterop.export_portable_claim(claim)
        imported = TrustInterop.import_claim(portable)
        assert imported.claim_id == "cid-123"
        assert imported.claim_type == "attestation"
        assert imported.metadata == {"key": "val"}

    def test_export_with_signature(self):
        claim = _sample_claim(signature="base64sighere==")
        portable = TrustInterop.export_portable_claim(claim)
        imported = TrustInterop.import_claim(portable)
        assert imported.signature == "base64sighere=="

    def test_export_with_none_signature(self):
        claim = _sample_claim(signature=None)
        portable = TrustInterop.export_portable_claim(claim)
        imported = TrustInterop.import_claim(portable)
        assert imported.signature is None


class TestImportPortableClaim:
    def test_round_trip(self):
        claim = _sample_claim()
        portable = TrustInterop.export_portable_claim(claim)
        recovered = TrustInterop.import_claim(portable)
        assert recovered.claim_id == claim.claim_id
        assert recovered.agent_id == claim.agent_id
        assert recovered.payload_hash == claim.payload_hash
        assert recovered.timestamp == claim.timestamp

    def test_invalid_prefix_raises_trust_error(self):
        with pytest.raises(TrustError, match="Unknown"):
            TrustInterop.import_claim("invalid.format.here")

    def test_tampered_payload_raises_trust_error(self):
        claim = _sample_claim()
        portable = TrustInterop.export_portable_claim(claim)
        # Tamper with the last few characters of the encoded part
        tampered = portable[:-5] + "AAAAA"
        with pytest.raises(TrustError):
            TrustInterop.import_claim(tampered)

    def test_empty_string_raises_trust_error(self):
        with pytest.raises(TrustError):
            TrustInterop.import_claim("")

    def test_metadata_preserved_round_trip(self):
        claim = _sample_claim(metadata={"a": 1, "b": "hello"})
        portable = TrustInterop.export_portable_claim(claim)
        recovered = TrustInterop.import_claim(portable)
        assert recovered.metadata["a"] == 1
        assert recovered.metadata["b"] == "hello"


class TestCrossRuntimeFormats:
    def test_to_openai_function_format(self):
        claim = _sample_claim()
        result = TrustInterop.to_openai_function_format(claim)
        assert result["name"] == "trust_claim"
        assert "arguments" in result
        args = json.loads(result["arguments"])
        assert args["claim_id"] == claim.claim_id
        assert args["agent_id"] == claim.agent_id

    def test_to_anthropic_tool_format(self):
        claim = _sample_claim()
        result = TrustInterop.to_anthropic_tool_format(claim)
        assert result["type"] == "tool_use"
        assert result["name"] == "trust_claim"
        assert "input" in result
        assert result["input"]["claim_id"] == claim.claim_id

    def test_openai_format_contains_payload_hash(self):
        claim = _sample_claim()
        result = TrustInterop.to_openai_function_format(claim)
        args = json.loads(result["arguments"])
        assert "payload_hash" in args

    def test_anthropic_format_contains_issuer_id(self):
        claim = _sample_claim(issuer_id="trusted-issuer")
        result = TrustInterop.to_anthropic_tool_format(claim)
        assert result["input"]["issuer_id"] == "trusted-issuer"
