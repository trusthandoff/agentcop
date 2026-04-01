"""Tests for the four built-in violation detectors and DEFAULT_DETECTORS."""

import pytest

from agentcop import SentinelEvent
from agentcop import (
    DEFAULT_DETECTORS,
    detect_ai_generated_payload,
    detect_overlap_window,
    detect_rejected_packet,
    detect_stale_capability,
)


def make_event(event_type, attributes=None, **kwargs):
    return SentinelEvent(
        event_id=kwargs.pop("event_id", "evt-001"),
        event_type=event_type,
        timestamp="2026-01-01T00:00:00Z",
        severity=kwargs.pop("severity", "ERROR"),
        body=kwargs.pop("body", "test body"),
        source_system=kwargs.pop("source_system", "test-system"),
        attributes=attributes or {},
        **kwargs,
    )


_OTHER_TYPES = ("capability_stale", "token_overlap_used", "ai_generated_payload", "unrelated")
_PACKET_OTHERS = ("packet_rejected", "token_overlap_used", "ai_generated_payload", "unrelated")
_OVERLAP_OTHERS = ("packet_rejected", "capability_stale", "ai_generated_payload", "unrelated")
_AI_OTHERS = ("packet_rejected", "capability_stale", "token_overlap_used", "unrelated")


class TestDetectRejectedPacket:
    def test_fires_on_packet_rejected(self):
        e = make_event("packet_rejected", {"packet_id": "pkt-1", "reason": "ttl_expired"})
        v = detect_rejected_packet(e)
        assert v is not None

    def test_violation_type(self):
        v = detect_rejected_packet(make_event("packet_rejected"))
        assert v.violation_type == "rejected_packet"

    def test_severity_is_error(self):
        v = detect_rejected_packet(make_event("packet_rejected"))
        assert v.severity == "ERROR"

    def test_source_event_id_linked(self):
        e = make_event("packet_rejected", event_id="evt-xyz")
        assert detect_rejected_packet(e).source_event_id == "evt-xyz"

    def test_trace_id_propagated(self):
        e = make_event("packet_rejected", trace_id="trace-abc")
        assert detect_rejected_packet(e).trace_id == "trace-abc"

    def test_detail_contains_packet_id_and_reason(self):
        e = make_event("packet_rejected", {"packet_id": "pkt-9", "reason": "auth_fail"})
        v = detect_rejected_packet(e)
        assert v.detail["packet_id"] == "pkt-9"
        assert v.detail["reason"] == "auth_fail"

    def test_detail_is_none_when_attributes_missing(self):
        v = detect_rejected_packet(make_event("packet_rejected"))
        assert v.detail["packet_id"] is None
        assert v.detail["reason"] is None

    def test_returns_none_for_other_event_types(self):
        for et in _OTHER_TYPES:
            assert detect_rejected_packet(make_event(et, severity="WARN")) is None


class TestDetectStaleCapability:
    def test_fires_on_capability_stale(self):
        e = make_event("capability_stale", {"capability_id": "cap-1", "reason": "expired"})
        v = detect_stale_capability(e)
        assert v is not None

    def test_violation_type(self):
        v = detect_stale_capability(make_event("capability_stale"))
        assert v.violation_type == "stale_capability"

    def test_severity_is_error(self):
        v = detect_stale_capability(make_event("capability_stale"))
        assert v.severity == "ERROR"

    def test_source_event_id_linked(self):
        e = make_event("capability_stale", event_id="evt-cap")
        assert detect_stale_capability(e).source_event_id == "evt-cap"

    def test_trace_id_propagated(self):
        e = make_event("capability_stale", trace_id="trace-cap")
        assert detect_stale_capability(e).trace_id == "trace-cap"

    def test_detail_contains_capability_id_and_reason(self):
        e = make_event("capability_stale", {"capability_id": "cap-42", "reason": "revoked"})
        v = detect_stale_capability(e)
        assert v.detail["capability_id"] == "cap-42"
        assert v.detail["reason"] == "revoked"

    def test_detail_is_none_when_attributes_missing(self):
        v = detect_stale_capability(make_event("capability_stale"))
        assert v.detail["capability_id"] is None
        assert v.detail["reason"] is None

    def test_returns_none_for_other_event_types(self):
        for et in _PACKET_OTHERS:
            sev = "WARN" if et in ("token_overlap_used", "ai_generated_payload") else "ERROR"
            assert detect_stale_capability(make_event(et, severity=sev)) is None


class TestDetectOverlapWindow:
    def test_fires_on_token_overlap_used(self):
        e = make_event("token_overlap_used", {"packet_id": "pkt-2", "reason": "replay"}, severity="WARN")
        v = detect_overlap_window(e)
        assert v is not None

    def test_violation_type(self):
        v = detect_overlap_window(make_event("token_overlap_used", severity="WARN"))
        assert v.violation_type == "overlap_window_used"

    def test_severity_is_warn(self):
        v = detect_overlap_window(make_event("token_overlap_used", severity="WARN"))
        assert v.severity == "WARN"

    def test_source_event_id_linked(self):
        e = make_event("token_overlap_used", event_id="evt-olap", severity="WARN")
        assert detect_overlap_window(e).source_event_id == "evt-olap"

    def test_trace_id_propagated(self):
        e = make_event("token_overlap_used", trace_id="trace-111", severity="WARN")
        assert detect_overlap_window(e).trace_id == "trace-111"

    def test_detail_contains_packet_id_and_reason(self):
        e = make_event("token_overlap_used", {"packet_id": "pkt-ov", "reason": "window"}, severity="WARN")
        v = detect_overlap_window(e)
        assert v.detail["packet_id"] == "pkt-ov"
        assert v.detail["reason"] == "window"

    def test_detail_is_none_when_attributes_missing(self):
        v = detect_overlap_window(make_event("token_overlap_used", severity="WARN"))
        assert v.detail["packet_id"] is None
        assert v.detail["reason"] is None

    def test_returns_none_for_other_event_types(self):
        for et in _OVERLAP_OTHERS:
            sev = "WARN" if et == "ai_generated_payload" else "ERROR"
            assert detect_overlap_window(make_event(et, severity=sev)) is None


class TestDetectAiGeneratedPayload:
    def test_fires_on_ai_generated_payload(self):
        e = make_event(
            "ai_generated_payload",
            {"packet_id": "pkt-3", "source": "llm", "model": "gpt-4"},
            severity="WARN",
        )
        v = detect_ai_generated_payload(e)
        assert v is not None

    def test_violation_type(self):
        v = detect_ai_generated_payload(make_event("ai_generated_payload", severity="WARN"))
        assert v.violation_type == "ai_generated_payload"

    def test_severity_is_warn(self):
        v = detect_ai_generated_payload(make_event("ai_generated_payload", severity="WARN"))
        assert v.severity == "WARN"

    def test_source_event_id_linked(self):
        e = make_event("ai_generated_payload", event_id="evt-ai", severity="WARN")
        assert detect_ai_generated_payload(e).source_event_id == "evt-ai"

    def test_trace_id_propagated(self):
        e = make_event("ai_generated_payload", trace_id="trace-222", severity="WARN")
        assert detect_ai_generated_payload(e).trace_id == "trace-222"

    def test_detail_contains_packet_source_model(self):
        attrs = {"packet_id": "pkt-ai", "source": "openai", "model": "gpt-4o"}
        e = make_event("ai_generated_payload", attrs, severity="WARN")
        v = detect_ai_generated_payload(e)
        assert v.detail["packet_id"] == "pkt-ai"
        assert v.detail["source"] == "openai"
        assert v.detail["model"] == "gpt-4o"

    def test_detail_is_none_when_attributes_missing(self):
        v = detect_ai_generated_payload(make_event("ai_generated_payload", severity="WARN"))
        assert v.detail["packet_id"] is None
        assert v.detail["source"] is None
        assert v.detail["model"] is None

    def test_returns_none_for_other_event_types(self):
        for et in _AI_OTHERS:
            assert detect_ai_generated_payload(make_event(et)) is None


class TestDefaultDetectors:
    def test_contains_all_four_detectors(self):
        assert detect_rejected_packet in DEFAULT_DETECTORS
        assert detect_stale_capability in DEFAULT_DETECTORS
        assert detect_overlap_window in DEFAULT_DETECTORS
        assert detect_ai_generated_payload in DEFAULT_DETECTORS

    def test_exactly_four_detectors(self):
        assert len(DEFAULT_DETECTORS) == 4

    def test_default_detectors_is_a_list(self):
        assert isinstance(DEFAULT_DETECTORS, list)
