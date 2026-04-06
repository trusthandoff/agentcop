"""
Tests for src/agentcop/adapters/langfuse.py

All tests mock the langfuse import guard so langfuse does not need to be
installed in the test environment. SpanProcessor tests build lightweight
mock OTel spans and call on_start / on_end directly.
"""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentcop import Sentinel, ViolationRecord
from agentcop.event import SentinelEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(run_id=None):
    """Return a LangfuseSentinelAdapter with the import guard bypassed."""
    with patch("agentcop.adapters.langfuse._require_langfuse"):
        from agentcop.adapters.langfuse import LangfuseSentinelAdapter

        return LangfuseSentinelAdapter(run_id=run_id)


def _mock_langfuse_modules():
    """Return a sys.modules patch dict that satisfies setup()'s imports."""
    mock_lf = MagicMock()
    mock_otel_sdk = MagicMock()
    # SpanProcessor base class — we just need it importable
    mock_otel_sdk.SpanProcessor = object
    return {
        "langfuse": mock_lf,
        "opentelemetry": MagicMock(),
        "opentelemetry.sdk": MagicMock(),
        "opentelemetry.sdk.trace": mock_otel_sdk,
    }


def _make_mock_client():
    """Build a mock Langfuse client whose tracer_provider is accessible."""
    client = MagicMock()
    mock_tp = MagicMock()
    client._resources.tracer_provider = mock_tp
    return client, mock_tp


def _make_span(
    obs_type="span",
    name="my-op",
    level=None,
    status_code_value=0,  # 0=UNSET, 1=OK, 2=ERROR
    trace_id_int=0xABCD1234ABCD1234ABCD1234ABCD1234,
    span_id_int=0xDEADBEEFDEADBEEF,
    parent_span_id_int=None,
    start_ns=None,
    end_ns=None,
    extra_attrs=None,
):
    """Build a minimal mock OTel span with Langfuse attributes."""
    attrs: dict[str, Any] = {
        "langfuse.observation.type": obs_type,
    }
    if level:
        attrs["langfuse.observation.level"] = level
    if extra_attrs:
        attrs.update(extra_attrs)

    span = MagicMock()
    span.name = name
    span.attributes = attrs

    # Context (trace_id + span_id)
    span._context = MagicMock()
    span._context.trace_id = trace_id_int
    span._context.span_id = span_id_int

    # Parent
    if parent_span_id_int is not None:
        span.parent = MagicMock()
        span.parent.span_id = parent_span_id_int
    else:
        span.parent = None

    # Timing
    span._start_time = start_ns
    span._end_time = end_ns

    # Status
    span._status = MagicMock()
    span._status.status_code = MagicMock()
    span._status.status_code.value = status_code_value
    span._status.description = ""

    return span


def _get_observer(mock_tp):
    """Extract the _LangfuseObserver registered on the mock tracer_provider."""
    return mock_tp.add_span_processor.call_args[0][0]


# ---------------------------------------------------------------------------
# TestRequireLangfuse
# ---------------------------------------------------------------------------


class TestRequireLangfuse:
    def test_raises_when_langfuse_missing(self):
        with patch.dict("sys.modules", {"langfuse": None}):
            if "agentcop.adapters.langfuse" in sys.modules:
                del sys.modules["agentcop.adapters.langfuse"]
            from agentcop.adapters.langfuse import _require_langfuse

            with pytest.raises(ImportError, match="langfuse"):
                _require_langfuse()

    def test_does_not_raise_when_langfuse_present(self):
        mock_lf = MagicMock()
        with patch.dict("sys.modules", {"langfuse": mock_lf}):
            from agentcop.adapters.langfuse import _require_langfuse

            _require_langfuse()  # no exception

    def test_constructor_calls_require(self):
        called = []

        def fake_require():
            called.append(True)

        with patch("agentcop.adapters.langfuse._require_langfuse", fake_require):
            from agentcop.adapters.langfuse import LangfuseSentinelAdapter

            LangfuseSentinelAdapter()
        assert called == [True]


# ---------------------------------------------------------------------------
# TestAdapterInit
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_source_system(self):
        assert _make_adapter().source_system == "langfuse"

    def test_run_id_none_by_default(self):
        assert _make_adapter()._run_id is None

    def test_run_id_stored(self):
        assert _make_adapter(run_id="sess-99")._run_id == "sess-99"

    def test_buffer_starts_empty(self):
        assert _make_adapter().drain() == []


# ---------------------------------------------------------------------------
# TestFromObservationStarted
# ---------------------------------------------------------------------------


class TestFromObservationStarted:
    def setup_method(self):
        self.adapter = _make_adapter(run_id="trace-started")

    def _ev(self, **kwargs):
        base = {
            "type": "observation_started",
            "observation_type": "span",
            "observation_name": "op",
        }
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_event_type(self):
        assert self._ev().event_type == "observation_started"

    def test_severity_info(self):
        assert self._ev().severity == "INFO"

    def test_source_system(self):
        assert self._ev().source_system == "langfuse"

    def test_trace_id_uses_run_id(self):
        assert self._ev().trace_id == "trace-started"

    def test_trace_id_falls_back_to_langfuse_trace_id(self):
        adapter = _make_adapter()  # no run_id
        ev = adapter.to_sentinel_event(
            {
                "type": "observation_started",
                "langfuse_trace_id": "aabbcc",
            }
        )
        assert ev.trace_id == "aabbcc"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("langfuse-obs-")

    def test_observation_type_in_attributes(self):
        ev = self._ev(observation_type="generation")
        assert ev.attributes["observation_type"] == "generation"

    def test_observation_name_in_attributes(self):
        ev = self._ev(observation_name="gpt-call")
        assert ev.attributes["observation_name"] == "gpt-call"

    def test_observation_id_in_attributes(self):
        ev = self._ev(observation_id="abc123")
        assert ev.attributes["observation_id"] == "abc123"

    def test_parent_observation_id_in_attributes(self):
        ev = self._ev(parent_observation_id="parent-id")
        assert ev.attributes["parent_observation_id"] == "parent-id"

    def test_user_id_in_attributes(self):
        ev = self._ev(user_id="user-42")
        assert ev.attributes["user_id"] == "user-42"

    def test_session_id_in_attributes(self):
        ev = self._ev(session_id="session-7")
        assert ev.attributes["session_id"] == "session-7"

    def test_body_contains_type_and_name(self):
        ev = self._ev(observation_type="agent", observation_name="planner")
        assert "agent" in ev.body and "planner" in ev.body

    def test_unique_event_ids(self):
        assert self._ev().event_id != self._ev().event_id


# ---------------------------------------------------------------------------
# TestFromSpanEvents
# ---------------------------------------------------------------------------


class TestFromSpanEvents:
    def setup_method(self):
        self.adapter = _make_adapter(run_id="trace-span")

    def _ev(self, type_="span_finished", **kwargs):
        base = {"type": type_, "observation_type": "span", "observation_name": "my-span"}
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_span_finished_event_type(self):
        assert self._ev().event_type == "span_finished"

    def test_span_finished_severity(self):
        assert self._ev().severity == "INFO"

    def test_span_error_event_type(self):
        assert self._ev("span_error").event_type == "span_error"

    def test_span_error_severity(self):
        assert self._ev("span_error").severity == "ERROR"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("langfuse-span-")

    def test_error_event_id_prefix(self):
        assert self._ev("span_error").event_id.startswith("langfuse-span-")

    def test_observation_type_agent(self):
        ev = self._ev(observation_type="agent")
        assert ev.attributes["observation_type"] == "agent"

    def test_observation_type_chain(self):
        ev = self._ev(observation_type="chain")
        assert ev.attributes["observation_type"] == "chain"

    def test_status_message_captured(self):
        ev = self._ev("span_error", status_message="db connection failed")
        assert ev.attributes["status_message"] == "db connection failed"

    def test_status_message_in_body(self):
        ev = self._ev("span_error", status_message="timeout")
        assert "timeout" in ev.body

    def test_input_captured(self):
        ev = self._ev(input="hello world")
        assert ev.attributes["input"] == "hello world"

    def test_output_captured(self):
        ev = self._ev(output="processed result")
        assert ev.attributes["output"] == "processed result"

    def test_level_captured(self):
        ev = self._ev(level="WARNING")
        assert ev.attributes["level"] == "WARNING"

    def test_langfuse_trace_id_in_attributes(self):
        ev = self._ev(langfuse_trace_id="deadbeef" * 4)
        assert ev.attributes["langfuse_trace_id"] == "deadbeef" * 4

    def test_trace_id_uses_run_id(self):
        assert self._ev().trace_id == "trace-span"


# ---------------------------------------------------------------------------
# TestFromGenerationEvents
# ---------------------------------------------------------------------------


class TestFromGenerationEvents:
    def setup_method(self):
        self.adapter = _make_adapter(run_id="trace-gen")

    def _ev(self, type_="generation_finished", **kwargs):
        base = {
            "type": type_,
            "observation_type": "generation",
            "observation_name": "gpt-call",
            "model": "gpt-4o",
        }
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_generation_finished_event_type(self):
        assert self._ev().event_type == "generation_finished"

    def test_generation_finished_severity(self):
        assert self._ev().severity == "INFO"

    def test_generation_error_event_type(self):
        assert self._ev("generation_error").event_type == "generation_error"

    def test_generation_error_severity(self):
        assert self._ev("generation_error").severity == "ERROR"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("langfuse-gen-")

    def test_model_in_attributes(self):
        ev = self._ev(model="claude-3-opus")
        assert ev.attributes["model"] == "claude-3-opus"

    def test_model_in_body(self):
        ev = self._ev(model="gpt-4o-mini")
        assert "gpt-4o-mini" in ev.body

    def test_usage_captured(self):
        ev = self._ev(usage={"prompt_tokens": 10, "completion_tokens": 20})
        assert ev.attributes["usage"]["prompt_tokens"] == 10
        assert ev.attributes["usage"]["completion_tokens"] == 20

    def test_usage_empty_by_default(self):
        assert self._ev().attributes["usage"] == {}

    def test_cost_captured(self):
        ev = self._ev(cost={"total_cost": 0.0023})
        assert ev.attributes["cost"]["total_cost"] == 0.0023

    def test_cost_empty_by_default(self):
        assert self._ev().attributes["cost"] == {}

    def test_prompt_name_captured(self):
        ev = self._ev(prompt_name="summarize-v2")
        assert ev.attributes["prompt_name"] == "summarize-v2"

    def test_prompt_version_captured(self):
        ev = self._ev(prompt_version="3")
        assert ev.attributes["prompt_version"] == "3"

    def test_generation_error_status_message_in_body(self):
        ev = self._ev("generation_error", status_message="rate limit")
        assert "rate limit" in ev.body

    def test_embedding_type(self):
        ev = self._ev(observation_type="embedding")
        assert ev.attributes["observation_type"] == "embedding"

    def test_trace_id_uses_run_id(self):
        assert self._ev().trace_id == "trace-gen"


# ---------------------------------------------------------------------------
# TestFromToolEvents
# ---------------------------------------------------------------------------


class TestFromToolEvents:
    def setup_method(self):
        self.adapter = _make_adapter()

    def _ev(self, type_="tool_finished", **kwargs):
        base = {"type": type_, "observation_type": "tool", "observation_name": "web-search"}
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_tool_finished_event_type(self):
        assert self._ev().event_type == "tool_finished"

    def test_tool_finished_severity(self):
        assert self._ev().severity == "INFO"

    def test_tool_error_event_type(self):
        assert self._ev("tool_error").event_type == "tool_error"

    def test_tool_error_severity(self):
        assert self._ev("tool_error").severity == "ERROR"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("langfuse-tool-")

    def test_observation_name_in_attributes(self):
        ev = self._ev(observation_name="code-exec")
        assert ev.attributes["observation_name"] == "code-exec"

    def test_status_message_captured(self):
        ev = self._ev("tool_error", status_message="exec failed")
        assert ev.attributes["status_message"] == "exec failed"

    def test_body_contains_name(self):
        ev = self._ev(observation_name="my-tool")
        assert "my-tool" in ev.body


# ---------------------------------------------------------------------------
# TestFromRetrieverEvents
# ---------------------------------------------------------------------------


class TestFromRetrieverEvents:
    def setup_method(self):
        self.adapter = _make_adapter()

    def _ev(self, type_="retriever_finished", **kwargs):
        base = {
            "type": type_,
            "observation_type": "retriever",
            "observation_name": "vector-search",
        }
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_retriever_finished_event_type(self):
        assert self._ev().event_type == "retriever_finished"

    def test_retriever_finished_severity(self):
        assert self._ev().severity == "INFO"

    def test_retriever_error_severity(self):
        assert self._ev("retriever_error").severity == "ERROR"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("langfuse-retriever-")

    def test_input_captured(self):
        ev = self._ev(input="what is RAG?")
        assert ev.attributes["input"] == "what is RAG?"

    def test_output_captured(self):
        ev = self._ev(output="[doc1, doc2]")
        assert ev.attributes["output"] == "[doc1, doc2]"


# ---------------------------------------------------------------------------
# TestFromEventOccurred
# ---------------------------------------------------------------------------


class TestFromEventOccurred:
    def setup_method(self):
        self.adapter = _make_adapter()

    def _ev(self, **kwargs):
        base = {
            "type": "event_occurred",
            "observation_type": "event",
            "observation_name": "cache-hit",
        }
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_event_type(self):
        assert self._ev().event_type == "event_occurred"

    def test_severity_info(self):
        assert self._ev().severity == "INFO"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("langfuse-event-")

    def test_body_contains_name(self):
        ev = self._ev(observation_name="cache-miss")
        assert "cache-miss" in ev.body


# ---------------------------------------------------------------------------
# TestFromGuardrailEvents
# ---------------------------------------------------------------------------


class TestFromGuardrailEvents:
    def setup_method(self):
        self.adapter = _make_adapter()

    def _ev(self, type_="guardrail_finished", **kwargs):
        base = {
            "type": type_,
            "observation_type": "guardrail",
            "observation_name": "content-filter",
        }
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_guardrail_finished_event_type(self):
        assert self._ev().event_type == "guardrail_finished"

    def test_guardrail_finished_severity(self):
        assert self._ev().severity == "INFO"

    def test_guardrail_error_event_type(self):
        assert self._ev("guardrail_error").event_type == "guardrail_error"

    def test_guardrail_error_severity(self):
        assert self._ev("guardrail_error").severity == "ERROR"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("langfuse-guardrail-")

    def test_status_message_in_body_on_error(self):
        ev = self._ev("guardrail_error", status_message="blocked content")
        assert "blocked content" in ev.body


# ---------------------------------------------------------------------------
# TestFromUnknown
# ---------------------------------------------------------------------------


class TestFromUnknown:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_unknown_type_maps_to_unknown_event(self):
        ev = self.adapter.to_sentinel_event({"type": "weird_thing"})
        assert ev.event_type == "unknown_langfuse_event"

    def test_severity_info(self):
        ev = self.adapter.to_sentinel_event({"type": "weird_thing"})
        assert ev.severity == "INFO"

    def test_original_type_preserved(self):
        ev = self.adapter.to_sentinel_event({"type": "my_custom"})
        assert ev.attributes["original_type"] == "my_custom"

    def test_empty_dict_produces_unknown(self):
        ev = self.adapter.to_sentinel_event({})
        assert ev.event_type == "unknown_langfuse_event"

    def test_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "x"})
        assert ev.event_id.startswith("langfuse-unknown-")

    def test_body_mentions_type(self):
        ev = self.adapter.to_sentinel_event({"type": "exotic_event"})
        assert "exotic_event" in ev.body


# ---------------------------------------------------------------------------
# TestTimestampParsing
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_parses_iso_timestamp(self):
        ev = self.adapter.to_sentinel_event(
            {
                "type": "span_finished",
                "timestamp": "2026-04-01T10:00:00Z",
            }
        )
        assert ev.timestamp.year == 2026

    def test_parses_timestamp_with_offset(self):
        ev = self.adapter.to_sentinel_event(
            {
                "type": "span_finished",
                "timestamp": "2026-04-01T10:00:00+00:00",
            }
        )
        assert ev.timestamp.year == 2026

    def test_falls_back_to_now_on_invalid_timestamp(self):
        before = datetime.now(UTC)
        ev = self.adapter.to_sentinel_event({"type": "span_finished", "timestamp": "bad"})
        after = datetime.now(UTC)
        assert before <= ev.timestamp <= after

    def test_falls_back_to_now_when_no_timestamp(self):
        before = datetime.now(UTC)
        ev = self.adapter.to_sentinel_event({"type": "span_finished"})
        after = datetime.now(UTC)
        assert before <= ev.timestamp <= after


# ---------------------------------------------------------------------------
# TestDrainFlush
# ---------------------------------------------------------------------------


class TestDrainFlush:
    def setup_method(self):
        self.adapter = _make_adapter(run_id="drain-test")

    def _push(self, event_type):
        ev = self.adapter.to_sentinel_event({"type": event_type})
        self.adapter._buffer_event(ev)

    def test_drain_returns_all_events(self):
        self._push("observation_started")
        self._push("span_finished")
        assert len(self.adapter.drain()) == 2

    def test_drain_clears_buffer(self):
        self._push("span_finished")
        self.adapter.drain()
        assert self.adapter.drain() == []

    def test_drain_returns_list(self):
        self._push("span_finished")
        assert isinstance(self.adapter.drain(), list)

    def test_flush_into_ingest(self):
        self._push("generation_finished")
        sentinel = Sentinel()
        self.adapter.flush_into(sentinel)
        assert isinstance(sentinel.detect_violations(), list)

    def test_flush_into_clears_buffer(self):
        self._push("generation_finished")
        self.adapter.flush_into(Sentinel())
        assert self.adapter.drain() == []

    def test_multiple_drain_calls_independent(self):
        self._push("span_finished")
        first = self.adapter.drain()
        self._push("span_error")
        second = self.adapter.drain()
        assert len(first) == 1
        assert len(second) == 1

    def test_buffer_event_appends(self):
        for t in ["observation_started", "generation_finished", "tool_finished"]:
            self._push(t)
        assert len(self.adapter.drain()) == 3

    def test_empty_flush_is_noop(self):
        self.adapter.flush_into(Sentinel())  # should not raise


# ---------------------------------------------------------------------------
# TestBufferThreadSafety
# ---------------------------------------------------------------------------


class TestBufferThreadSafety:
    def test_concurrent_buffer_events(self):
        adapter = _make_adapter()
        errors = []

        def push():
            try:
                for _ in range(50):
                    ev = adapter.to_sentinel_event({"type": "span_finished"})
                    adapter._buffer_event(ev)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=push) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(adapter.drain()) == 250

    def test_drain_concurrent_with_buffer(self):
        adapter = _make_adapter()
        drained: list[SentinelEvent] = []
        errors = []

        def producer():
            try:
                for _ in range(100):
                    ev = adapter.to_sentinel_event({"type": "generation_finished"})
                    adapter._buffer_event(ev)
            except Exception as exc:
                errors.append(exc)

        def consumer():
            try:
                for _ in range(10):
                    drained.extend(adapter.drain())
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=producer)
        t2 = threading.Thread(target=consumer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        drained.extend(adapter.drain())

        assert not errors
        assert len(drained) == 100


# ---------------------------------------------------------------------------
# TestSetup
# ---------------------------------------------------------------------------


class TestSetup:
    def _run_setup(self, run_id=None):
        adapter = _make_adapter(run_id=run_id)
        client, mock_tp = _make_mock_client()
        with patch.dict("sys.modules", _mock_langfuse_modules()):
            adapter.setup(client)
        observer = _get_observer(mock_tp)
        return adapter, observer

    def test_setup_registers_span_processor(self):
        adapter = _make_adapter()
        client, mock_tp = _make_mock_client()
        with patch.dict("sys.modules", _mock_langfuse_modules()):
            adapter.setup(client)
        mock_tp.add_span_processor.assert_called_once()

    def test_setup_uses_global_client_when_none_given(self):
        """setup() with no client should call get_client()."""
        mock_get_client = MagicMock()
        mock_client, mock_tp = _make_mock_client()
        mock_get_client.return_value = mock_client
        mods = _mock_langfuse_modules()
        mods["langfuse"].get_client = mock_get_client
        with patch.dict("sys.modules", mods):
            # patch get_client at the module level where it's imported inside setup
            with patch("agentcop.adapters.langfuse._require_langfuse"):
                from agentcop.adapters.langfuse import LangfuseSentinelAdapter

                a = LangfuseSentinelAdapter()
            with patch("langfuse.get_client", mock_get_client):
                with patch.dict("sys.modules", mods):
                    a.setup()

    def test_on_start_skips_non_langfuse_spans(self):
        adapter, observer = self._run_setup()
        span = MagicMock()
        span.attributes = {}  # no langfuse.observation.type
        observer.on_start(span)
        assert adapter.drain() == []

    def test_on_end_skips_non_langfuse_spans(self):
        adapter, observer = self._run_setup()
        span = MagicMock()
        span.attributes = {}
        observer.on_end(span)
        assert adapter.drain() == []

    def test_on_start_emits_observation_started(self):
        adapter, observer = self._run_setup(run_id="test-trace")
        span = _make_span(obs_type="span", name="my-span")
        observer.on_start(span)
        events = adapter.drain()
        assert len(events) == 1
        assert events[0].event_type == "observation_started"

    def test_on_end_span_emits_span_finished(self):
        adapter, observer = self._run_setup()
        span = _make_span(obs_type="span", name="db-query")
        observer.on_end(span)
        events = adapter.drain()
        assert len(events) == 1
        assert events[0].event_type == "span_finished"

    def test_on_end_span_with_error_level_emits_span_error(self):
        adapter, observer = self._run_setup()
        span = _make_span(obs_type="span", name="op", level="ERROR")
        observer.on_end(span)
        events = adapter.drain()
        assert events[0].event_type == "span_error"
        assert events[0].severity == "ERROR"

    def test_on_end_span_with_otel_error_status_emits_span_error(self):
        adapter, observer = self._run_setup()
        span = _make_span(obs_type="span", name="op", status_code_value=2)
        observer.on_end(span)
        events = adapter.drain()
        assert events[0].event_type == "span_error"

    def test_on_end_generation_emits_generation_finished(self):
        adapter, observer = self._run_setup()
        span = _make_span(
            obs_type="generation",
            name="gpt-call",
            extra_attrs={
                "langfuse.observation.model.name": "gpt-4o",
                "langfuse.observation.usage_details": '{"prompt_tokens": 10, "completion_tokens": 5}',
            },
        )
        observer.on_end(span)
        events = adapter.drain()
        assert events[0].event_type == "generation_finished"
        assert events[0].attributes["model"] == "gpt-4o"
        assert events[0].attributes["usage"]["prompt_tokens"] == 10

    def test_on_end_generation_error(self):
        adapter, observer = self._run_setup()
        span = _make_span(
            obs_type="generation",
            name="gpt-call",
            level="ERROR",
            extra_attrs={"langfuse.observation.status_message": "quota exceeded"},
        )
        observer.on_end(span)
        events = adapter.drain()
        assert events[0].event_type == "generation_error"

    def test_on_end_tool_emits_tool_finished(self):
        adapter, observer = self._run_setup()
        span = _make_span(obs_type="tool", name="web-search")
        observer.on_end(span)
        assert adapter.drain()[0].event_type == "tool_finished"

    def test_on_end_retriever_emits_retriever_finished(self):
        adapter, observer = self._run_setup()
        span = _make_span(obs_type="retriever", name="vec-search")
        observer.on_end(span)
        assert adapter.drain()[0].event_type == "retriever_finished"

    def test_on_end_event_emits_event_occurred(self):
        adapter, observer = self._run_setup()
        span = _make_span(obs_type="event", name="cache-hit")
        observer.on_end(span)
        assert adapter.drain()[0].event_type == "event_occurred"

    def test_on_end_guardrail_emits_guardrail_finished(self):
        adapter, observer = self._run_setup()
        span = _make_span(obs_type="guardrail", name="content-filter")
        observer.on_end(span)
        assert adapter.drain()[0].event_type == "guardrail_finished"

    def test_on_end_agent_type_emits_span_finished(self):
        adapter, observer = self._run_setup()
        span = _make_span(obs_type="agent", name="planner")
        observer.on_end(span)
        assert adapter.drain()[0].event_type == "span_finished"

    def test_on_end_chain_type_emits_span_finished(self):
        adapter, observer = self._run_setup()
        span = _make_span(obs_type="chain", name="rag-chain")
        observer.on_end(span)
        assert adapter.drain()[0].event_type == "span_finished"

    def test_on_end_extracts_ids_from_span_context(self):
        adapter, observer = self._run_setup(run_id=None)
        trace_int = 0xABCDEF1234567890ABCDEF1234567890
        span_int = 0xDEADBEEFCAFEBABE
        span = _make_span(obs_type="span", name="op", trace_id_int=trace_int, span_id_int=span_int)
        observer.on_end(span)
        ev = adapter.drain()[0]
        assert ev.attributes["langfuse_trace_id"] == format(trace_int, "032x")
        assert ev.attributes["observation_id"] == format(span_int, "016x")

    def test_on_end_sets_trace_id_from_run_id(self):
        adapter, observer = self._run_setup(run_id="my-session")
        span = _make_span(obs_type="span", name="op")
        observer.on_end(span)
        assert adapter.drain()[0].trace_id == "my-session"

    def test_observer_force_flush_does_not_raise(self):
        _, observer = self._run_setup()
        observer.force_flush()  # should not raise

    def test_observer_shutdown_does_not_raise(self):
        _, observer = self._run_setup()
        observer.shutdown()  # should not raise


# ---------------------------------------------------------------------------
# TestModuleLevelHelpers
# ---------------------------------------------------------------------------


class TestSafeJsonLoad:
    def setup_method(self):
        with patch("agentcop.adapters.langfuse._require_langfuse"):
            from agentcop.adapters.langfuse import _safe_json_load

            self._fn = _safe_json_load

    def test_parses_json_string(self):
        assert self._fn('{"a": 1}') == {"a": 1}

    def test_parses_json_list(self):
        assert self._fn("[1, 2, 3]") == [1, 2, 3]

    def test_returns_raw_on_invalid_json(self):
        assert self._fn("not-json") == "not-json"

    def test_returns_non_string_unchanged(self):
        assert self._fn(42) == 42
        assert self._fn({"a": 1}) == {"a": 1}


class TestIsError:
    def setup_method(self):
        with patch("agentcop.adapters.langfuse._require_langfuse"):
            from agentcop.adapters.langfuse import _is_error

            self._fn = _is_error

    def test_error_level_returns_true(self):
        span = _make_span(level="ERROR")
        assert self._fn(span) is True

    def test_default_level_returns_false(self):
        span = _make_span(level="DEFAULT")
        assert self._fn(span) is False

    def test_otel_status_code_2_returns_true(self):
        span = _make_span(status_code_value=2)
        assert self._fn(span) is True

    def test_otel_status_code_0_returns_false(self):
        span = _make_span(status_code_value=0)
        assert self._fn(span) is False

    def test_no_status_returns_false(self):
        span = MagicMock()
        span.attributes = {}
        span._status = None
        assert self._fn(span) is False


class TestNsToIso:
    def setup_method(self):
        with patch("agentcop.adapters.langfuse._require_langfuse"):
            from agentcop.adapters.langfuse import _ns_to_iso

            self._fn = _ns_to_iso

    def test_converts_ns_to_iso(self):
        # 2026-01-01T00:00:00 UTC in nanoseconds
        ns = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1e9)
        result = self._fn(ns)
        assert result is not None
        assert "2026" in result

    def test_none_returns_none(self):
        assert self._fn(None) is None

    def test_zero_returns_none(self):
        assert self._fn(0) is None


class TestSpanToRawStart:
    def setup_method(self):
        with patch("agentcop.adapters.langfuse._require_langfuse"):
            from agentcop.adapters.langfuse import _span_to_raw_start

            self._fn = _span_to_raw_start

    def test_returns_none_for_non_langfuse_span(self):
        span = MagicMock()
        span.attributes = {}
        assert self._fn(span) is None

    def test_returns_observation_started_type(self):
        span = _make_span(obs_type="generation")
        result = self._fn(span)
        assert result["type"] == "observation_started"

    def test_captures_observation_type(self):
        span = _make_span(obs_type="agent")
        assert self._fn(span)["observation_type"] == "agent"

    def test_captures_span_name(self):
        span = _make_span(obs_type="span", name="my-func")
        assert self._fn(span)["observation_name"] == "my-func"

    def test_formats_trace_id_as_hex(self):
        span = _make_span(trace_id_int=0xABCD1234)
        result = self._fn(span)
        assert format(0xABCD1234, "032x") in result["langfuse_trace_id"]

    def test_formats_span_id_as_hex(self):
        span = _make_span(span_id_int=0xDEADBEEF)
        result = self._fn(span)
        assert format(0xDEADBEEF, "016x") in result["observation_id"]

    def test_parent_id_captured(self):
        span = _make_span(parent_span_id_int=0x1234ABCD)
        result = self._fn(span)
        assert format(0x1234ABCD, "016x") in result["parent_observation_id"]

    def test_no_parent_gives_empty_string(self):
        span = _make_span()
        result = self._fn(span)
        assert result["parent_observation_id"] == ""


class TestSpanToRawEnd:
    def setup_method(self):
        with patch("agentcop.adapters.langfuse._require_langfuse"):
            from agentcop.adapters.langfuse import _span_to_raw_end

            self._fn = _span_to_raw_end

    def test_returns_none_for_non_langfuse_span(self):
        span = MagicMock()
        span.attributes = {}
        assert self._fn(span) is None

    def test_span_type_maps_to_span_finished(self):
        span = _make_span(obs_type="span")
        assert self._fn(span)["type"] == "span_finished"

    def test_span_type_with_error_maps_to_span_error(self):
        span = _make_span(obs_type="span", level="ERROR")
        assert self._fn(span)["type"] == "span_error"

    def test_generation_type_maps_to_generation_finished(self):
        span = _make_span(obs_type="generation")
        assert self._fn(span)["type"] == "generation_finished"

    def test_tool_type_maps_to_tool_finished(self):
        span = _make_span(obs_type="tool")
        assert self._fn(span)["type"] == "tool_finished"

    def test_retriever_type_maps_to_retriever_finished(self):
        span = _make_span(obs_type="retriever")
        assert self._fn(span)["type"] == "retriever_finished"

    def test_event_type_maps_to_event_occurred(self):
        span = _make_span(obs_type="event")
        assert self._fn(span)["type"] == "event_occurred"

    def test_guardrail_type_maps_to_guardrail_finished(self):
        span = _make_span(obs_type="guardrail")
        assert self._fn(span)["type"] == "guardrail_finished"

    def test_unknown_obs_type_maps_to_unknown(self):
        span = _make_span(obs_type="my_custom_type")
        assert self._fn(span)["type"] == "unknown_langfuse_event"

    def test_generation_extracts_model(self):
        span = _make_span(
            obs_type="generation", extra_attrs={"langfuse.observation.model.name": "gpt-4o"}
        )
        result = self._fn(span)
        assert result["model"] == "gpt-4o"

    def test_generation_parses_usage_json(self):
        span = _make_span(
            obs_type="generation",
            extra_attrs={
                "langfuse.observation.usage_details": '{"prompt_tokens": 5, "completion_tokens": 3}'
            },
        )
        result = self._fn(span)
        assert result["usage"]["prompt_tokens"] == 5

    def test_input_truncated_to_500(self):
        span = _make_span(obs_type="span", extra_attrs={"langfuse.observation.input": "x" * 600})
        result = self._fn(span)
        assert len(result["input"]) == 500

    def test_user_id_extracted(self):
        span = _make_span(obs_type="span", extra_attrs={"user.id": "user-7"})
        result = self._fn(span)
        assert result["user_id"] == "user-7"

    def test_session_id_extracted(self):
        span = _make_span(obs_type="span", extra_attrs={"session.id": "sess-3"})
        result = self._fn(span)
        assert result["session_id"] == "sess-3"


# ---------------------------------------------------------------------------
# TestProtocolConformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_conforms_to_sentinel_adapter_protocol(self):
        from agentcop.adapters import SentinelAdapter

        assert isinstance(_make_adapter(), SentinelAdapter)

    def test_has_source_system_attr(self):
        assert _make_adapter().source_system == "langfuse"

    def test_to_sentinel_event_returns_sentinel_event(self):
        result = _make_adapter().to_sentinel_event({"type": "span_finished"})
        assert isinstance(result, SentinelEvent)


# ---------------------------------------------------------------------------
# TestSentinelIntegration
# ---------------------------------------------------------------------------


class TestSentinelIntegration:
    def _make_sentinel_with(self, events, detectors=None):
        adapter = _make_adapter(run_id="integration")
        for ev_dict in events:
            adapter._buffer_event(adapter.to_sentinel_event(ev_dict))
        sentinel = Sentinel(detectors=detectors or [])
        adapter.flush_into(sentinel)
        return sentinel

    def test_ingests_all_events(self):
        from agentcop.violations import DEFAULT_DETECTORS

        s = self._make_sentinel_with(
            [
                {"type": "observation_started", "observation_type": "span"},
                {"type": "generation_finished", "model": "gpt-4o"},
                {"type": "span_finished"},
            ],
            detectors=DEFAULT_DETECTORS,
        )
        assert isinstance(s.detect_violations(), list)

    def test_detect_generation_error(self):
        def detect_gen_error(event: SentinelEvent):
            if event.event_type != "generation_error":
                return None
            return ViolationRecord(
                violation_type="llm_call_failed",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={
                    "model": event.attributes.get("model"),
                    "error": event.attributes.get("status_message"),
                },
            )

        s = self._make_sentinel_with(
            [{"type": "generation_error", "model": "gpt-4o", "status_message": "rate limit"}],
            detectors=[detect_gen_error],
        )
        violations = s.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "llm_call_failed"

    def test_detect_guardrail_block(self):
        def detect_block(event: SentinelEvent):
            if event.event_type != "guardrail_error":
                return None
            return ViolationRecord(
                violation_type="guardrail_blocked",
                severity="CRITICAL",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={"name": event.attributes["observation_name"]},
            )

        s = self._make_sentinel_with(
            [
                {
                    "type": "guardrail_error",
                    "observation_name": "content-policy",
                    "status_message": "blocked",
                }
            ],
            detectors=[detect_block],
        )
        violations = s.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "guardrail_blocked"
        assert violations[0].severity == "CRITICAL"

    def test_detect_tool_error(self):
        def detect_tool(event: SentinelEvent):
            if event.event_type != "tool_error":
                return None
            return ViolationRecord(
                violation_type="tool_execution_failed",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={},
            )

        s = self._make_sentinel_with(
            [
                {
                    "type": "tool_error",
                    "observation_name": "code-exec",
                    "status_message": "segfault",
                }
            ],
            detectors=[detect_tool],
        )
        violations = s.detect_violations()
        assert len(violations) == 1

    def test_no_violations_on_clean_trace(self):
        def detect_error(event: SentinelEvent):
            if event.severity != "ERROR":
                return None
            return ViolationRecord(
                violation_type="error_event",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={},
            )

        s = self._make_sentinel_with(
            [
                {"type": "observation_started"},
                {"type": "generation_finished", "model": "gpt-4o"},
                {"type": "span_finished"},
            ],
            detectors=[detect_error],
        )
        assert s.detect_violations() == []

    def test_multiple_violations_detected(self):
        def detect_any_error(event: SentinelEvent):
            if "error" not in event.event_type:
                return None
            return ViolationRecord(
                violation_type="any_error",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={},
            )

        s = self._make_sentinel_with(
            [
                {"type": "generation_error", "status_message": "fail1"},
                {"type": "span_error", "status_message": "fail2"},
                {"type": "tool_error", "status_message": "fail3"},
            ],
            detectors=[detect_any_error],
        )
        assert len(s.detect_violations()) == 3


# ---------------------------------------------------------------------------
# Runtime security tests
# ---------------------------------------------------------------------------


def _make_langfuse_runtime(gate=None, permissions=None, sandbox=None, approvals=None):
    with patch("agentcop.adapters.langfuse._require_langfuse"):
        from agentcop.adapters.langfuse import LangfuseSentinelAdapter

        return LangfuseSentinelAdapter(
            run_id="rt-run",
            gate=gate,
            permissions=permissions,
            sandbox=sandbox,
            approvals=approvals,
        )


class TestRuntimeSecurityLangfuse:
    def test_init_stores_none_by_default(self):
        a = _make_langfuse_runtime()
        assert a._gate is None
        assert a._permissions is None

    def test_init_stores_runtime_params(self):
        gate = MagicMock()
        perms = MagicMock()
        a = _make_langfuse_runtime(gate=gate, permissions=perms)
        assert a._gate is gate
        assert a._permissions is perms

    def test_gate_decision_logged_for_tool_observation(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=False, reason="blocked", risk_score=90)
        a = _make_langfuse_runtime(gate=gate)
        from agentcop.adapters._runtime import check_tool_call

        with pytest.raises(PermissionError):
            check_tool_call(a, "web_search", {})
        events = a.drain()
        assert any(e.event_type == "gate_denied" for e in events)

    def test_permission_violation_fires_event(self):
        perms = MagicMock()
        perms.verify.return_value = MagicMock(granted=False, reason="not allowed")
        a = _make_langfuse_runtime(permissions=perms)
        from agentcop.adapters._runtime import check_tool_call

        with pytest.raises(PermissionError):
            check_tool_call(a, "db_query", {})
        events = a.drain()
        assert any(e.event_type == "permission_violation" for e in events)

    def test_no_gate_backward_compatible(self):
        a = _make_langfuse_runtime()
        event = a.to_sentinel_event({"type": "observation_started", "observation_name": "x"})
        assert event.event_type == "observation_started"
