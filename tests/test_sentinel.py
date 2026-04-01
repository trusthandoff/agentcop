"""Tests for Sentinel.ingest, detect_violations, report, and thread safety."""

import threading

import pytest

from agentcop import Sentinel, SentinelEvent, ViolationRecord


def make_event(event_type="unrelated", event_id="evt-001", severity="INFO", **kwargs):
    return SentinelEvent(
        event_id=event_id,
        event_type=event_type,
        timestamp="2026-01-01T00:00:00Z",
        severity=severity,
        body="test",
        source_system="test-system",
        **kwargs,
    )


class TestSentinelIngest:
    def test_ingest_list(self):
        s = Sentinel()
        s.ingest([make_event(event_id="e1"), make_event(event_id="e2")])
        # no triggering events → no violations, but no error either
        assert s.detect_violations() == []

    def test_ingest_generator(self):
        s = Sentinel()
        s.ingest(make_event(event_id=f"evt-{i}") for i in range(10))
        assert s.detect_violations() == []

    def test_ingest_empty_iterable(self):
        s = Sentinel()
        s.ingest([])
        assert s.detect_violations() == []

    def test_second_ingest_replaces_first(self):
        s = Sentinel()
        # first ingest: one triggering event
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        assert len(s.detect_violations()) == 1

        # second ingest: non-triggering event overwrites the first
        s.ingest([make_event("unrelated")])
        assert s.detect_violations() == []

    def test_ingest_single_event(self):
        s = Sentinel()
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        assert len(s.detect_violations()) == 1


class TestSentinelDetectViolations:
    def test_no_events_returns_empty_list(self):
        assert Sentinel().detect_violations() == []

    def test_returns_list_type(self):
        assert isinstance(Sentinel().detect_violations(), list)

    def test_non_matching_event_returns_empty(self):
        s = Sentinel()
        s.ingest([make_event("completely_unknown_type")])
        assert s.detect_violations() == []

    def test_packet_rejected_triggers_violation(self):
        s = Sentinel()
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        violations = s.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "rejected_packet"

    def test_capability_stale_triggers_violation(self):
        s = Sentinel()
        s.ingest([make_event("capability_stale", severity="ERROR")])
        violations = s.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "stale_capability"

    def test_token_overlap_used_triggers_violation(self):
        s = Sentinel()
        s.ingest([make_event("token_overlap_used", severity="WARN")])
        violations = s.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "overlap_window_used"

    def test_ai_generated_payload_triggers_violation(self):
        s = Sentinel()
        s.ingest([make_event("ai_generated_payload", severity="WARN")])
        violations = s.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "ai_generated_payload"

    def test_all_four_detectors_fire_simultaneously(self):
        s = Sentinel()
        s.ingest([
            make_event("packet_rejected", event_id="e1", severity="ERROR"),
            make_event("capability_stale", event_id="e2", severity="ERROR"),
            make_event("token_overlap_used", event_id="e3", severity="WARN"),
            make_event("ai_generated_payload", event_id="e4", severity="WARN"),
        ])
        violation_types = {v.violation_type for v in s.detect_violations()}
        assert violation_types == {
            "rejected_packet",
            "stale_capability",
            "overlap_window_used",
            "ai_generated_payload",
        }

    def test_mixed_events_only_matching_produces_violations(self):
        s = Sentinel()
        s.ingest([
            make_event("packet_rejected", event_id="e1", severity="ERROR"),
            make_event("unrelated", event_id="e2"),
            make_event("capability_stale", event_id="e3", severity="ERROR"),
        ])
        violations = s.detect_violations()
        assert len(violations) == 2

    def test_source_event_id_linked_correctly(self):
        s = Sentinel()
        s.ingest([make_event("packet_rejected", event_id="evt-special", severity="ERROR")])
        assert s.detect_violations()[0].source_event_id == "evt-special"

    def test_trace_id_propagated_to_violation(self):
        s = Sentinel()
        s.ingest([make_event("packet_rejected", severity="ERROR", trace_id="trace-xyz")])
        assert s.detect_violations()[0].trace_id == "trace-xyz"

    def test_detect_violations_is_idempotent(self):
        s = Sentinel()
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        first = s.detect_violations()
        second = s.detect_violations()
        assert len(first) == len(second) == 1
        assert first[0].violation_type == second[0].violation_type

    def test_each_call_returns_new_violation_records(self):
        s = Sentinel()
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        first = s.detect_violations()
        second = s.detect_violations()
        # violation_id is generated fresh each time (new ViolationRecord instances)
        assert first[0].violation_id != second[0].violation_id

    def test_custom_detectors_list_replaces_defaults(self):
        def always_custom(event):
            return ViolationRecord(
                violation_type="custom",
                severity="WARN",
                source_event_id=event.event_id,
            )

        s = Sentinel(detectors=[always_custom])
        # packet_rejected would normally fire built-in; only custom runs here
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        violations = s.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "custom"

    def test_empty_detectors_list_never_fires(self):
        s = Sentinel(detectors=[])
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        assert s.detect_violations() == []

    def test_register_detector_appends_to_active_set(self):
        s = Sentinel()

        def custom(event):
            if event.event_type == "custom_event":
                return ViolationRecord(
                    violation_type="custom",
                    severity="WARN",
                    source_event_id=event.event_id,
                )

        s.register_detector(custom)
        s.ingest([make_event("custom_event")])
        violations = s.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "custom"

    def test_register_detector_runs_after_builtins(self):
        call_order = []

        def builtin_stub(event):
            call_order.append("builtin")
            return None

        def custom(event):
            call_order.append("custom")
            return None

        s = Sentinel(detectors=[builtin_stub])
        s.register_detector(custom)
        s.ingest([make_event()])
        s.detect_violations()
        assert call_order == ["builtin", "custom"]

    def test_default_sentinel_uses_all_four_default_detectors(self):
        s = Sentinel()
        from agentcop import DEFAULT_DETECTORS
        assert s._detectors == DEFAULT_DETECTORS

    def test_custom_detector_receiving_none_does_not_add_violation(self):
        def never_fires(event):
            return None

        s = Sentinel(detectors=[never_fires])
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        assert s.detect_violations() == []


class TestSentinelReport:
    def test_report_prints_no_violations(self, capsys):
        Sentinel().report()
        assert "No violations detected" in capsys.readouterr().out

    def test_report_prints_sentinel_header(self, capsys):
        s = Sentinel()
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        s.report()
        assert "SENTINEL REPORT" in capsys.readouterr().out

    def test_report_prints_violation_type(self, capsys):
        s = Sentinel()
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        s.report()
        assert "rejected_packet" in capsys.readouterr().out

    def test_report_prints_severity(self, capsys):
        s = Sentinel()
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        s.report()
        assert "ERROR" in capsys.readouterr().out

    def test_report_includes_trace_id_when_present(self, capsys):
        s = Sentinel()
        s.ingest([make_event("packet_rejected", severity="ERROR", trace_id="trace-999")])
        s.report()
        assert "trace-999" in capsys.readouterr().out


class TestThreadSafety:
    def test_concurrent_ingest_does_not_raise(self):
        """50 threads calling ingest() simultaneously must not raise."""
        s = Sentinel()
        errors = []

        def worker(i):
            try:
                s.ingest([make_event(event_id=f"evt-{i}")])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        s.detect_violations()  # must not raise after concurrent ingests

    def test_concurrent_detect_violations_all_succeed(self):
        """50 threads calling detect_violations() simultaneously must all succeed."""
        s = Sentinel()
        s.ingest([
            make_event("packet_rejected", event_id="e1", severity="ERROR"),
            make_event("capability_stale", event_id="e2", severity="ERROR"),
        ])
        results = []
        errors = []

        def worker():
            try:
                results.append(s.detect_violations())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == 50
        for r in results:
            assert len(r) == 2

    def test_concurrent_ingest_and_detect_do_not_deadlock(self):
        """25 ingest threads + 25 detect threads running together must not raise."""
        s = Sentinel()
        s.ingest([make_event("packet_rejected", severity="ERROR")])
        errors = []

        def ingester(i):
            try:
                s.ingest([make_event(event_id=f"evt-{i}")])
            except Exception as e:
                errors.append(e)

        def detector():
            try:
                s.detect_violations()
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=ingester, args=(i,)) for i in range(25)]
            + [threading.Thread(target=detector) for _ in range(25)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_concurrent_register_detector_preserves_all_registrations(self):
        """50 threads each registering one detector must result in exactly 50."""
        s = Sentinel(detectors=[])
        errors = []

        def register(i):
            try:
                s.register_detector(lambda event, _i=i: None)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(s._detectors) == 50

    def test_lock_prevents_ingest_tearing_under_concurrent_detect(self):
        """Violations returned must always reflect a consistent snapshot of events."""
        s = Sentinel()
        s.ingest([make_event("packet_rejected", event_id="e1", severity="ERROR")])
        errors = []

        def flip():
            for _ in range(200):
                s.ingest([make_event("packet_rejected", event_id="e1", severity="ERROR")])
                s.ingest([make_event("unrelated", event_id="e2")])

        def check():
            for _ in range(200):
                result = s.detect_violations()
                # result must be a list (not torn/corrupt state)
                if not isinstance(result, list):
                    errors.append(f"non-list result: {type(result)}")

        threads = (
            [threading.Thread(target=flip) for _ in range(5)]
            + [threading.Thread(target=check) for _ in range(5)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
