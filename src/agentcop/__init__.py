from .event import SentinelEvent, ViolationRecord
from .sentinel import Sentinel, ViolationDetector
from .violations import (
    DEFAULT_DETECTORS,
    detect_ai_generated_payload,
    detect_overlap_window,
    detect_rejected_packet,
    detect_stale_capability,
)
from .adapters import SentinelAdapter

__version__ = "0.1.9"

__all__ = [
    # Core schema
    "SentinelEvent",
    "ViolationRecord",
    # Auditor
    "Sentinel",
    "ViolationDetector",
    # Built-in detectors
    "DEFAULT_DETECTORS",
    "detect_rejected_packet",
    "detect_stale_capability",
    "detect_overlap_window",
    "detect_ai_generated_payload",
    # Adapter protocol
    "SentinelAdapter",
]
