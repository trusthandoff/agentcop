"""
NodeAttestor — agent identity verification and inter-node handoff verification.

Falls back to hash-only mode if the ``cryptography`` package is not installed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from .models import AttestationError, TrustClaim, make_uuid

_log = logging.getLogger(__name__)


def _try_ed25519() -> dict[str, Any] | None:
    """Attempt to import Ed25519 primitives from the cryptography package."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            PublicFormat,
            load_pem_private_key,
            load_pem_public_key,
        )

        return {
            "Ed25519PrivateKey": Ed25519PrivateKey,
            "Ed25519PublicKey": Ed25519PublicKey,
            "Encoding": Encoding,
            "PublicFormat": PublicFormat,
            "PrivateFormat": PrivateFormat,
            "NoEncryption": NoEncryption,
            "load_pem_public_key": load_pem_public_key,
            "load_pem_private_key": load_pem_private_key,
            "InvalidSignature": InvalidSignature,
        }
    except ImportError:
        return None


def _hash_attestation(agent_id: str, timestamp: float, metadata: dict) -> str:
    payload = f"{agent_id}:{timestamp}:{json.dumps(metadata, sort_keys=True)}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _handoff_hash(
    sender_id: str,
    receiver_id: str,
    payload_hash: str,
    timestamp: float,
) -> str:
    payload = f"{sender_id}:{receiver_id}:{payload_hash}:{timestamp}"
    return hashlib.sha256(payload.encode()).hexdigest()


class NodeAttestor:
    """
    Provides node identity attestation with optional Ed25519 signatures.

    If the ``cryptography`` package is not installed, operates in hash-only mode:
    claims are created with ``signature=None`` and verified via their payload_hash alone.
    """

    def __init__(self, private_key_pem: str | None = None) -> None:
        self._crypto = _try_ed25519()
        self._private_key_pem = private_key_pem
        self._has_crypto = self._crypto is not None and private_key_pem is not None

    @property
    def has_crypto(self) -> bool:
        """True if Ed25519 signing is available and a private key was provided."""
        return self._has_crypto

    def attest(
        self,
        agent_id: str,
        public_key_pem: str | None = None,
        metadata: dict | None = None,
    ) -> TrustClaim:
        """
        Create an attestation TrustClaim for the given agent.

        Signs the payload with Ed25519 if a private key was provided and the
        ``cryptography`` package is installed. Otherwise operates in hash-only mode.
        """
        if metadata is None:
            metadata = {}
        timestamp = time.time()
        # Exclude public_key_pem from hash input so key rotation doesn't invalidate old claims
        payload_hash = _hash_attestation(agent_id, timestamp, metadata)
        signature: str | None = None

        if self._has_crypto and self._private_key_pem and self._crypto:
            try:
                import base64

                private_key = self._crypto["load_pem_private_key"](
                    self._private_key_pem.encode(), password=None
                )
                sig_bytes = private_key.sign(payload_hash.encode())
                signature = base64.b64encode(sig_bytes).decode()
            except Exception as exc:
                _log.warning("Ed25519 signing failed, falling back to hash-only: %s", exc)

        return TrustClaim(
            claim_id=make_uuid(),
            agent_id=agent_id,
            claim_type="attestation",
            payload_hash=payload_hash,
            issuer_id=agent_id,
            timestamp=timestamp,
            signature=signature,
            metadata={**metadata, "public_key_pem": public_key_pem or ""},
        )

    def verify_attestation(self, claim: TrustClaim) -> bool:
        """
        Verify an attestation claim.

        If the claim has a signature and cryptography is available, verifies the
        Ed25519 signature. Otherwise verifies the payload hash only.
        """
        if claim.claim_type != "attestation":
            return False

        # Recompute expected hash (exclude public_key_pem, as in attest())
        metadata_copy = {k: v for k, v in claim.metadata.items() if k != "public_key_pem"}
        expected_hash = _hash_attestation(claim.agent_id, claim.timestamp, metadata_copy)

        if claim.payload_hash != expected_hash:
            return False

        if claim.signature is None:
            return True  # Hash-only mode: hash matches

        if self._crypto is None:
            _log.debug("cryptography not installed; skipping Ed25519 verification")
            return True  # Can't verify signature; hash already confirmed above

        public_key_pem = claim.metadata.get("public_key_pem", "")
        if not public_key_pem:
            return True  # No public key registered; accept hash-only

        try:
            import base64

            public_key = self._crypto["load_pem_public_key"](public_key_pem.encode())
            sig_bytes = base64.b64decode(claim.signature)
            public_key.verify(sig_bytes, claim.payload_hash.encode())
            return True
        except Exception:
            return False

    def create_handoff(
        self,
        sender_id: str,
        receiver_id: str,
        payload_hash: str,
    ) -> TrustClaim:
        """
        Create a handoff claim asserting that receiver got exactly what sender sent.

        The handoff_hash encodes sender, receiver, original payload hash, and timestamp.
        Any mutation in transit will cause verify_handoff() to return False.
        """
        timestamp = time.time()
        h_hash = _handoff_hash(sender_id, receiver_id, payload_hash, timestamp)
        return TrustClaim(
            claim_id=make_uuid(),
            agent_id=sender_id,
            claim_type="handoff",
            payload_hash=h_hash,
            issuer_id=sender_id,
            timestamp=timestamp,
            metadata={
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "original_payload_hash": payload_hash,
            },
        )

    def verify_handoff(
        self,
        claim: TrustClaim,
        sender_id: str,
        receiver_id: str,
        payload_hash: str,
    ) -> bool:
        """Verify that a handoff claim matches the expected sender, receiver, and payload."""
        if claim.claim_type != "handoff":
            return False
        expected = _handoff_hash(sender_id, receiver_id, payload_hash, claim.timestamp)
        return expected == claim.payload_hash

    @staticmethod
    def generate_key_pair() -> tuple[str, str]:
        """
        Generate an Ed25519 key pair. Returns (private_key_pem, public_key_pem).

        Raises AttestationError if cryptography is not installed.
        """
        crypto = _try_ed25519()
        if crypto is None:
            raise AttestationError(
                "cryptography package required for key generation: pip install agentcop[badge]"
            )
        private_key = crypto["Ed25519PrivateKey"].generate()
        priv_pem = private_key.private_bytes(
            encoding=crypto["Encoding"].PEM,
            format=crypto["PrivateFormat"].PKCS8,
            encryption_algorithm=crypto["NoEncryption"](),
        ).decode()
        pub_pem = (
            private_key.public_key()
            .public_bytes(
                encoding=crypto["Encoding"].PEM,
                format=crypto["PublicFormat"].SubjectPublicKeyInfo,
            )
            .decode()
        )
        return priv_pem, pub_pem
