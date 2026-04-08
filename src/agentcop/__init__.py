from .adapters import SentinelAdapter, validate_adapter
from .event import SentinelEvent, ViolationRecord
from .identity import (
    AgentIdentity,
    BehavioralBaseline,
    DriftConfig,
    IdentityStore,
    InMemoryIdentityStore,
    SQLiteIdentityStore,
)
from .sentinel import Sentinel, ViolationDetector, WatchHandle
from .violations import (
    DEFAULT_DETECTORS,
    detect_ai_generated_payload,
    detect_overlap_window,
    detect_rejected_packet,
    detect_stale_capability,
)

# Reliability — optional submodule; imported lazily to avoid breaking installs
# that don't use the reliability features.  Users can import directly:
#   from agentcop.reliability import ReliabilityTracer, ReliabilityStore, ...
# Or use the convenience re-exports below (no extra deps required):


def _lazy_reliability() -> None:
    """Internal: trigger an informative error when reliability is not importable."""
    pass  # reliability has no optional deps; this is just a namespace guard


# Convenience re-exports of the four most commonly used reliability symbols.
# These are imported lazily so that `import agentcop` never fails even in
# environments where the reliability submodule hasn't been initialised yet.
try:
    from .reliability.instrumentation import ReliabilityTracer, wrap_for_reliability
    from .reliability.models import ReliabilityReport
    from .reliability.store import ReliabilityStore
    _reliability_available = True
except ImportError:  # pragma: no cover
    _reliability_available = False

__version__ = "0.4.10"

__all__ = [
    # Core schema
    "SentinelEvent",
    "ViolationRecord",
    # Auditor
    "Sentinel",
    "ViolationDetector",
    "WatchHandle",
    # Built-in detectors
    "DEFAULT_DETECTORS",
    "detect_rejected_packet",
    "detect_stale_capability",
    "detect_overlap_window",
    "detect_ai_generated_payload",
    # Adapter protocol
    "SentinelAdapter",
    "validate_adapter",
    # Agent identity
    "AgentIdentity",
    "BehavioralBaseline",
    "DriftConfig",
    "IdentityStore",
    "InMemoryIdentityStore",
    "SQLiteIdentityStore",
    # Reliability (convenience re-exports; full API: agentcop.reliability)
    "ReliabilityTracer",
    "ReliabilityStore",
    "ReliabilityReport",
    "wrap_for_reliability",
    # Badge — imported from agentcop.badge (requires agentcop[badge])
    # AgentBadge, BadgeIssuer, BadgeStore, InMemoryBadgeStore, SQLiteBadgeStore,
    # generate_svg, generate_badge_card, generate_markdown, tier_from_score
]
