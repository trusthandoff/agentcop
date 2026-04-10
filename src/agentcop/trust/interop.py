"""
TrustInterop — export and import TrustClaims across runtimes.

The portable format is ``agentcop.trust.v1.<base64url(json)>``.
A SHA256 checksum is embedded in the payload so tampering is detectable
without requiring Ed25519 or any external crypto library.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging

from .models import TrustClaim, TrustError

_log = logging.getLogger(__name__)

_PREFIX = "agentcop.trust.v1."


class TrustInterop:
    """
    Exports TrustClaims to a portable string that any runtime can verify.

    Cross-runtime formats supported:
    - ``agentcop.trust.v1.*`` (native)
    - OpenAI function call format
    - Anthropic tool use format
    """

    @staticmethod
    def export_portable_claim(claim: TrustClaim) -> str:
        """
        Serialise a TrustClaim to a portable, verifiable string.

        Embeds a checksum so any receiver can detect tampering without
        installing agentcop.
        """
        data: dict = {
            "claim_id": claim.claim_id,
            "agent_id": claim.agent_id,
            "claim_type": claim.claim_type,
            "payload_hash": claim.payload_hash,
            "issuer_id": claim.issuer_id,
            "timestamp": claim.timestamp,
            "signature": claim.signature,
            "metadata": claim.metadata,
        }
        # Compute checksum over the sorted serialisation (signature excluded)
        payload_str = json.dumps(data, separators=(",", ":"), sort_keys=True)
        checksum = hashlib.sha256(payload_str.encode()).hexdigest()[:16]
        data["_checksum"] = checksum

        full_str = json.dumps(data, separators=(",", ":"), sort_keys=True)
        encoded = base64.urlsafe_b64encode(full_str.encode()).decode().rstrip("=")
        return f"{_PREFIX}{encoded}"

    @staticmethod
    def import_claim(portable: str) -> TrustClaim:
        """
        Deserialise and verify a portable claim string.

        Raises :class:`TrustError` if the format is invalid or the checksum fails.
        """
        if not portable.startswith(_PREFIX):
            raise TrustError(
                f"Unknown portable claim format. Expected prefix '{_PREFIX}', "
                f"got '{portable[:30]}...'"
            )
        encoded = portable[len(_PREFIX):]

        # Restore base64 padding
        padding = 4 - (len(encoded) % 4)
        if padding != 4:
            encoded += "=" * padding

        try:
            raw = base64.urlsafe_b64decode(encoded).decode()
            data = json.loads(raw)
        except Exception as exc:
            raise TrustError(f"Failed to decode portable claim: {exc}") from exc

        # Verify embedded checksum
        checksum = data.pop("_checksum", None)
        payload_str = json.dumps(data, separators=(",", ":"), sort_keys=True)
        expected = hashlib.sha256(payload_str.encode()).hexdigest()[:16]
        if checksum != expected:
            raise TrustError(
                "Portable claim checksum mismatch — claim may have been tampered with"
            )

        try:
            return TrustClaim(
                claim_id=data["claim_id"],
                agent_id=data["agent_id"],
                claim_type=data["claim_type"],
                payload_hash=data["payload_hash"],
                issuer_id=data["issuer_id"],
                timestamp=float(data["timestamp"]),
                signature=data.get("signature"),
                metadata=data.get("metadata", {}),
            )
        except (KeyError, TypeError) as exc:
            raise TrustError(f"Malformed portable claim: {exc}") from exc

    @staticmethod
    def to_openai_function_format(claim: TrustClaim) -> dict:
        """Export a TrustClaim in OpenAI function call format."""
        return {
            "name": "trust_claim",
            "arguments": json.dumps({
                "claim_id": claim.claim_id,
                "agent_id": claim.agent_id,
                "claim_type": claim.claim_type,
                "payload_hash": claim.payload_hash,
                "issuer_id": claim.issuer_id,
                "timestamp": claim.timestamp,
            }),
        }

    @staticmethod
    def to_anthropic_tool_format(claim: TrustClaim) -> dict:
        """Export a TrustClaim in Anthropic tool use format."""
        return {
            "type": "tool_use",
            "name": "trust_claim",
            "input": {
                "claim_id": claim.claim_id,
                "agent_id": claim.agent_id,
                "claim_type": claim.claim_type,
                "payload_hash": claim.payload_hash,
                "issuer_id": claim.issuer_id,
                "timestamp": claim.timestamp,
            },
        }
