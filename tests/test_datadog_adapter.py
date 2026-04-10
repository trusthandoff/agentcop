"""
Tests for src/agentcop/adapters/datadog.py

All tests mock the ddtrace import guard so ddtrace does not need to be
installed in the test environment. Setup tests build a mock tracer with a
_writer, then call the intercepted write() with mock spans.
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentcop import Sentinel, ViolationRecord
from agentcop.event import SentinelEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(run_id=None):
    """Return a DatadogSentinelAdapter with the import guard bypassed."""
    with patch("agentcop.adapters.datadog._require_ddtrace"):
        from agentcop.adapters.datadog import DatadogSentinelAdapter

        return DatadogSentinelAdapter(run_id=run_id)


def _make_mock_tracer():
    """Return a MagicMock tracer whose _writer.write is a regular Mock."""
    tracer = MagicMock()
    tracer._writer = MagicMock()
    tracer._writer.write = MagicMock(return_value=None)
    return tracer


def _setup_adapter(run_id=None):
    """Return (adapter, mock_tracer) with the interceptor already installed."""
    adapter = _make_adapter(run_id=run_id)
    tracer = _make_mock_tracer()
    adapter.setup(tracer)
    return adapter, tracer


def _make_span(
    name="my-op",
    resource="my-resource",
    service="my-svc",
    component="",
    span_kind="client",
    error=0,
    trace_id=0xABCDEF1234567890,
    span_id=0x1234567890ABCDEF,
    parent_id=0,
    duration=1_500_000,
    start_ns=1_700_000_000_000_000_000,
    tags: dict[str, Any] | None = None,
    metrics: dict[str, float] | None = None,
):
    """Build a minimal mock ddtrace Span."""
    span = MagicMock()
    span.name = name
    span.resource = resource
    span.service = service
    span.error = error
    span.trace_id = trace_id
    span.span_id = span_id
    span.parent_id = parent_id
    span.duration = duration
    span.start_ns = start_ns
    span.start = (start_ns / 1e9) if start_ns is not None else 0.0

    all_tags: dict[str, Any] = {"component": component, "span.kind": span_kind}
    if tags:
        all_tags.update(tags)
    all_metrics: dict[str, float] = dict(metrics or {})

    def get_tag(key):
        return all_tags.get(key)

    def get_metric(key):
        return all_metrics.get(key)

    span.get_tag = get_tag
    span.get_metric = get_metric
    return span


def _write(tracer, spans):
    """Invoke the intercepted writer with a list of spans."""
    tracer._writer.write(spans)


# ---------------------------------------------------------------------------
# TestRequireDdtrace
# ---------------------------------------------------------------------------


class TestRequireDdtrace:
    def test_raises_when_ddtrace_missing(self):
        with patch.dict(sys.modules, {"ddtrace": None}):
            with pytest.raises(ImportError, match="ddtrace"):
                from agentcop.adapters.datadog import _require_ddtrace

                _require_ddtrace()

    def test_passes_when_ddtrace_available(self):
        fake = MagicMock()
        with patch.dict(sys.modules, {"ddtrace": fake}):
            from agentcop.adapters.datadog import _require_ddtrace

            _require_ddtrace()  # must not raise


# ---------------------------------------------------------------------------
# TestAdapterInit
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_source_system(self):
        assert _make_adapter().source_system == "datadog"

    def test_run_id_none_by_default(self):
        assert _make_adapter()._run_id is None

    def test_run_id_stored(self):
        assert _make_adapter(run_id="sess-1")._run_id == "sess-1"

    def test_buffer_starts_empty(self):
        assert _make_adapter().drain() == []

    def test_lock_exists(self):
        adapter = _make_adapter()
        assert isinstance(adapter._lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# TestFromSpanFinished
# ---------------------------------------------------------------------------


class TestFromSpanFinished:
    def test_event_type(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_finished"})
        assert ev.event_type == "span_finished"

    def test_severity(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_finished"})
        assert ev.severity == "INFO"

    def test_event_id_prefix(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_finished"})
        assert ev.event_id.startswith("dd-span-")

    def test_source_system(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_finished"})
        assert ev.source_system == "datadog"

    def test_body_contains_span_name(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_finished", "span_name": "my-op"})
        assert "my-op" in ev.body

    def test_body_contains_service(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_finished", "service": "my-svc"})
        assert "my-svc" in ev.body

    def test_trace_id_from_run_id(self):
        adapter = _make_adapter(run_id="run-007")
        ev = adapter.to_sentinel_event({"type": "span_finished"})
        assert ev.trace_id == "run-007"

    def test_trace_id_from_dd_trace_id(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event(
            {"type": "span_finished", "dd_trace_id": "abcdef0123456789"}
        )
        assert ev.trace_id == "abcdef0123456789"

    def test_trace_id_none_when_unset(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "span_finished"})
        assert ev.trace_id is None

    def test_attributes_span_name(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_finished", "span_name": "my-op"})
        assert ev.attributes["span_name"] == "my-op"

    def test_attributes_component(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_finished", "component": "mycomp"})
        assert ev.attributes["component"] == "mycomp"

    def test_attributes_duration_ns(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_finished", "duration_ns": 2_000_000})
        assert ev.attributes["duration_ns"] == 2_000_000

    def test_attributes_error_false(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_finished"})
        assert ev.attributes["error"] is False

    def test_attributes_dd_span_ids(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "span_finished",
                "dd_trace_id": "aaaa",
                "dd_span_id": "bbbb",
                "dd_parent_id": "cccc",
            }
        )
        assert ev.attributes["dd_trace_id"] == "aaaa"
        assert ev.attributes["dd_span_id"] == "bbbb"
        assert ev.attributes["dd_parent_id"] == "cccc"


# ---------------------------------------------------------------------------
# TestFromSpanError
# ---------------------------------------------------------------------------


class TestFromSpanError:
    def test_event_type(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_error"})
        assert ev.event_type == "span_error"

    def test_severity(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_error"})
        assert ev.severity == "ERROR"

    def test_event_id_prefix(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_error"})
        assert ev.event_id.startswith("dd-span-")

    def test_body_contains_error_message(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "span_error",
                "span_name": "my-op",
                "error_message": "connection refused",
            }
        )
        assert "connection refused" in ev.body

    def test_attributes_error_true(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_error", "error": True})
        assert ev.attributes["error"] is True

    def test_attributes_error_type(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "span_error",
                "error_type": "ConnectionError",
            }
        )
        assert ev.attributes["error_type"] == "ConnectionError"

    def test_attributes_error_message(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "span_error",
                "error_message": "timeout",
            }
        )
        assert ev.attributes["error_message"] == "timeout"


# ---------------------------------------------------------------------------
# TestFromLLMSpanFinished
# ---------------------------------------------------------------------------


class TestFromLLMSpanFinished:
    def test_event_type(self):
        ev = _make_adapter().to_sentinel_event({"type": "llm_span_finished"})
        assert ev.event_type == "llm_span_finished"

    def test_severity(self):
        ev = _make_adapter().to_sentinel_event({"type": "llm_span_finished"})
        assert ev.severity == "INFO"

    def test_event_id_prefix(self):
        ev = _make_adapter().to_sentinel_event({"type": "llm_span_finished"})
        assert ev.event_id.startswith("dd-llm-")

    def test_body_contains_model(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "llm_span_finished",
                "span_name": "openai.request",
                "model": "gpt-4o-mini",
                "component": "openai",
            }
        )
        assert "gpt-4o-mini" in ev.body

    def test_body_contains_provider(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "llm_span_finished",
                "span_name": "openai.request",
                "provider": "openai",
                "component": "openai",
            }
        )
        assert "openai" in ev.body

    def test_attributes_model(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "llm_span_finished",
                "model": "gpt-4o",
            }
        )
        assert ev.attributes["model"] == "gpt-4o"

    def test_attributes_provider(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "llm_span_finished",
                "provider": "openai",
            }
        )
        assert ev.attributes["provider"] == "openai"

    def test_attributes_provider_falls_back_to_component(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "llm_span_finished",
                "component": "anthropic",
            }
        )
        assert ev.attributes["provider"] == "anthropic"

    def test_attributes_usage(self):
        usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "llm_span_finished",
                "usage": usage,
            }
        )
        assert ev.attributes["usage"] == usage

    def test_attributes_usage_defaults_empty(self):
        ev = _make_adapter().to_sentinel_event({"type": "llm_span_finished"})
        assert ev.attributes["usage"] == {}

    def test_attributes_model_defaults_unknown(self):
        ev = _make_adapter().to_sentinel_event({"type": "llm_span_finished"})
        assert ev.attributes["model"] == "unknown"


# ---------------------------------------------------------------------------
# TestFromLLMSpanError
# ---------------------------------------------------------------------------


class TestFromLLMSpanError:
    def test_event_type(self):
        ev = _make_adapter().to_sentinel_event({"type": "llm_span_error"})
        assert ev.event_type == "llm_span_error"

    def test_severity(self):
        ev = _make_adapter().to_sentinel_event({"type": "llm_span_error"})
        assert ev.severity == "ERROR"

    def test_event_id_prefix(self):
        ev = _make_adapter().to_sentinel_event({"type": "llm_span_error"})
        assert ev.event_id.startswith("dd-llm-")

    def test_body_contains_error_message(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "llm_span_error",
                "model": "gpt-4o",
                "error_message": "rate limit exceeded",
            }
        )
        assert "rate limit exceeded" in ev.body

    def test_body_contains_model(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "llm_span_error",
                "model": "claude-3-opus",
            }
        )
        assert "claude-3-opus" in ev.body

    def test_attributes_include_llm_attrs(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "llm_span_error",
                "model": "gpt-4o",
                "provider": "openai",
                "usage": {"total_tokens": 5},
            }
        )
        assert ev.attributes["model"] == "gpt-4o"
        assert ev.attributes["provider"] == "openai"
        assert ev.attributes["usage"] == {"total_tokens": 5}


# ---------------------------------------------------------------------------
# TestFromHTTPSpanFinished
# ---------------------------------------------------------------------------


class TestFromHTTPSpanFinished:
    def test_event_type(self):
        ev = _make_adapter().to_sentinel_event({"type": "http_span_finished"})
        assert ev.event_type == "http_span_finished"

    def test_severity(self):
        ev = _make_adapter().to_sentinel_event({"type": "http_span_finished"})
        assert ev.severity == "INFO"

    def test_event_id_prefix(self):
        ev = _make_adapter().to_sentinel_event({"type": "http_span_finished"})
        assert ev.event_id.startswith("dd-http-")

    def test_body_contains_url(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "http_span_finished",
                "http_url": "https://api.openai.com/v1/chat",
            }
        )
        assert "https://api.openai.com/v1/chat" in ev.body

    def test_body_contains_status(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "http_span_finished",
                "http_status_code": "200",
            }
        )
        assert "200" in ev.body

    def test_attributes_http_url(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "http_span_finished",
                "http_url": "https://example.com",
            }
        )
        assert ev.attributes["http_url"] == "https://example.com"

    def test_attributes_http_status_code(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "http_span_finished",
                "http_status_code": "201",
            }
        )
        assert ev.attributes["http_status_code"] == "201"

    def test_attributes_http_method(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "http_span_finished",
                "http_method": "POST",
            }
        )
        assert ev.attributes["http_method"] == "POST"


# ---------------------------------------------------------------------------
# TestFromHTTPSpanError
# ---------------------------------------------------------------------------


class TestFromHTTPSpanError:
    def test_event_type(self):
        ev = _make_adapter().to_sentinel_event({"type": "http_span_error"})
        assert ev.event_type == "http_span_error"

    def test_severity(self):
        ev = _make_adapter().to_sentinel_event({"type": "http_span_error"})
        assert ev.severity == "ERROR"

    def test_event_id_prefix(self):
        ev = _make_adapter().to_sentinel_event({"type": "http_span_error"})
        assert ev.event_id.startswith("dd-http-")

    def test_body_contains_url(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "http_span_error",
                "http_url": "https://api.example.com",
                "error_message": "timeout",
            }
        )
        assert "https://api.example.com" in ev.body

    def test_body_contains_error_message(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "http_span_error",
                "error_message": "connection refused",
            }
        )
        assert "connection refused" in ev.body

    def test_attributes_include_http_attrs(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "http_span_error",
                "http_url": "https://example.com",
                "http_status_code": "503",
                "http_method": "GET",
            }
        )
        assert ev.attributes["http_url"] == "https://example.com"
        assert ev.attributes["http_status_code"] == "503"
        assert ev.attributes["http_method"] == "GET"


# ---------------------------------------------------------------------------
# TestFromDBSpanFinished
# ---------------------------------------------------------------------------


class TestFromDBSpanFinished:
    def test_event_type(self):
        ev = _make_adapter().to_sentinel_event({"type": "db_span_finished"})
        assert ev.event_type == "db_span_finished"

    def test_severity(self):
        ev = _make_adapter().to_sentinel_event({"type": "db_span_finished"})
        assert ev.severity == "INFO"

    def test_event_id_prefix(self):
        ev = _make_adapter().to_sentinel_event({"type": "db_span_finished"})
        assert ev.event_id.startswith("dd-db-")

    def test_body_contains_span_name(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "db_span_finished",
                "span_name": "postgresql.query",
                "component": "psycopg2",
            }
        )
        assert "postgresql.query" in ev.body

    def test_body_contains_component(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "db_span_finished",
                "component": "redis",
            }
        )
        assert "redis" in ev.body

    def test_attributes_component(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "db_span_finished",
                "component": "sqlalchemy",
            }
        )
        assert ev.attributes["component"] == "sqlalchemy"


# ---------------------------------------------------------------------------
# TestFromDBSpanError
# ---------------------------------------------------------------------------


class TestFromDBSpanError:
    def test_event_type(self):
        ev = _make_adapter().to_sentinel_event({"type": "db_span_error"})
        assert ev.event_type == "db_span_error"

    def test_severity(self):
        ev = _make_adapter().to_sentinel_event({"type": "db_span_error"})
        assert ev.severity == "ERROR"

    def test_event_id_prefix(self):
        ev = _make_adapter().to_sentinel_event({"type": "db_span_error"})
        assert ev.event_id.startswith("dd-db-")

    def test_body_contains_error_message(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "db_span_error",
                "span_name": "redis.get",
                "component": "redis",
                "error_message": "key not found",
            }
        )
        assert "key not found" in ev.body

    def test_body_contains_component(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "db_span_error",
                "component": "pymongo",
            }
        )
        assert "pymongo" in ev.body

    def test_attributes_error_message(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "db_span_error",
                "error_message": "duplicate key",
            }
        )
        assert ev.attributes["error_message"] == "duplicate key"


# ---------------------------------------------------------------------------
# TestFromUnknown
# ---------------------------------------------------------------------------


class TestFromUnknown:
    def test_event_type(self):
        ev = _make_adapter().to_sentinel_event({"type": "some_new_thing"})
        assert ev.event_type == "unknown_datadog_event"

    def test_severity(self):
        ev = _make_adapter().to_sentinel_event({"type": "some_new_thing"})
        assert ev.severity == "INFO"

    def test_event_id_prefix(self):
        ev = _make_adapter().to_sentinel_event({"type": "some_new_thing"})
        assert ev.event_id.startswith("dd-unknown-")

    def test_body_contains_original_type(self):
        ev = _make_adapter().to_sentinel_event({"type": "some_new_thing"})
        assert "some_new_thing" in ev.body

    def test_original_type_attribute(self):
        ev = _make_adapter().to_sentinel_event({"type": "some_new_thing"})
        assert ev.attributes["original_type"] == "some_new_thing"

    def test_empty_type_maps_to_unknown(self):
        ev = _make_adapter().to_sentinel_event({})
        assert ev.event_type == "unknown_datadog_event"

    def test_trace_id_from_run_id_on_unknown(self):
        adapter = _make_adapter(run_id="my-run")
        ev = adapter.to_sentinel_event({"type": "weird_event"})
        assert ev.trace_id == "my-run"


# ---------------------------------------------------------------------------
# TestTimestampParsing
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    def test_iso_timestamp_parsed(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "span_finished",
                "timestamp": "2024-03-15T09:00:00+00:00",
            }
        )
        assert ev.timestamp.year == 2024
        assert ev.timestamp.month == 3
        assert ev.timestamp.day == 15

    def test_z_suffix_timestamp_parsed(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "span_finished",
                "timestamp": "2024-07-04T12:00:00Z",
            }
        )
        assert ev.timestamp.year == 2024
        assert ev.timestamp.month == 7

    def test_invalid_timestamp_falls_back_to_now(self):
        ev = _make_adapter().to_sentinel_event(
            {
                "type": "span_finished",
                "timestamp": "not-a-date",
            }
        )
        assert isinstance(ev.timestamp, datetime)

    def test_missing_timestamp_falls_back_to_now(self):
        ev = _make_adapter().to_sentinel_event({"type": "span_finished"})
        assert isinstance(ev.timestamp, datetime)


# ---------------------------------------------------------------------------
# TestDrainFlush
# ---------------------------------------------------------------------------


class TestDrainFlush:
    def test_drain_returns_events(self):
        adapter = _make_adapter()
        adapter._buffer_event(adapter.to_sentinel_event({"type": "span_finished"}))
        assert len(adapter.drain()) == 1

    def test_drain_clears_buffer(self):
        adapter = _make_adapter()
        adapter._buffer_event(adapter.to_sentinel_event({"type": "span_finished"}))
        adapter.drain()
        assert adapter.drain() == []

    def test_flush_into_ingest(self):
        adapter = _make_adapter()
        sentinel = Sentinel()
        adapter._buffer_event(adapter.to_sentinel_event({"type": "span_finished"}))
        adapter.flush_into(sentinel)
        assert isinstance(sentinel.detect_violations(), list)

    def test_flush_into_clears_buffer(self):
        adapter = _make_adapter()
        sentinel = Sentinel()
        adapter._buffer_event(adapter.to_sentinel_event({"type": "span_finished"}))
        adapter.flush_into(sentinel)
        assert adapter.drain() == []

    def test_multiple_events_drained_in_order(self):
        adapter = _make_adapter()
        for t in ["span_finished", "llm_span_finished", "http_span_finished"]:
            adapter._buffer_event(adapter.to_sentinel_event({"type": t}))
        events = adapter.drain()
        assert len(events) == 3
        assert events[0].event_type == "span_finished"
        assert events[1].event_type == "llm_span_finished"
        assert events[2].event_type == "http_span_finished"


# ---------------------------------------------------------------------------
# TestBufferThreadSafety
# ---------------------------------------------------------------------------


class TestBufferThreadSafety:
    def test_concurrent_buffer_writes(self):
        adapter = _make_adapter()
        errors = []

        def worker():
            try:
                for _ in range(50):
                    adapter._buffer_event(adapter.to_sentinel_event({"type": "span_finished"}))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(adapter.drain()) == 500

    def test_concurrent_drains_no_duplication(self):
        adapter = _make_adapter()
        for _ in range(100):
            adapter._buffer_event(adapter.to_sentinel_event({"type": "span_finished"}))

        collected = []
        lock = threading.Lock()

        def drainer():
            evs = adapter.drain()
            with lock:
                collected.extend(evs)

        threads = [threading.Thread(target=drainer) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(collected) == 100


# ---------------------------------------------------------------------------
# TestSetup
# ---------------------------------------------------------------------------


class TestSetup:
    def test_writer_write_is_replaced(self):
        adapter, tracer = _setup_adapter()
        # The write attribute should now be the interceptor, not the original mock
        # It's still callable, just a different function
        assert callable(tracer._writer.write)

    def test_original_write_called(self):
        adapter = _make_adapter()
        tracer = _make_mock_tracer()
        original_write = tracer._writer.write
        adapter.setup(tracer)
        _write(tracer, [])
        original_write.assert_called_once()

    def test_empty_span_list_does_not_crash(self):
        adapter, tracer = _setup_adapter()
        _write(tracer, [])
        assert adapter.drain() == []

    def test_none_span_list_does_not_crash(self):
        adapter, tracer = _setup_adapter()
        _write(tracer, None)
        assert adapter.drain() == []

    def test_generic_span_is_buffered(self):
        adapter, tracer = _setup_adapter()
        span = _make_span(name="web.request", component="")
        _write(tracer, [span])
        events = adapter.drain()
        assert len(events) == 1
        assert events[0].event_type == "span_finished"

    def test_llm_span_is_buffered(self):
        adapter, tracer = _setup_adapter()
        span = _make_span(name="openai.request", component="openai")
        _write(tracer, [span])
        events = adapter.drain()
        assert any(e.event_type == "llm_span_finished" for e in events)

    def test_http_span_is_buffered(self):
        adapter, tracer = _setup_adapter()
        span = _make_span(name="requests.get", component="requests")
        _write(tracer, [span])
        events = adapter.drain()
        assert any(e.event_type == "http_span_finished" for e in events)

    def test_db_span_is_buffered(self):
        adapter, tracer = _setup_adapter()
        span = _make_span(name="sqlalchemy.query", component="sqlalchemy")
        _write(tracer, [span])
        events = adapter.drain()
        assert any(e.event_type == "db_span_finished" for e in events)

    def test_error_span_buffers_error_event(self):
        adapter, tracer = _setup_adapter()
        span = _make_span(
            name="web.request", component="", error=1, tags={"error.message": "boom"}
        )
        _write(tracer, [span])
        events = adapter.drain()
        assert any(e.event_type == "span_error" for e in events)

    def test_multiple_spans_in_trace(self):
        adapter, tracer = _setup_adapter()
        spans = [
            _make_span(name="web.request", component=""),
            _make_span(name="openai.request", component="openai"),
            _make_span(name="sqlalchemy.query", component="sqlalchemy"),
        ]
        _write(tracer, spans)
        events = adapter.drain()
        assert len(events) == 3
        types = {e.event_type for e in events}
        assert "span_finished" in types
        assert "llm_span_finished" in types
        assert "db_span_finished" in types

    def test_bad_span_does_not_stop_processing(self):
        """An exception converting one span must not prevent others from buffering."""
        adapter, tracer = _setup_adapter()
        bad = MagicMock()
        bad.name = None
        bad.error = "not-an-int"  # will trigger bool() but won't crash
        bad.get_tag = MagicMock(side_effect=RuntimeError("boom"))
        bad.get_metric = MagicMock(return_value=None)
        good = _make_span(name="ok-span", component="")
        _write(tracer, [bad, good])
        events = adapter.drain()
        # At least the good span should be buffered (bad one is silently skipped)
        assert any(e.event_type in ("span_finished", "span_error") for e in events)

    def test_run_id_propagated_as_trace_id(self):
        adapter, tracer = _setup_adapter(run_id="session-42")
        span = _make_span(name="web.request")
        _write(tracer, [span])
        events = adapter.drain()
        assert all(e.trace_id == "session-42" for e in events)

    def test_writer_fallback_to_writer_attr(self):
        """Falls back to tracer.writer if tracer._writer is absent."""
        adapter = _make_adapter()
        tracer = MagicMock()
        del tracer._writer  # remove _writer
        tracer.writer = MagicMock()
        tracer.writer.write = MagicMock(return_value=None)
        adapter.setup(tracer)
        # After setup, interceptor is on tracer.writer.write (not _writer)
        tracer.writer.write([])  # must not raise

    def test_no_writer_raises_runtime_error(self):
        adapter = _make_adapter()
        tracer = MagicMock(spec=[])  # no _writer, no writer
        with pytest.raises(RuntimeError, match="writer"):
            adapter.setup(tracer)


# ---------------------------------------------------------------------------
# TestSpanToRaw
# ---------------------------------------------------------------------------


class TestSpanToRaw:
    def _raw(self, **kwargs):
        from agentcop.adapters.datadog import _span_to_raw

        return _span_to_raw(_make_span(**kwargs))

    def test_generic_span_type(self):
        assert self._raw(component="")["type"] == "span_finished"

    def test_generic_span_error_type(self):
        assert self._raw(component="", error=1)["type"] == "span_error"

    def test_openai_component_llm_finished(self):
        assert self._raw(component="openai")["type"] == "llm_span_finished"

    def test_anthropic_component_llm_finished(self):
        assert self._raw(component="anthropic")["type"] == "llm_span_finished"

    def test_langchain_component_llm_finished(self):
        assert self._raw(component="langchain")["type"] == "llm_span_finished"

    def test_llm_error_type(self):
        assert self._raw(component="openai", error=1)["type"] == "llm_span_error"

    def test_requests_component_http_finished(self):
        assert self._raw(component="requests")["type"] == "http_span_finished"

    def test_httpx_component_http_finished(self):
        assert self._raw(component="httpx")["type"] == "http_span_finished"

    def test_http_error_type(self):
        assert self._raw(component="requests", error=1)["type"] == "http_span_error"

    def test_sqlalchemy_component_db_finished(self):
        assert self._raw(component="sqlalchemy")["type"] == "db_span_finished"

    def test_redis_component_db_finished(self):
        assert self._raw(component="redis")["type"] == "db_span_finished"

    def test_db_error_type(self):
        assert self._raw(component="sqlalchemy", error=1)["type"] == "db_span_error"

    def test_trace_id_formatted_as_hex(self):
        raw = self._raw(trace_id=0xABCD1234)
        assert raw["dd_trace_id"] == format(0xABCD1234, "016x")

    def test_span_id_formatted_as_hex(self):
        raw = self._raw(span_id=0xDEADBEEF)
        assert raw["dd_span_id"] == format(0xDEADBEEF, "016x")

    def test_parent_id_formatted_as_hex(self):
        raw = self._raw(parent_id=0x12345678)
        assert raw["dd_parent_id"] == format(0x12345678, "016x")

    def test_zero_parent_id_empty_string(self):
        raw = self._raw(parent_id=0)
        assert raw["dd_parent_id"] == ""

    def test_timestamp_from_start_ns(self):
        # 1_700_000_000_000_000_000 ns = 2023-11-14 22:13:20 UTC
        raw = self._raw(start_ns=1_700_000_000_000_000_000)
        assert raw["timestamp"] is not None
        assert "2023" in raw["timestamp"]

    def test_timestamp_from_start_float(self):
        from agentcop.adapters.datadog import _span_to_raw

        span = _make_span(start_ns=None)  # start_ns=None triggers fallback
        span.start = 1_700_000_000.0  # float seconds
        raw = _span_to_raw(span)
        assert raw["timestamp"] is not None
        assert "2023" in raw["timestamp"]

    def test_llm_model_from_ai_model_name(self):
        raw = self._raw(component="openai", tags={"ai.model.name": "gpt-4o"})
        assert raw["model"] == "gpt-4o"

    def test_llm_model_from_openai_tag(self):
        raw = self._raw(component="openai", tags={"openai.request.model": "gpt-4o-mini"})
        assert raw["model"] == "gpt-4o-mini"

    def test_llm_model_from_langchain_tag(self):
        raw = self._raw(
            component="langchain", tags={"langchain.request.llm.model_name": "gpt-3.5-turbo"}
        )
        assert raw["model"] == "gpt-3.5-turbo"

    def test_llm_usage_from_metrics(self):
        raw = self._raw(
            component="openai",
            metrics={
                "llm.usage.prompt_tokens": 10,
                "llm.usage.completion_tokens": 20,
                "llm.usage.total_tokens": 30,
            },
        )
        assert raw["usage"]["prompt_tokens"] == 10
        assert raw["usage"]["completion_tokens"] == 20
        assert raw["usage"]["total_tokens"] == 30

    def test_llm_usage_falls_back_to_openai_metrics(self):
        raw = self._raw(
            component="openai",
            metrics={"openai.response.usage.prompt_tokens": 5},
        )
        assert raw["usage"]["prompt_tokens"] == 5

    def test_http_url_from_tag(self):
        raw = self._raw(component="requests", tags={"http.url": "https://api.openai.com/v1"})
        assert raw["http_url"] == "https://api.openai.com/v1"

    def test_http_status_from_tag(self):
        raw = self._raw(component="requests", tags={"http.status_code": "200"})
        assert raw["http_status_code"] == "200"

    def test_error_message_from_tag(self):
        raw = self._raw(error=1, tags={"error.message": "connection refused"})
        assert raw["error_message"] == "connection refused"

    def test_duration_stored(self):
        raw = self._raw(duration=5_000_000)
        assert raw["duration_ns"] == 5_000_000

    def test_non_llm_no_usage_key(self):
        raw = self._raw(component="")
        assert "usage" not in raw

    def test_service_stored(self):
        raw = self._raw(service="my-agent")
        assert raw["service"] == "my-agent"


# ---------------------------------------------------------------------------
# TestGetTag
# ---------------------------------------------------------------------------


class TestGetTag:
    def test_returns_tag_value(self):
        from agentcop.adapters.datadog import _get_tag

        span = _make_span(tags={"foo": "bar"})
        assert _get_tag(span, "foo") == "bar"

    def test_returns_default_when_missing(self):
        from agentcop.adapters.datadog import _get_tag

        span = _make_span()
        assert _get_tag(span, "nonexistent") == ""

    def test_returns_default_when_none(self):
        from agentcop.adapters.datadog import _get_tag

        span = _make_span(tags={"key": None})
        assert _get_tag(span, "key") == ""

    def test_swallows_exception(self):
        from agentcop.adapters.datadog import _get_tag

        span = MagicMock()
        span.get_tag = MagicMock(side_effect=RuntimeError("broken"))
        assert _get_tag(span, "key") == ""


# ---------------------------------------------------------------------------
# TestGetNumeric
# ---------------------------------------------------------------------------


class TestGetNumeric:
    def test_returns_metric_value(self):
        from agentcop.adapters.datadog import _get_numeric

        span = _make_span(metrics={"my.metric": 42.0})
        assert _get_numeric(span, "my.metric") == 42.0

    def test_falls_back_to_tag(self):
        from agentcop.adapters.datadog import _get_numeric

        span = _make_span(tags={"my.metric": "7"})
        assert _get_numeric(span, "my.metric") == 7.0

    def test_returns_none_when_all_fail(self):
        from agentcop.adapters.datadog import _get_numeric

        span = _make_span()
        assert _get_numeric(span, "nonexistent") is None

    def test_tries_keys_in_order(self):
        from agentcop.adapters.datadog import _get_numeric

        span = _make_span(metrics={"second": 99.0})
        # first key missing, second key present
        assert _get_numeric(span, "first", "second") == 99.0

    def test_swallows_exception(self):
        from agentcop.adapters.datadog import _get_numeric

        span = MagicMock()
        span.get_metric = MagicMock(side_effect=RuntimeError("boom"))
        span.get_tag = MagicMock(side_effect=RuntimeError("boom"))
        assert _get_numeric(span, "key") is None


# ---------------------------------------------------------------------------
# TestNsToIso
# ---------------------------------------------------------------------------


class TestNsToIso:
    def test_converts_nanoseconds(self):
        from agentcop.adapters.datadog import _ns_to_iso

        iso = _ns_to_iso(1_700_000_000_000_000_000)
        assert iso is not None
        assert "2023" in iso

    def test_zero_returns_none(self):
        from agentcop.adapters.datadog import _ns_to_iso

        assert _ns_to_iso(0) is None

    def test_none_returns_none(self):
        from agentcop.adapters.datadog import _ns_to_iso

        assert _ns_to_iso(None) is None


# ---------------------------------------------------------------------------
# TestProtocolConformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_is_sentinel_adapter(self):
        from agentcop.adapters.base import SentinelAdapter

        assert isinstance(_make_adapter(), SentinelAdapter)

    def test_has_source_system_str(self):
        adapter = _make_adapter()
        assert isinstance(adapter.source_system, str)

    def test_to_sentinel_event_callable(self):
        assert callable(_make_adapter().to_sentinel_event)

    def test_to_sentinel_event_returns_sentinel_event(self):
        result = _make_adapter().to_sentinel_event({"type": "span_finished"})
        assert isinstance(result, SentinelEvent)


# ---------------------------------------------------------------------------
# TestSentinelIntegration
# ---------------------------------------------------------------------------


class TestSentinelIntegration:
    def test_flush_into_ingests_all_events(self):
        adapter, tracer = _setup_adapter(run_id="test-run")
        spans = [
            _make_span(name="web.request", component=""),
            _make_span(name="openai.request", component="openai"),
        ]
        _write(tracer, spans)
        sentinel = Sentinel()
        adapter.flush_into(sentinel)
        assert len(sentinel._events) == 2

    def test_custom_detector_fires_on_llm_error(self):
        def detect_llm_failure(event: SentinelEvent):
            if event.event_type != "llm_span_error":
                return None
            return ViolationRecord(
                violation_type="llm_call_failed",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={"model": event.attributes.get("model", "unknown")},
            )

        adapter, tracer = _setup_adapter(run_id="ci-run")
        span = _make_span(
            name="openai.request",
            component="openai",
            error=1,
            tags={
                "ai.model.name": "gpt-4o",
                "error.message": "rate limit exceeded",
            },
        )
        _write(tracer, [span])
        sentinel = Sentinel(detectors=[detect_llm_failure])
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "llm_call_failed"
        assert violations[0].detail["model"] == "gpt-4o"

    def test_custom_detector_fires_on_http_5xx(self):
        def detect_http_5xx(event: SentinelEvent):
            if event.event_type not in ("http_span_finished", "http_span_error"):
                return None
            status = event.attributes.get("http_status_code", "")
            if not str(status).startswith("5"):
                return None
            return ViolationRecord(
                violation_type="http_5xx",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={"status": status},
            )

        adapter, tracer = _setup_adapter(run_id="ci-run")
        span = _make_span(
            name="requests.get",
            component="requests",
            tags={"http.status_code": "503", "http.url": "https://example.com"},
        )
        _write(tracer, [span])
        sentinel = Sentinel(detectors=[detect_http_5xx])
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "http_5xx"

    def test_no_violations_on_clean_run(self):
        def detect_errors(event: SentinelEvent):
            if "error" not in event.event_type:
                return None
            return ViolationRecord(
                violation_type="span_error",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={},
            )

        adapter, tracer = _setup_adapter()
        spans = [
            _make_span(name="web.request", component=""),
            _make_span(name="openai.request", component="openai"),
        ]
        _write(tracer, spans)
        sentinel = Sentinel(detectors=[detect_errors])
        adapter.flush_into(sentinel)
        assert sentinel.detect_violations() == []

    def test_multiple_traces(self):
        adapter, tracer = _setup_adapter(run_id="multi")
        for _ in range(3):
            _write(tracer, [_make_span(name="web.request", component="")])
        events = adapter.drain()
        assert len(events) == 3
        assert all(e.trace_id == "multi" for e in events)


# ---------------------------------------------------------------------------
# Runtime security tests
# ---------------------------------------------------------------------------


def _make_datadog_runtime(gate=None, permissions=None, sandbox=None, approvals=None):
    with patch("agentcop.adapters.datadog._require_ddtrace"):
        from agentcop.adapters.datadog import DatadogSentinelAdapter

        return DatadogSentinelAdapter(
            run_id="rt-run",
            gate=gate,
            permissions=permissions,
            sandbox=sandbox,
            approvals=approvals,
        )


class TestRuntimeSecurityDatadog:
    def test_init_stores_none_by_default(self):
        a = _make_datadog_runtime()
        assert a._gate is None
        assert a._permissions is None

    def test_init_stores_runtime_params(self):
        gate = MagicMock()
        perms = MagicMock()
        a = _make_datadog_runtime(gate=gate, permissions=perms)
        assert a._gate is gate
        assert a._permissions is perms

    def test_gate_denial_fires_event(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=False, reason="blocked", risk_score=90)
        a = _make_datadog_runtime(gate=gate)
        from agentcop.adapters._runtime import check_tool_call

        with pytest.raises(PermissionError):
            check_tool_call(a, "openai.request", {})
        events = a.drain()
        assert any(e.event_type == "gate_denied" for e in events)

    def test_permission_violation_fires_event(self):
        perms = MagicMock()
        perms.verify.return_value = MagicMock(granted=False, reason="denied")
        a = _make_datadog_runtime(permissions=perms)
        from agentcop.adapters._runtime import check_tool_call

        with pytest.raises(PermissionError):
            check_tool_call(a, "anthropic.request", {})
        events = a.drain()
        assert any(e.event_type == "permission_violation" for e in events)

    def test_no_gate_backward_compatible(self):
        a = _make_datadog_runtime()
        event = a.to_sentinel_event({"type": "llm_span_finished", "span_name": "openai.chat"})
        assert event.event_type == "llm_span_finished"


# ---------------------------------------------------------------------------
# Trust integration
# ---------------------------------------------------------------------------


def _make_adapter_trust(**kwargs):
    with patch("agentcop.adapters.datadog._require_ddtrace"):
        from agentcop.adapters.datadog import DatadogSentinelAdapter

        return DatadogSentinelAdapter(**kwargs)


class TestTrustIntegration:
    def test_accepts_trust_observer_param(self):
        obs = MagicMock()
        a = _make_adapter_trust(trust_observer=obs)
        assert a._trust_observer is obs

    def test_accepts_hierarchy_param(self):
        hierarchy = MagicMock()
        a = _make_adapter_trust(hierarchy=hierarchy)
        assert a._hierarchy is hierarchy

    def test_no_trust_observer_defaults_to_none(self):
        a = _make_adapter_trust()
        assert a._trust_observer is None

    def test_span_write_calls_record_verified_chain(self):
        obs = MagicMock()
        a = _make_adapter_trust(trust_observer=obs)
        tracer = _make_mock_tracer()
        a.setup(tracer=tracer)
        span = _make_span(error=0)
        _write(tracer, [span])
        obs.record_verified_chain.assert_called()

    def test_error_span_does_not_call_record_verified_chain(self):
        """Spans with type ending in _error should not trigger record_verified_chain."""
        obs = MagicMock()
        a = _make_adapter_trust(trust_observer=obs)
        tracer = _make_mock_tracer()
        a.setup(tracer=tracer)
        span = _make_span(error=1)
        _write(tracer, [span])
        obs.record_verified_chain.assert_not_called()
