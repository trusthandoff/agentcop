"""
Pure dataclass models for the TrustChain layer.
No external dependencies — stdlib only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TrustError(Exception):
    """Base exception for all trust module errors."""


class AttestationError(TrustError):
    """Raised when node attestation fails."""


class BoundaryViolationError(TrustError):
    """Raised when a tool trust boundary is crossed without permission."""


class DelegationViolationError(TrustError):
    """Raised when an agent exceeds its delegation authority."""


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


@dataclass
class TrustClaim:
    """A single verifiable unit of trust in an execution chain."""

    claim_id: str
    agent_id: str
    claim_type: Literal["attestation", "handoff", "execution", "rag", "memory"]
    payload_hash: str
    issuer_id: str
    timestamp: float
    signature: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class TrustChain:
    """An ordered sequence of TrustClaims forming a verifiable execution history."""

    chain_id: str
    root_claim_id: str
    claims: list[TrustClaim]
    broken_at: str | None = None
    verified: bool = False


@dataclass
class ExecutionNode:
    """A single step in a multi-agent execution pipeline."""

    node_id: str
    agent_id: str
    tool_calls: list[str]
    context_hash: str
    output_hash: str
    duration_ms: int
    attestation: TrustClaim | None = None


def make_uuid() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())
