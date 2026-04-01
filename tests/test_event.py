"""Tests for SentinelEvent and ViolationRecord."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agentcop import SentinelEvent, ViolationRecord


def make_event(**kwargs):
    defaults = dict(
        event_id="evt-001",
        event_type="test_event",
        timestamp="2026-01-01T00:00:00Z",
        severity="INFO",
        body="test body",
        source_system="test-system",
    )
    defaults.update(kwargs)
    return SentinelEvent(**defaults)


class TestSentinelEvent:
    def test_minimal_construction(self):
        e = make_event()
        assert e.event_id == "evt-001"
        assert e.event_type == "test_event"
        assert e.severity == "INFO"
        assert e.body == "test body"
        assert e.source_system == "test-system"

    def test_observed_at_auto_populated(self):
        e = make_event()
        assert e.observed_at is not None
        assert e.observed_at.tzinfo is not None

    def test_optional_fields_default_to_none(self):
        e = make_event()
        assert e.producer_id is None
        assert e.trace_id is None
        assert e.span_id is None

    def test_attributes_default_to_empty_dict(self):
        e = make_event()
        assert e.attributes == {}

    def test_all_severity_levels_accepted(self):
        for sev in ("INFO", "WARN", "ERROR", "CRITICAL"):
            e = make_event(severity=sev)
            assert e.severity == sev

    def test_invalid_severity_rejected(self):
        with pytest.raises(ValidationError):
            make_event(severity="DEBUG")

    def test_timestamp_parsed_from_iso_string(self):
        e = make_event(timestamp="2026-03-31T12:00:00Z")
        assert isinstance(e.timestamp, datetime)

    def test_timestamp_accepts_datetime_object(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        e = make_event(timestamp=dt)
        assert e.timestamp == dt

    def test_optional_fields_round_trip(self):
        e = make_event(
            producer_id="agent-1",
            trace_id="abc123",
            span_id="def456",
            attributes={"key": "value", "count": 42},
        )
        assert e.producer_id == "agent-1"
        assert e.trace_id == "abc123"
        assert e.span_id == "def456"
        assert e.attributes == {"key": "value", "count": 42}

    def test_missing_event_id_raises(self):
        with pytest.raises(ValidationError):
            SentinelEvent(
                event_type="x",
                timestamp="2026-01-01T00:00:00Z",
                severity="INFO",
                body="b",
                source_system="s",
            )

    def test_missing_body_raises(self):
        with pytest.raises(ValidationError):
            SentinelEvent(
                event_id="e1",
                event_type="x",
                timestamp="2026-01-01T00:00:00Z",
                severity="INFO",
                source_system="s",
            )

    def test_missing_source_system_raises(self):
        with pytest.raises(ValidationError):
            SentinelEvent(
                event_id="e1",
                event_type="x",
                timestamp="2026-01-01T00:00:00Z",
                severity="INFO",
                body="b",
            )

    def test_attributes_can_hold_nested_values(self):
        e = make_event(attributes={"nested": {"a": 1}, "list": [1, 2, 3]})
        assert e.attributes["nested"] == {"a": 1}
        assert e.attributes["list"] == [1, 2, 3]


class TestViolationRecord:
    def _make(self, **kwargs):
        defaults = dict(violation_type="test_violation", severity="ERROR", source_event_id="evt-001")
        defaults.update(kwargs)
        return ViolationRecord(**defaults)

    def test_violation_id_auto_generated(self):
        v = self._make()
        assert v.violation_id
        assert len(v.violation_id) == 36  # UUID4 format

    def test_violation_ids_are_unique(self):
        ids = {self._make().violation_id for _ in range(100)}
        assert len(ids) == 100

    def test_detected_at_auto_populated(self):
        v = self._make()
        assert v.detected_at is not None
        assert isinstance(v.detected_at, datetime)

    def test_trace_id_defaults_to_none(self):
        assert self._make().trace_id is None

    def test_detail_defaults_to_empty_dict(self):
        assert self._make().detail == {}

    def test_info_severity_not_allowed(self):
        with pytest.raises(ValidationError):
            self._make(severity="INFO")

    def test_all_valid_severities(self):
        for sev in ("WARN", "ERROR", "CRITICAL"):
            v = self._make(severity=sev)
            assert v.severity == sev

    def test_explicit_trace_id_and_detail(self):
        v = self._make(trace_id="trace-xyz", detail={"reason": "bad", "code": 42})
        assert v.trace_id == "trace-xyz"
        assert v.detail == {"reason": "bad", "code": 42}

    def test_source_event_id_stored(self):
        v = self._make(source_event_id="evt-special")
        assert v.source_event_id == "evt-special"

    def test_missing_source_event_id_raises(self):
        with pytest.raises(ValidationError):
            ViolationRecord(violation_type="t", severity="WARN")
