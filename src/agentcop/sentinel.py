import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal

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

    With agent identity::

        identity = AgentIdentity.register(agent_id="my-agent", code=fn)
        sentinel.attach_identity(identity)

        with sentinel.watch(identity.observe_violation):
            sentinel.push(event)  # auto-enriched with identity metadata

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

    def __init__(
        self,
        detectors: list[ViolationDetector] | None = None,
        *,
        max_buffer_size: int | None = None,
        buffer_full: Literal["drop", "block"] = "drop",
    ):
        self._lock = threading.Lock()
        # _not_full shares _lock as its underlying mutex so callers that hold
        # _lock can also wait/notify without an extra acquisition.
        self._not_full = threading.Condition(self._lock)
        self._events: list[SentinelEvent] = []
        self._detectors: list[ViolationDetector] = (
            list(detectors) if detectors is not None else list(DEFAULT_DETECTORS)
        )
        # Violation hooks: called after each violation is detected.
        # Each hook receives the ViolationRecord and may return additional
        # violations (e.g. an agent_flagged record from AgentIdentity).
        self._violation_hooks: list[Callable[[ViolationRecord], list[ViolationRecord]]] = []
        self._identity: object | None = None  # AgentIdentity, avoid circular import
        self._max_buffer_size = max_buffer_size
        self._buffer_full: Literal["drop", "block"] = buffer_full

    def register_detector(self, fn: ViolationDetector) -> None:
        """Append a custom detector. Runs after all built-in detectors."""
        with self._lock:
            self._detectors.append(fn)

    def attach_identity(self, identity: object) -> None:
        """Attach an :class:`~agentcop.AgentIdentity` for automatic event enrichment
        and drift monitoring.

        After attaching:

        - Events pushed via :meth:`push` are enriched with identity attributes
          (``agent_id``, ``trust_score``, ``fingerprint``, ``identity_status``).
        - A drift detector is automatically registered on this Sentinel.
        - In :meth:`watch` mode, violations returned by the watch callback are
          also forwarded to the identity's :meth:`~AgentIdentity.observe_violation`
          hook so the trust score stays current.  Pass ``identity.observe_violation``
          as the *on_violation* callback::

              with sentinel.watch(identity.observe_violation):
                  sentinel.push(event)
        """
        with self._lock:
            self._identity = identity
            self._violation_hooks.append(identity.observe_violation)  # type: ignore[union-attr]
        self.register_detector(identity.make_drift_detector())  # type: ignore[union-attr]

    def ingest(self, events: Iterable[SentinelEvent]) -> None:
        """Replace the internal event buffer with the provided events."""
        ingested = list(events)
        with self._not_full:
            self._events = ingested
            # Wake any push() calls blocked on a full buffer — the buffer was
            # just replaced so there is likely space again.
            self._not_full.notify_all()

    def push(self, event: SentinelEvent) -> None:
        """Append a single event to the buffer.

        Preferred over :meth:`ingest` when using :meth:`watch`, because it
        accumulates events rather than replacing the buffer.

        If an :class:`~agentcop.AgentIdentity` is attached via
        :meth:`attach_identity`, the event is enriched with a snapshot of the
        identity's current ``agent_id``, ``trust_score``, ``fingerprint``, and
        ``identity_status`` attributes before it is stored.
        """
        with self._lock:
            identity = self._identity

        if identity is not None:
            enriched_attrs = {
                **event.attributes,
                **identity.as_event_attributes(),  # type: ignore[union-attr]
            }
            event = event.model_copy(update={"attributes": enriched_attrs})

        with self._not_full:
            if self._max_buffer_size is not None:
                if self._buffer_full == "block":
                    # Block until there is room in the buffer.
                    while len(self._events) >= self._max_buffer_size:
                        self._not_full.wait()
                else:  # "drop"
                    if len(self._events) >= self._max_buffer_size:
                        return  # silently drop the event
            self._events.append(event)

    def detect_violations(self) -> list[ViolationRecord]:
        with self._lock:
            events = list(self._events)
            detectors = list(self._detectors)
            violation_hooks = list(self._violation_hooks)

        violations: list[ViolationRecord] = []
        for event in events:
            for detector in detectors:
                result = detector(event)
                if result is not None:
                    violations.append(result)
                    for hook in violation_hooks:
                        extras = hook(result)
                        violations.extend(extras)
        return violations

    def watch(
        self,
        on_violation: Callable[[ViolationRecord], list[ViolationRecord] | None],
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

        *on_violation* may return a list of additional violations (e.g. when
        using :meth:`AgentIdentity.observe_violation <agentcop.AgentIdentity.observe_violation>`);
        those are also passed to *on_violation* recursively.

        Args:
            on_violation: Callback invoked for every detected violation.
                          Called from the background thread — make it thread-safe.
                          May return additional ViolationRecords (or None/[]).
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
                    violation_hooks = list(self._violation_hooks)

                # ingest() replaced the buffer with fewer events — reset
                if len(snapshot) < watermark:
                    watermark = 0

                for event in snapshot[watermark:]:
                    for detector in detectors:
                        result = detector(event)
                        if result is not None:
                            extras = on_violation(result) or []
                            for extra_v in extras:
                                on_violation(extra_v)
                            for hook in violation_hooks:
                                hook_extras = hook(result)
                                for extra_v in hook_extras:
                                    on_violation(extra_v)

                watermark = len(snapshot)
                stop_event.wait(poll_interval)

        thread = threading.Thread(target=_loop, daemon=True, name="agentcop-watch")
        thread.start()
        return WatchHandle(thread, stop_event)

    def watch_file(
        self,
        path: str | Path,
        on_violation: Callable[[ViolationRecord], list[ViolationRecord] | None],
        *,
        parser: Callable[[str], SentinelEvent | None] | None = None,
    ) -> WatchHandle:
        """Watch a file for new events using inotify (via the ``watchdog`` library).

        Each new line appended to *path* is parsed as a JSON-serialized
        :class:`SentinelEvent` (JSONL format) and fed into :meth:`push`.  Violations
        are delivered to *on_violation* exactly as in :meth:`watch`.

        A custom *parser* function can be provided to handle other line formats; it
        should return a :class:`SentinelEvent` or ``None`` to skip the line.

        Requires ``watchdog>=3.0`` (``pip install agentcop[watchdog]``).

        Args:
            path: File to watch.  Must already exist; new lines are appended to it
                  by an external writer (e.g. an agent logging events to a JSONL file).
            on_violation: Callback for each detected violation (same contract as
                          :meth:`watch`).
            parser: Optional line → SentinelEvent converter.  Defaults to
                    ``SentinelEvent.model_validate_json``.

        Returns:
            A :class:`WatchHandle` that stops the file watcher on :meth:`~WatchHandle.stop`.
        """
        try:
            from watchdog.events import FileModifiedEvent, FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError as exc:
            raise ImportError(
                "watch_file() requires the watchdog library. "
                "Install with: pip install agentcop[watchdog]"
            ) from exc

        _path = Path(path).resolve()
        _parser: Callable[[str], SentinelEvent | None] = parser or (
            lambda line: SentinelEvent.model_validate_json(line)
        )

        # Shared mutable state: position in file (bytes read so far).
        state = {"pos": _path.stat().st_size if _path.exists() else 0}

        def _process_new_lines() -> None:
            try:
                with open(_path, encoding="utf-8") as fh:
                    fh.seek(state["pos"])
                    for raw_line in fh:
                        line = raw_line.rstrip("\n")
                        if not line:
                            continue
                        try:
                            event = _parser(line)
                        except Exception:
                            continue
                        if event is not None:
                            self.push(event)
                    state["pos"] = fh.tell()
            except OSError:
                pass

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
                if Path(event.src_path).resolve() == _path:
                    _process_new_lines()

        observer = Observer()
        observer.schedule(_Handler(), str(_path.parent), recursive=False)
        observer.start()

        # Piggyback on the existing polling watch() loop for violation detection
        # so the on_violation callback is dispatched consistently.
        poll_handle = self.watch(on_violation, poll_interval=0.1)

        # stop_event drives a real monitor thread that tears down both the
        # observer and the poll handle when WatchHandle.stop() is called.
        stop_event = threading.Event()

        def _run_until_stopped() -> None:
            stop_event.wait()
            poll_handle.stop()
            observer.stop()
            observer.join()

        stop_thread = threading.Thread(
            target=_run_until_stopped, daemon=True, name="agentcop-file-watch"
        )
        stop_thread.start()

        return WatchHandle(stop_thread, stop_event)

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
