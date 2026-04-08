"""Tests for LangGraphSentinelAdapter."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

# Patch _require_langgraph at import time so tests run without langgraph installed.
# Each test that constructs an adapter uses the `adapter` fixture which patches it.


def _make_adapter(thread_id=None):
    """Construct a LangGraphSentinelAdapter with the langgraph guard bypassed."""
    with patch("agentcop.adapters.langgraph._require_langgraph"):
        from agentcop.adapters.langgraph import LangGraphSentinelAdapter

        return LangGraphSentinelAdapter(thread_id=thread_id)


@pytest.fixture()
def adapter():
    return _make_adapter(thread_id="thread-default")


@pytest.fixture()
def adapter_no_thread():
    return _make_adapter(thread_id=None)


# ---------------------------------------------------------------------------
# Guard / import
# ---------------------------------------------------------------------------


class TestRequireLangGraph:
    def test_raises_import_error_when_langgraph_missing(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "langgraph":
                raise ImportError("No module named 'langgraph'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            from agentcop.adapters.langgraph import _require_langgraph

            with pytest.raises(ImportError, match="pip install agentcop\\[langgraph\\]"):
                _require_langgraph()

    def test_does_not_raise_when_langgraph_present(self):
        with patch("builtins.__import__", return_value=MagicMock()):
            from agentcop.adapters.langgraph import _require_langgraph

            _require_langgraph()  # must not raise


class TestAdapterInit:
    def test_constructor_calls_require_langgraph(self):
        with patch("agentcop.adapters.langgraph._require_langgraph") as mock_guard:
            from agentcop.adapters.langgraph import LangGraphSentinelAdapter

            LangGraphSentinelAdapter()
            mock_guard.assert_called_once()

    def test_source_system_is_langgraph(self, adapter):
        assert adapter.source_system == "langgraph"

    def test_thread_id_stored(self):
        a = _make_adapter(thread_id="my-thread")
        assert a._thread_id == "my-thread"

    def test_thread_id_defaults_to_none(self, adapter_no_thread):
        assert adapter_no_thread._thread_id is None


# ---------------------------------------------------------------------------
# task events → node_start
# ---------------------------------------------------------------------------


class TestFromTask:
    def _raw(self, **overrides):
        base = {
            "type": "task",
            "timestamp": "2026-01-01T10:00:00Z",
            "step": 2,
            "payload": {
                "id": "task-abc",
                "name": "my_node",
                "triggers": ["start:my_node"],
                "interrupts": [],
                "metadata": {},
            },
        }
        base.update(overrides)
        return base

    def test_event_type_is_node_start(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert e.event_type == "node_start"

    def test_severity_is_info(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert e.severity == "INFO"

    def test_event_id_prefixed_with_lg_task(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert e.event_id == "lg-task-task-abc"

    def test_source_system(self, adapter):
        assert adapter.to_sentinel_event(self._raw()).source_system == "langgraph"

    def test_body_contains_node_name_and_step(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert "my_node" in e.body
        assert "2" in e.body

    def test_attributes_node_name(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert e.attributes["node"] == "my_node"

    def test_attributes_task_id(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert e.attributes["task_id"] == "task-abc"

    def test_attributes_step(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert e.attributes["step"] == 2

    def test_attributes_triggers(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert e.attributes["triggers"] == ["start:my_node"]

    def test_trace_id_from_payload_metadata(self, adapter_no_thread):
        raw = self._raw()
        raw["payload"]["metadata"]["thread_id"] = "thread-from-payload"
        e = adapter_no_thread.to_sentinel_event(raw)
        assert e.trace_id == "thread-from-payload"

    def test_trace_id_falls_back_to_default(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert e.trace_id == "thread-default"

    def test_trace_id_payload_wins_over_default(self):
        a = _make_adapter(thread_id="default-thread")
        raw = self._raw()
        raw["payload"]["metadata"]["thread_id"] = "payload-thread"
        e = a.to_sentinel_event(raw)
        assert e.trace_id == "payload-thread"

    def test_timestamp_parsed_from_iso_string(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert isinstance(e.timestamp, datetime)
        assert e.timestamp.year == 2026

    def test_missing_payload_does_not_raise(self, adapter):
        e = adapter.to_sentinel_event({"type": "task", "step": 0})
        assert e.event_type == "node_start"
        assert e.attributes["node"] == "unknown"

    def test_missing_task_id_generates_one(self, adapter):
        raw = self._raw()
        del raw["payload"]["id"]
        e = adapter.to_sentinel_event(raw)
        assert e.event_id.startswith("lg-task-")
        assert len(e.event_id) > len("lg-task-")


# ---------------------------------------------------------------------------
# task_result events → node_end / node_error
# ---------------------------------------------------------------------------


class TestFromTaskResult:
    def _raw_success(self, **overrides):
        base = {
            "type": "task_result",
            "timestamp": "2026-01-01T10:00:01Z",
            "step": 2,
            "payload": {
                "id": "task-abc",
                "name": "my_node",
                "error": None,
                "interrupts": [],
                "metadata": {},
            },
        }
        base.update(overrides)
        return base

    def _raw_error(self, error_msg="something went wrong"):
        raw = self._raw_success()
        raw["payload"]["error"] = error_msg
        return raw

    def test_success_event_type_is_node_end(self, adapter):
        assert adapter.to_sentinel_event(self._raw_success()).event_type == "node_end"

    def test_success_severity_is_info(self, adapter):
        assert adapter.to_sentinel_event(self._raw_success()).severity == "INFO"

    def test_success_event_id_prefixed_with_lg_result(self, adapter):
        assert adapter.to_sentinel_event(self._raw_success()).event_id == "lg-result-task-abc"

    def test_success_body_contains_node_and_step(self, adapter):
        e = adapter.to_sentinel_event(self._raw_success())
        assert "my_node" in e.body
        assert "2" in e.body

    def test_success_attributes(self, adapter):
        e = adapter.to_sentinel_event(self._raw_success())
        assert e.attributes["node"] == "my_node"
        assert e.attributes["task_id"] == "task-abc"
        assert e.attributes["step"] == 2
        assert "error" not in e.attributes

    def test_error_event_type_is_node_error(self, adapter):
        assert adapter.to_sentinel_event(self._raw_error()).event_type == "node_error"

    def test_error_severity_is_error(self, adapter):
        assert adapter.to_sentinel_event(self._raw_error()).severity == "ERROR"

    def test_error_body_contains_error_message(self, adapter):
        e = adapter.to_sentinel_event(self._raw_error("division by zero"))
        assert "division by zero" in e.body

    def test_error_attributes_contains_error(self, adapter):
        e = adapter.to_sentinel_event(self._raw_error("division by zero"))
        assert e.attributes["error"] == "division by zero"

    def test_interrupts_included_in_attributes_when_present(self, adapter):
        raw = self._raw_success()
        raw["payload"]["interrupts"] = ["human_in_loop"]
        e = adapter.to_sentinel_event(raw)
        assert e.attributes["interrupts"] == ["human_in_loop"]

    def test_empty_interrupts_not_included_in_attributes(self, adapter):
        e = adapter.to_sentinel_event(self._raw_success())
        assert "interrupts" not in e.attributes

    def test_trace_id_from_payload_metadata(self, adapter_no_thread):
        raw = self._raw_success()
        raw["payload"]["metadata"]["thread_id"] = "t-42"
        assert adapter_no_thread.to_sentinel_event(raw).trace_id == "t-42"

    def test_trace_id_falls_back_to_default(self, adapter):
        assert adapter.to_sentinel_event(self._raw_success()).trace_id == "thread-default"

    def test_missing_payload_does_not_raise(self, adapter):
        e = adapter.to_sentinel_event({"type": "task_result", "step": 0})
        assert e.event_type == "node_end"


# ---------------------------------------------------------------------------
# checkpoint events → checkpoint_saved
# ---------------------------------------------------------------------------


class TestFromCheckpoint:
    def _raw(self, **overrides):
        base = {
            "type": "checkpoint",
            "timestamp": "2026-01-01T10:00:02Z",
            "step": 2,
            "payload": {
                "config": {
                    "configurable": {
                        "thread_id": "thread-99",
                        "checkpoint_id": "ckpt-xyz",
                    }
                },
                "metadata": {"source": "loop", "step": 2, "writes": {}},
                "values": {"messages": []},
                "next": ["next_node"],
                "tasks": [],
                "parent_config": None,
            },
        }
        base.update(overrides)
        return base

    def test_event_type_is_checkpoint_saved(self, adapter):
        assert adapter.to_sentinel_event(self._raw()).event_type == "checkpoint_saved"

    def test_severity_is_info(self, adapter):
        assert adapter.to_sentinel_event(self._raw()).severity == "INFO"

    def test_event_id_prefixed_with_lg_checkpoint(self, adapter):
        assert adapter.to_sentinel_event(self._raw()).event_id == "lg-checkpoint-ckpt-xyz"

    def test_body_contains_step_and_source(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert "2" in e.body
        assert "loop" in e.body

    def test_attributes_checkpoint_id(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert e.attributes["checkpoint_id"] == "ckpt-xyz"

    def test_attributes_thread_id(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert e.attributes["thread_id"] == "thread-99"

    def test_attributes_step(self, adapter):
        assert adapter.to_sentinel_event(self._raw()).attributes["step"] == 2

    def test_attributes_source(self, adapter):
        assert adapter.to_sentinel_event(self._raw()).attributes["source"] == "loop"

    def test_attributes_next_nodes(self, adapter):
        e = adapter.to_sentinel_event(self._raw())
        assert e.attributes["next"] == ["next_node"]

    def test_trace_id_from_configurable(self, adapter_no_thread):
        e = adapter_no_thread.to_sentinel_event(self._raw())
        assert e.trace_id == "thread-99"

    def test_trace_id_falls_back_to_default_when_missing(self):
        a = _make_adapter(thread_id="fallback-thread")
        raw = self._raw()
        del raw["payload"]["config"]["configurable"]["thread_id"]
        e = a.to_sentinel_event(raw)
        assert e.trace_id == "fallback-thread"

    def test_missing_checkpoint_id_generates_one(self, adapter):
        raw = self._raw()
        del raw["payload"]["config"]["configurable"]["checkpoint_id"]
        e = adapter.to_sentinel_event(raw)
        assert e.event_id.startswith("lg-checkpoint-")
        assert len(e.event_id) > len("lg-checkpoint-")

    def test_missing_payload_does_not_raise(self, adapter):
        e = adapter.to_sentinel_event({"type": "checkpoint", "step": 0})
        assert e.event_type == "checkpoint_saved"


# ---------------------------------------------------------------------------
# Unknown event types
# ---------------------------------------------------------------------------


class TestFromUnknown:
    def test_event_type_is_unknown_langgraph_event(self, adapter):
        e = adapter.to_sentinel_event({"type": "metadata", "step": 0})
        assert e.event_type == "unknown_langgraph_event"

    def test_severity_is_info(self, adapter):
        assert adapter.to_sentinel_event({"type": "debug"}).severity == "INFO"

    def test_event_id_prefixed_with_lg_unknown(self, adapter):
        e = adapter.to_sentinel_event({"type": "weird"})
        assert e.event_id.startswith("lg-unknown-")

    def test_body_contains_original_type(self, adapter):
        e = adapter.to_sentinel_event({"type": "some_new_type", "step": 3})
        assert "some_new_type" in e.body

    def test_attributes_original_type(self, adapter):
        e = adapter.to_sentinel_event({"type": "some_new_type", "step": 3})
        assert e.attributes["original_type"] == "some_new_type"

    def test_attributes_step(self, adapter):
        e = adapter.to_sentinel_event({"type": "x", "step": 7})
        assert e.attributes["step"] == 7

    def test_trace_id_is_default_thread(self, adapter):
        e = adapter.to_sentinel_event({"type": "x"})
        assert e.trace_id == "thread-default"

    def test_empty_dict_does_not_raise(self, adapter):
        e = adapter.to_sentinel_event({})
        assert e.event_type == "unknown_langgraph_event"


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    def test_iso_z_suffix_parsed(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "task",
                "timestamp": "2026-06-15T08:30:00Z",
                "step": 0,
                "payload": {"id": "t1", "name": "n"},
            }
        )
        assert e.timestamp == datetime(2026, 6, 15, 8, 30, 0, tzinfo=UTC)

    def test_iso_offset_parsed(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "task",
                "timestamp": "2026-06-15T08:30:00+00:00",
                "step": 0,
                "payload": {"id": "t1", "name": "n"},
            }
        )
        assert e.timestamp.year == 2026

    def test_missing_timestamp_uses_now(self, adapter):
        before = datetime.now(UTC)
        e = adapter.to_sentinel_event({"type": "task", "step": 0})
        after = datetime.now(UTC)
        assert before <= e.timestamp <= after

    def test_invalid_timestamp_falls_back_to_now(self, adapter):
        before = datetime.now(UTC)
        e = adapter.to_sentinel_event(
            {
                "type": "task",
                "timestamp": "not-a-date",
                "step": 0,
            }
        )
        after = datetime.now(UTC)
        assert before <= e.timestamp <= after


# ---------------------------------------------------------------------------
# iter_events
# ---------------------------------------------------------------------------


class TestIterEvents:
    def _stream(self):
        return [
            {
                "type": "task",
                "timestamp": "2026-01-01T00:00:00Z",
                "step": 1,
                "payload": {"id": "t1", "name": "node_a", "triggers": [], "metadata": {}},
            },
            {
                "type": "task_result",
                "timestamp": "2026-01-01T00:00:01Z",
                "step": 1,
                "payload": {
                    "id": "t1",
                    "name": "node_a",
                    "error": None,
                    "interrupts": [],
                    "metadata": {},
                },
            },
            {
                "type": "checkpoint",
                "timestamp": "2026-01-01T00:00:02Z",
                "step": 1,
                "payload": {
                    "config": {"configurable": {"thread_id": "th-1", "checkpoint_id": "ck-1"}},
                    "metadata": {"source": "loop"},
                    "next": [],
                },
            },
        ]

    def test_yields_correct_count(self, adapter):
        events = list(adapter.iter_events(self._stream()))
        assert len(events) == 3

    def test_yields_sentinel_events(self, adapter):
        from agentcop import SentinelEvent

        for e in adapter.iter_events(self._stream()):
            assert isinstance(e, SentinelEvent)

    def test_event_types_in_order(self, adapter):
        events = list(adapter.iter_events(self._stream()))
        assert events[0].event_type == "node_start"
        assert events[1].event_type == "node_end"
        assert events[2].event_type == "checkpoint_saved"

    def test_accepts_generator(self, adapter):
        from agentcop import SentinelEvent

        events = list(adapter.iter_events(e for e in self._stream()))
        assert all(isinstance(e, SentinelEvent) for e in events)

    def test_empty_stream_yields_nothing(self, adapter):
        assert list(adapter.iter_events([])) == []


# ---------------------------------------------------------------------------
# SentinelAdapter protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_sentinel_adapter_protocol(self, adapter):
        from agentcop import SentinelAdapter

        assert isinstance(adapter, SentinelAdapter)

    def test_source_system_attribute(self, adapter):
        assert adapter.source_system == "langgraph"

    def test_to_sentinel_event_callable(self, adapter):
        assert callable(adapter.to_sentinel_event)


# ---------------------------------------------------------------------------
# Integration with Sentinel
# ---------------------------------------------------------------------------


class TestSentinelIntegration:
    def test_ingest_via_iter_events(self, adapter):
        from agentcop import Sentinel

        stream = [
            {
                "type": "task",
                "timestamp": "2026-01-01T00:00:00Z",
                "step": 1,
                "payload": {"id": "t1", "name": "planner", "triggers": [], "metadata": {}},
            },
            {
                "type": "task_result",
                "timestamp": "2026-01-01T00:00:01Z",
                "step": 1,
                "payload": {
                    "id": "t1",
                    "name": "planner",
                    "error": None,
                    "interrupts": [],
                    "metadata": {},
                },
            },
        ]

        sentinel = Sentinel()
        sentinel.ingest(adapter.iter_events(stream))
        # No built-in detectors fire on node_start/node_end
        assert sentinel.detect_violations() == []

    def test_custom_detector_fires_on_node_error(self, adapter):
        from agentcop import Sentinel, ViolationRecord

        def detect_node_errors(event):
            if event.event_type == "node_error":
                return ViolationRecord(
                    violation_type="node_execution_failed",
                    severity="ERROR",
                    source_event_id=event.event_id,
                    trace_id=event.trace_id,
                    detail={
                        "node": event.attributes.get("node"),
                        "error": event.attributes.get("error"),
                    },
                )

        stream = [
            {
                "type": "task_result",
                "timestamp": "2026-01-01T00:00:00Z",
                "step": 1,
                "payload": {
                    "id": "t-fail",
                    "name": "risky_node",
                    "error": "KeyError: 'missing_key'",
                    "interrupts": [],
                    "metadata": {"thread_id": "thread-default"},
                },
            }
        ]

        sentinel = Sentinel(detectors=[detect_node_errors])
        sentinel.ingest(adapter.iter_events(stream))
        violations = sentinel.detect_violations()

        assert len(violations) == 1
        assert violations[0].violation_type == "node_execution_failed"
        assert violations[0].detail["node"] == "risky_node"
        assert "KeyError" in violations[0].detail["error"]

    def test_trace_id_consistent_across_run(self):
        a = _make_adapter(thread_id="run-xyz")
        from agentcop import Sentinel

        stream = [
            {
                "type": "task",
                "step": 1,
                "payload": {"id": "t1", "name": "a", "triggers": [], "metadata": {}},
            },
            {
                "type": "task_result",
                "step": 1,
                "payload": {
                    "id": "t1",
                    "name": "a",
                    "error": None,
                    "interrupts": [],
                    "metadata": {},
                },
            },
            {
                "type": "checkpoint",
                "step": 1,
                "payload": {
                    "config": {"configurable": {"thread_id": "run-xyz", "checkpoint_id": "ck-1"}},
                    "metadata": {"source": "loop"},
                    "next": [],
                },
            },
        ]

        sentinel = Sentinel()
        sentinel.ingest(a.iter_events(stream))
        events_ingested = list(a.iter_events(stream))
        assert all(e.trace_id == "run-xyz" for e in events_ingested)


# ---------------------------------------------------------------------------
# Runtime security tests
# ---------------------------------------------------------------------------


def _make_adapter_runtime(
    gate=None, permissions=None, sandbox=None, approvals=None, identity=None
):
    with patch("agentcop.adapters.langgraph._require_langgraph"):
        from agentcop.adapters.langgraph import LangGraphSentinelAdapter

        return LangGraphSentinelAdapter(
            thread_id="rt-thread",
            gate=gate,
            permissions=permissions,
            sandbox=sandbox,
            approvals=approvals,
            identity=identity,
        )


_TASK_RAW = {
    "type": "task",
    "step": 1,
    "payload": {"name": "my_node", "id": "t-1", "triggers": [], "metadata": {}},
}


class TestRuntimeSecurityLangGraph:
    def test_init_stores_none_by_default(self):
        a = _make_adapter_runtime()
        assert a._gate is None
        assert a._permissions is None
        assert a._sandbox is None
        assert a._approvals is None
        assert a._identity is None

    def test_init_stores_runtime_params(self):
        gate = MagicMock()
        perms = MagicMock()
        sandbox = MagicMock()
        approvals = MagicMock()
        identity = MagicMock()
        a = _make_adapter_runtime(
            gate=gate,
            permissions=perms,
            sandbox=sandbox,
            approvals=approvals,
            identity=identity,
        )
        assert a._gate is gate
        assert a._permissions is perms
        assert a._sandbox is sandbox
        assert a._approvals is approvals
        assert a._identity is identity

    def test_gate_denial_raises_permission_error(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=False, reason="blocked", risk_score=90)
        a = _make_adapter_runtime(gate=gate)
        with pytest.raises(PermissionError, match="blocked"):
            list(a.iter_events([_TASK_RAW]))

    def test_gate_denial_buffers_gate_denied_event(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=False, reason="blocked", risk_score=90)
        a = _make_adapter_runtime(gate=gate)
        with pytest.raises(PermissionError):
            list(a.iter_events([_TASK_RAW]))
        events = a.drain()
        assert any(e.event_type == "gate_denied" for e in events)

    def test_permission_violation_raises_permission_error(self):
        perms = MagicMock()
        perms.verify.return_value = MagicMock(granted=False, reason="not allowed")
        a = _make_adapter_runtime(permissions=perms)
        with pytest.raises(PermissionError, match="not allowed"):
            list(a.iter_events([_TASK_RAW]))

    def test_permission_violation_fires_sentinel_event(self):
        perms = MagicMock()
        perms.verify.return_value = MagicMock(granted=False, reason="not allowed")
        a = _make_adapter_runtime(permissions=perms)
        with pytest.raises(PermissionError):
            list(a.iter_events([_TASK_RAW]))
        events = a.drain()
        assert any(e.event_type == "permission_violation" for e in events)

    def test_gate_allow_passes_through_normally(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=True, reason="ok", risk_score=10)
        a = _make_adapter_runtime(gate=gate)
        results = list(a.iter_events([_TASK_RAW]))
        assert len(results) == 1
        assert results[0].event_type == "node_start"

    def test_no_gate_no_check(self):
        a = _make_adapter_runtime()
        results = list(a.iter_events([_TASK_RAW]))
        assert len(results) == 1

    def test_drain_returns_security_events(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=False, reason="denied", risk_score=99)
        a = _make_adapter_runtime(gate=gate)
        with pytest.raises(PermissionError):
            list(a.iter_events([_TASK_RAW]))
        drained = a.drain()
        assert len(drained) >= 1
        assert drained[0].source_system == "langgraph"

    def test_approval_requested_when_risk_high(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=True, reason="ok", risk_score=95)
        approvals = MagicMock()
        approvals.requires_approval_above = 70
        req = MagicMock(request_id="req-1", denied=False)
        approvals.submit.return_value = req
        approvals.wait_for_decision.return_value = MagicMock(denied=False, reason="approved")
        a = _make_adapter_runtime(gate=gate, approvals=approvals)
        list(a.iter_events([_TASK_RAW]))
        approvals.submit.assert_called_once()
        approvals.wait_for_decision.assert_called_once()
