import threading
from collections.abc import Callable, Iterable

from .event import SentinelEvent, ViolationRecord
from .violations import DEFAULT_DETECTORS

ViolationDetector = Callable[[SentinelEvent], ViolationRecord | None]


class Sentinel:
    """
    Universal forensic auditor.

    Ingests SentinelEvents, runs violation detectors, returns typed ViolationRecords.

    Usage::

        sentinel = Sentinel()
        sentinel.ingest(adapter.to_sentinel_event(e) for e in raw_events)
        violations = sentinel.detect_violations()

    Custom detectors::

        def my_detector(event: SentinelEvent) -> ViolationRecord | None:
            if event.event_type == "custom_alert":
                return ViolationRecord(
                    violation_type="custom_alert",
                    severity="WARN",
                    source_event_id=event.event_id,
                    trace_id=event.trace_id,
                    detail={"msg": event.body},
                )

        sentinel = Sentinel()
        sentinel.register_detector(my_detector)
    """

    def __init__(self, detectors: list[ViolationDetector] | None = None):
        self._lock = threading.Lock()
        self._events: list[SentinelEvent] = []
        self._detectors: list[ViolationDetector] = (
            list(detectors) if detectors is not None else list(DEFAULT_DETECTORS)
        )

    def register_detector(self, fn: ViolationDetector) -> None:
        """Append a custom detector. Runs after all built-in detectors."""
        with self._lock:
            self._detectors.append(fn)

    def ingest(self, events: Iterable[SentinelEvent]) -> None:
        """Replace the internal event buffer with the provided events."""
        ingested = list(events)
        with self._lock:
            self._events = ingested

    def detect_violations(self) -> list[ViolationRecord]:
        with self._lock:
            events = list(self._events)
            detectors = list(self._detectors)

        violations: list[ViolationRecord] = []
        for event in events:
            for detector in detectors:
                result = detector(event)
                if result is not None:
                    violations.append(result)
        return violations

    def report(self) -> None:
        violations = self.detect_violations()
        if not violations:
            print("No violations detected")
            return
        print("=== SENTINEL REPORT ===")
        for v in violations:
            print(
                f"[{v.severity}] {v.violation_type}"
                + (f" trace={v.trace_id}" if v.trace_id else "")
                + (f" {v.detail}" if v.detail else "")
            )
