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

__version__ = "0.4.2"

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
]
