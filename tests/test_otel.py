"""
Integration tests for agentcop.otel:
  - to_otel_attributes()  — no OTel dependency required
  - to_otel_log_record()  — mocked OTel SDK
  - OtelSentinelExporter  — mocked OTel SDK
"""

import sys
from datetime import UTC, datetime
from types import ModuleType
from unittest.mock import MagicMock, call, patch

import pytest

from agentcop.event import SentinelEvent
from agentcop.otel import to_otel_attributes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(**kwargs) -> SentinelEvent:
    defaults = {
        "event_id": "e-otel-1",
        "event_type": "node_end",
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "severity": "INFO",
        "body": "otel test",
        "source_system": "test-otel",
    }
    defaults.update(kwargs)
    return SentinelEvent(**defaults)


def _otel_modules() -> dict:
    """Return a minimal sys.modules patch that satisfies otel.py's imports."""
    mock_otel = MagicMock()
    mock_severity = MagicMock()
    mock_severity_instance = MagicMock()
    mock_severity.return_value = mock_severity_instance

    mock_log_record_cls = MagicMock()

    mock_logs_mod = MagicMock()
    mock_logs_mod.get_logger_provider = MagicMock(return_value=MagicMock())

    mock_severity_mod = MagicMock()
    mock_severity_mod.SeverityNumber = mock_severity

    mock_sdk_logs_mod = MagicMock()
    mock_sdk_logs_mod.LogRecord = mock_log_record_cls

    return {
        "opentelemetry": mock_otel,
        "opentelemetry._logs": mock_logs_mod,
        "opentelemetry._logs.severity": mock_severity_mod,
        "opentelemetry.sdk": MagicMock(),
        "opentelemetry.sdk._logs": mock_sdk_logs_mod,
    }


# ---------------------------------------------------------------------------
# to_otel_attributes — no SDK needed
# ---------------------------------------------------------------------------


class TestToOtelAttributes:
    def test_core_fields_present(self):
        attrs = to_otel_attributes(_event())
        assert "sentinel.event_id" in attrs
        assert "sentinel.event_type" in attrs
        assert "sentinel.source_system" in attrs

    def test_event_id_value(self):
        attrs = to_otel_attributes(_event(event_id="my-evt"))
        assert attrs["sentinel.event_id"] == "my-evt"

    def test_event_type_value(self):
        attrs = to_otel_attributes(_event(event_type="packet_rejected"))
        assert attrs["sentinel.event_type"] == "packet_rejected"

    def test_source_system_value(self):
        attrs = to_otel_attributes(_event(source_system="firewall"))
        assert attrs["sentinel.source_system"] == "firewall"

    def test_producer_id_included_when_present(self):
        attrs = to_otel_attributes(_event(producer_id="prod-1"))
        assert attrs["sentinel.producer_id"] == "prod-1"

    def test_producer_id_absent_when_none(self):
        attrs = to_otel_attributes(_event())
        assert "sentinel.producer_id" not in attrs

    def test_trace_id_included_when_present(self):
        attrs = to_otel_attributes(_event(trace_id="trace-abc"))
        assert attrs["sentinel.trace_id"] == "trace-abc"

    def test_trace_id_absent_when_none(self):
        attrs = to_otel_attributes(_event())
        assert "sentinel.trace_id" not in attrs

    def test_span_id_included_when_present(self):
        attrs = to_otel_attributes(_event(span_id="span-xyz"))
        assert attrs["sentinel.span_id"] == "span-xyz"

    def test_span_id_absent_when_none(self):
        attrs = to_otel_attributes(_event())
        assert "sentinel.span_id" not in attrs

    def test_extra_attributes_namespaced_under_sentinel(self):
        attrs = to_otel_attributes(_event(attributes={"my_key": "my_val"}))
        assert "sentinel.my_key" in attrs
        assert attrs["sentinel.my_key"] == "my_val"

    def test_multiple_extra_attributes(self):
        attrs = to_otel_attributes(_event(attributes={"a": "1", "b": "2"}))
        assert attrs["sentinel.a"] == "1"
        assert attrs["sentinel.b"] == "2"

    def test_none_attribute_values_are_skipped(self):
        attrs = to_otel_attributes(_event(attributes={"k": None}))
        assert "sentinel.k" not in attrs

    def test_attribute_values_coerced_to_str(self):
        attrs = to_otel_attributes(_event(attributes={"count": 42}))
        assert attrs["sentinel.count"] == "42"

    def test_returns_dict(self):
        assert isinstance(to_otel_attributes(_event()), dict)


# ---------------------------------------------------------------------------
# _require_otel guard
# ---------------------------------------------------------------------------


class TestRequireOtel:
    def test_raises_import_error_when_otel_missing(self):
        from agentcop.otel import _require_otel

        with patch.dict(sys.modules, {"opentelemetry": None}):
            with pytest.raises(ImportError, match="opentelemetry-sdk"):
                _require_otel()

    def test_no_error_when_otel_present(self):
        from agentcop.otel import _require_otel

        with patch.dict(sys.modules, {"opentelemetry": MagicMock()}):
            _require_otel()  # should not raise


# ---------------------------------------------------------------------------
# to_otel_log_record — mocked SDK
# ---------------------------------------------------------------------------


class TestToOtelLogRecord:
    def _call_with_mocks(self, event: SentinelEvent):
        mods = _otel_modules()
        log_record_cls = mods["opentelemetry.sdk._logs"].LogRecord
        with patch.dict(sys.modules, mods):
            # Force re-import inside the function by importing fresh
            import importlib

            import agentcop.otel as otel_mod

            importlib.reload(otel_mod)
            otel_mod.to_otel_log_record(event)
        return log_record_cls

    def test_log_record_is_constructed(self):
        mods = _otel_modules()
        log_record_cls = mods["opentelemetry.sdk._logs"].LogRecord
        with patch.dict(sys.modules, mods):
            import importlib

            import agentcop.otel as otel_mod

            importlib.reload(otel_mod)
            otel_mod.to_otel_log_record(_event())
        assert log_record_cls.called

    def test_log_record_body_matches_event_body(self):
        mods = _otel_modules()
        log_record_cls = mods["opentelemetry.sdk._logs"].LogRecord
        with patch.dict(sys.modules, mods):
            import importlib

            import agentcop.otel as otel_mod

            importlib.reload(otel_mod)
            otel_mod.to_otel_log_record(_event(body="hello otel"))
        _, kwargs = log_record_cls.call_args
        assert kwargs.get("body") == "hello otel"

    def test_log_record_severity_text(self):
        mods = _otel_modules()
        log_record_cls = mods["opentelemetry.sdk._logs"].LogRecord
        with patch.dict(sys.modules, mods):
            import importlib

            import agentcop.otel as otel_mod

            importlib.reload(otel_mod)
            otel_mod.to_otel_log_record(_event(severity="ERROR"))
        _, kwargs = log_record_cls.call_args
        assert kwargs.get("severity_text") == "ERROR"

    def test_log_record_attributes_include_sentinel_event_id(self):
        mods = _otel_modules()
        log_record_cls = mods["opentelemetry.sdk._logs"].LogRecord
        with patch.dict(sys.modules, mods):
            import importlib

            import agentcop.otel as otel_mod

            importlib.reload(otel_mod)
            otel_mod.to_otel_log_record(_event(event_id="check-id"))
        _, kwargs = log_record_cls.call_args
        assert kwargs["attributes"]["sentinel.event_id"] == "check-id"


# ---------------------------------------------------------------------------
# OtelSentinelExporter — mocked SDK
# ---------------------------------------------------------------------------


class TestOtelSentinelExporter:
    def test_export_emits_one_record_per_event(self):
        mods = _otel_modules()
        mock_logger = MagicMock()
        mock_provider = MagicMock()
        mock_provider.get_logger.return_value = mock_logger

        with patch.dict(sys.modules, mods):
            import importlib

            import agentcop.otel as otel_mod

            importlib.reload(otel_mod)
            exporter = otel_mod.OtelSentinelExporter(logger_provider=mock_provider)
            events = [_event(event_id="a"), _event(event_id="b"), _event(event_id="c")]
            exporter.export(events)

        assert mock_logger.emit.call_count == 3

    def test_export_empty_list_emits_nothing(self):
        mods = _otel_modules()
        mock_logger = MagicMock()
        mock_provider = MagicMock()
        mock_provider.get_logger.return_value = mock_logger

        with patch.dict(sys.modules, mods):
            import importlib

            import agentcop.otel as otel_mod

            importlib.reload(otel_mod)
            exporter = otel_mod.OtelSentinelExporter(logger_provider=mock_provider)
            exporter.export([])

        mock_logger.emit.assert_not_called()

    def test_exporter_uses_instrumentation_name_as_logger_name(self):
        mods = _otel_modules()
        mock_provider = MagicMock()
        mock_provider.get_logger.return_value = MagicMock()

        with patch.dict(sys.modules, mods):
            import importlib

            import agentcop.otel as otel_mod

            importlib.reload(otel_mod)
            otel_mod.OtelSentinelExporter(
                logger_provider=mock_provider,
                instrumentation_name="my-sentinel",
            )

        mock_provider.get_logger.assert_called_once_with("my-sentinel")

    def test_exporter_raises_when_otel_missing(self):
        with patch.dict(sys.modules, {"opentelemetry": None}):
            import importlib

            import agentcop.otel as otel_mod

            importlib.reload(otel_mod)
            with pytest.raises(ImportError, match="opentelemetry-sdk"):
                otel_mod.OtelSentinelExporter()
