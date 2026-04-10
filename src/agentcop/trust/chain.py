"""
TrustChainBuilder — core chain-of-trust implementation.

Each node's payload_hash encodes its content AND the previous claim's ID,
so any mutation anywhere in the chain is detectable at verify_chain() time.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Literal

from .models import ExecutionNode, TrustChain, TrustClaim, make_uuid

_log = logging.getLogger(__name__)


def _hash_node(prev_claim_id: str, node: ExecutionNode) -> str:
    """Compute the payload hash that links a node into the chain."""
    payload = (
        prev_claim_id
        + node.node_id
        + node.agent_id
        + node.context_hash
        + node.output_hash
        + ",".join(node.tool_calls)
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class TrustChainBuilder:
    """
    Builds and verifies a cryptographic chain of trust across execution nodes.

    Usage::

        with TrustChainBuilder(agent_id="my-agent") as chain:
            result = agent.run(task)
        verified = chain.verify_chain()
    """

    def __init__(self, agent_id: str, storage: str = "memory") -> None:
        self._agent_id = agent_id
        self._storage = storage  # "memory" only for now; "sqlite" is future work
        self._chain_id = make_uuid()
        # Each entry is (ExecutionNode, TrustClaim) in insertion order.
        self._entries: list[tuple[ExecutionNode, TrustClaim]] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> TrustChainBuilder:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def add_node(self, node: ExecutionNode) -> TrustClaim:
        """Record an execution node and return the resulting TrustClaim."""
        with self._lock:
            prev_claim_id = self._entries[-1][1].claim_id if self._entries else ""
            payload_hash = _hash_node(prev_claim_id, node)
            claim = TrustClaim(
                claim_id=make_uuid(),
                agent_id=node.agent_id,
                claim_type="execution",
                payload_hash=payload_hash,
                issuer_id=self._agent_id,
                timestamp=time.time(),
                metadata={"node_id": node.node_id, "chain_id": self._chain_id},
            )
            self._entries.append((node, claim))
        _log.debug("Added node %s → claim %s", node.node_id, claim.claim_id)
        return claim

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_chain(self) -> TrustChain:
        """
        Verify the integrity of the entire chain.

        Recomputes payload_hash for each entry and checks it matches the stored
        hash. Returns a TrustChain with verified=True if all hashes match.
        """
        with self._lock:
            entries = list(self._entries)

        if not entries:
            return TrustChain(
                chain_id=self._chain_id,
                root_claim_id="",
                claims=[],
                verified=True,
            )

        claims = [c for _, c in entries]
        broken_at: str | None = None

        for i, (node, claim) in enumerate(entries):
            prev_claim_id = entries[i - 1][1].claim_id if i > 0 else ""
            expected = _hash_node(prev_claim_id, node)
            if expected != claim.payload_hash:
                broken_at = claim.claim_id
                _log.warning(
                    "Chain broken at claim %s (node %s)", claim.claim_id, node.node_id
                )
                break

        return TrustChain(
            chain_id=self._chain_id,
            root_claim_id=claims[0].claim_id,
            claims=claims,
            broken_at=broken_at,
            verified=broken_at is None,
        )

    # ------------------------------------------------------------------
    # Lineage
    # ------------------------------------------------------------------

    def get_lineage(self) -> list[ExecutionNode]:
        """Return execution nodes in the order they were added."""
        with self._lock:
            return [n for n, _ in self._entries]

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_chain(self, format: Literal["json", "compact"] = "json") -> str:
        """Export the chain as JSON or a compact human-readable string."""
        chain = self.verify_chain()

        if format == "compact":
            agent_ids = [c.agent_id for c in chain.claims]
            # Deduplicate preserving order
            seen: set[str] = set()
            unique: list[str] = []
            for a in agent_ids:
                if a not in seen:
                    unique.append(a)
                    seen.add(a)
            chain_str = "→".join(unique) if unique else "(empty)"
            hash_preview = chain.claims[-1].payload_hash[:8] if chain.claims else "none"
            return (
                f"{chain_str} [hash:{hash_preview}] [verified:{str(chain.verified).lower()}]"
            )

        # JSON format
        data = {
            "chain_id": chain.chain_id,
            "root_claim_id": chain.root_claim_id,
            "verified": chain.verified,
            "broken_at": chain.broken_at,
            "claims": [
                {
                    "claim_id": c.claim_id,
                    "agent_id": c.agent_id,
                    "claim_type": c.claim_type,
                    "payload_hash": c.payload_hash,
                    "issuer_id": c.issuer_id,
                    "timestamp": c.timestamp,
                    "signature": c.signature,
                    "metadata": c.metadata,
                }
                for c in chain.claims
            ],
        }
        return json.dumps(data, indent=2)
