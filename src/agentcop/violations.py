"""
Built-in violation detectors for sentinel-core.

Each detector is a plain function:
    (SentinelEvent) -> ViolationRecord | None

Return a ViolationRecord if the event represents a violation, None otherwise.
Register custom detectors via Sentinel.register_detector().
"""

from .event import SentinelEvent, ViolationRecord


def detect_rejected_packet(event: SentinelEvent) -> ViolationRecord | None:
    if event.event_type != "packet_rejected":
        return None
    return ViolationRecord(
        violation_type="rejected_packet",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "packet_id": event.attributes.get("packet_id"),
            "reason": event.attributes.get("reason"),
        },
    )


def detect_stale_capability(event: SentinelEvent) -> ViolationRecord | None:
    if event.event_type != "capability_stale":
        return None
    return ViolationRecord(
        violation_type="stale_capability",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "capability_id": event.attributes.get("capability_id"),
            "reason": event.attributes.get("reason"),
        },
    )


def detect_overlap_window(event: SentinelEvent) -> ViolationRecord | None:
    if event.event_type != "token_overlap_used":
        return None
    return ViolationRecord(
        violation_type="overlap_window_used",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "packet_id": event.attributes.get("packet_id"),
            "reason": event.attributes.get("reason"),
        },
    )


def detect_ai_generated_payload(event: SentinelEvent) -> ViolationRecord | None:
    if event.event_type != "ai_generated_payload":
        return None
    return ViolationRecord(
        violation_type="ai_generated_payload",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "packet_id": event.attributes.get("packet_id"),
            "source": event.attributes.get("source"),
            "model": event.attributes.get("model"),
        },
    )


DEFAULT_DETECTORS = [
    detect_rejected_packet,
    detect_stale_capability,
    detect_overlap_window,
    detect_ai_generated_payload,
]
