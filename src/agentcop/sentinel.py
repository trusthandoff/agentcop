import threading
from collections.abc import Callable, Iterable

from .event import SentinelEvent, ViolationRecord
from .violations import DEFAULT_DETECTORS

ViolationDetector = Callable[[SentinelEvent], ViolationRecord | None]


class WatchHandle:
    """
    Returned by :meth:`Sentinel.watch`. Stops the background monitoring thread.

    Use as a context manager or call :meth:`stop` explicitly::

        # context manager — stops automatically on exit
        with sentinel.watch(on_violation):
            sentinel.push(event)

        # explicit stop
        handle = sentinel.watch(on_violation)
        ...
        handle.stop()
    """

    def __init__(self, thread: threading.Thread, stop_event: threading.Event) -> None:
        self._thread = thread
        self._stop_event = stop_event

    def stop(self) -> None:
        """Signal the watch loop to stop and block until the thread finishes."""
        self._stop_event.set()
        self._thread.join()

    def __enter__(self) -> "WatchHandle":
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


class Sentinel:
    """
    Universal forensic auditor.

    Ingests SentinelEvents, runs violation detectors, returns typed ViolationRecords.

    Batch mode::

        sentinel = Sentinel()
        sentinel.ingest(adapter.to_sentinel_event(e) for e in raw_events)
        violations = sentinel.detect_violations()

    Continuous monitoring mode::

        def alert(v: ViolationRecord) -> None:
            print(f"[{v.severity}] {v.violation_type}")

        with sentinel.watch(alert, poll_interval=0.05):
            for raw in pipeline:
                sentinel.push(adapter.to_sentinel_event(raw))

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

    def push(self, event: SentinelEvent) -> None:
        """Append a single event to the buffer.

        Preferred over :meth:`ingest` when using :meth:`watch`, because it
        accumulates events rather than replacing the buffer.
        """
        with self._lock:
            self._events.append(event)

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

    def watch(
        self,
        on_violation: Callable[[ViolationRecord], None],
        *,
        poll_interval: float = 0.1,
    ) -> WatchHandle:
        """Start continuous monitoring in a background thread.

        On each poll cycle the watch loop snapshots the current event buffer,
        processes any events not yet seen, and calls *on_violation* for each
        :class:`ViolationRecord` found.  The loop runs until the returned
        :class:`WatchHandle` is stopped.

        Use :meth:`push` to feed events during monitoring.  :meth:`ingest` may
        also be used (e.g. to replace the buffer with a fresh batch); the watch
        loop detects the replacement and re-scans from the beginning of the new
        buffer so no events are silently skipped.

        Args:
            on_violation: Callback invoked for every detected violation.
                          Called from the background thread — make it thread-safe.
            poll_interval: Seconds between buffer scans (default 0.1).

        Returns:
            A :class:`WatchHandle` whose :meth:`~WatchHandle.stop` method
            (or context-manager ``__exit__``) halts the loop.
        """
        stop_event = threading.Event()

        def _loop() -> None:
            watermark = 0
            while not stop_event.is_set():
                with self._lock:
                    snapshot = list(self._events)
                    detectors = list(self._detectors)

                # ingest() replaced the buffer with fewer events — reset
                if len(snapshot) < watermark:
                    watermark = 0

                for event in snapshot[watermark:]:
                    for detector in detectors:
                        result = detector(event)
                        if result is not None:
                            on_violation(result)

                watermark = len(snapshot)
                stop_event.wait(poll_interval)

        thread = threading.Thread(target=_loop, daemon=True, name="agentcop-watch")
        thread.start()
        return WatchHandle(thread, stop_event)

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
