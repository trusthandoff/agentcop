"""Tests for the SentinelAdapter protocol."""

from agentcop import SentinelAdapter, SentinelEvent


def _make_event():
    return SentinelEvent(
        event_id="evt-001",
        event_type="test",
        timestamp="2026-01-01T00:00:00Z",
        severity="INFO",
        body="test",
        source_system="test-system",
    )


class TestSentinelAdapterProtocol:
    def test_complete_implementation_satisfies_protocol(self):
        class GoodAdapter:
            source_system = "good-system"

            def to_sentinel_event(self, raw: dict) -> SentinelEvent:
                return _make_event()

        assert isinstance(GoodAdapter(), SentinelAdapter)

    def test_missing_to_sentinel_event_fails_protocol(self):
        class NoMethod:
            source_system = "x"

        assert not isinstance(NoMethod(), SentinelAdapter)

    def test_empty_class_fails_protocol(self):
        class Empty:
            pass

        assert not isinstance(Empty(), SentinelAdapter)

    def test_adapter_produces_correct_event(self):
        class MyAdapter:
            source_system = "my-system"

            def to_sentinel_event(self, raw: dict) -> SentinelEvent:
                return SentinelEvent(
                    event_id=raw["id"],
                    event_type=raw["type"],
                    timestamp="2026-01-01T00:00:00Z",
                    severity="INFO",
                    body=raw.get("message", ""),
                    source_system=self.source_system,
                )

        adapter = MyAdapter()
        event = adapter.to_sentinel_event(
            {"id": "x1", "type": "tool_call", "message": "called shell"}
        )
        assert event.event_id == "x1"
        assert event.event_type == "tool_call"
        assert event.body == "called shell"
        assert event.source_system == "my-system"

    def test_adapter_source_system_accessible(self):
        class MyAdapter:
            source_system = "ingestion-bus"

            def to_sentinel_event(self, raw: dict) -> SentinelEvent:
                return _make_event()

        assert MyAdapter().source_system == "ingestion-bus"

    def test_adapter_integrates_with_sentinel(self):
        """An adapter feeding events into Sentinel produces correct violations."""
        from agentcop import Sentinel

        class RejectionAdapter:
            source_system = "firewall"

            def to_sentinel_event(self, raw: dict) -> SentinelEvent:
                return SentinelEvent(
                    event_id=raw["id"],
                    event_type="packet_rejected",
                    timestamp="2026-01-01T00:00:00Z",
                    severity="ERROR",
                    body=raw.get("reason", ""),
                    source_system=self.source_system,
                    attributes={"packet_id": raw["id"], "reason": raw.get("reason")},
                )

        adapter = RejectionAdapter()
        raw_events = [
            {"id": "pkt-1", "reason": "ttl_expired"},
            {"id": "pkt-2", "reason": "auth_fail"},
        ]

        sentinel = Sentinel()
        sentinel.ingest(adapter.to_sentinel_event(e) for e in raw_events)
        violations = sentinel.detect_violations()

        assert len(violations) == 2
        assert all(v.violation_type == "rejected_packet" for v in violations)
        assert {v.source_event_id for v in violations} == {"pkt-1", "pkt-2"}
