"""
agentcop.trust — TrustChain layer for cryptographic verification of multi-agent pipelines.

Usage::

    from agentcop.trust import TrustChainBuilder

    with TrustChainBuilder(agent_id="my-agent") as chain:
        result = agent.run(task)
    verified = chain.verify_chain()  # TrustChain(verified=True, claims=[...])

Full import::

    from agentcop.trust import (
        TrustChainBuilder,
        NodeAttestor,
        ToolTrustBoundary,
        ProvenanceTracker,
        ExecutionLineage,
        ContextGuard,
        RAGTrustLayer,
        MemoryGuard,
        AgentHierarchy,
        TrustInterop,
        TrustObserver,
        TrustClaim,
        TrustChain,
        ExecutionNode,
    )
"""

from .attestation import NodeAttestor
from .boundaries import BoundaryResult, ToolTrustBoundary
from .chain import TrustChainBuilder
from .context_guard import ContextGuard, MutationReport
from .hierarchy import AgentHierarchy, HierarchyDefinition
from .interop import TrustInterop
from .lineage import ExecutionLineage
from .memory_guard import MemoryGuard, MemoryIntegrityResult
from .memory_guard import PoisoningAlert as MemoryPoisoningAlert
from .models import (
    AttestationError,
    BoundaryViolationError,
    DelegationViolationError,
    ExecutionNode,
    TrustChain,
    TrustClaim,
    TrustError,
)
from .observability import TrustObserver
from .provenance import ProvenanceRecord, ProvenanceTracker
from .rag_trust import PoisoningAlert as RAGPoisoningAlert
from .rag_trust import RAGTrustLayer, RAGTrustResult

__all__ = [
    # Core models
    "TrustClaim",
    "TrustChain",
    "ExecutionNode",
    # Exceptions
    "TrustError",
    "AttestationError",
    "BoundaryViolationError",
    "DelegationViolationError",
    # Chain builder
    "TrustChainBuilder",
    # Attestation
    "NodeAttestor",
    # Boundaries
    "ToolTrustBoundary",
    "BoundaryResult",
    # Provenance
    "ProvenanceTracker",
    "ProvenanceRecord",
    # Lineage
    "ExecutionLineage",
    # Context guard
    "ContextGuard",
    "MutationReport",
    # RAG trust
    "RAGTrustLayer",
    "RAGTrustResult",
    "RAGPoisoningAlert",
    # Memory guard
    "MemoryGuard",
    "MemoryIntegrityResult",
    "MemoryPoisoningAlert",
    # Hierarchy
    "AgentHierarchy",
    "HierarchyDefinition",
    # Interop
    "TrustInterop",
    # Observability
    "TrustObserver",
]
