"""
Tests for src/agentcop/adapters/haystack.py

All tests mock the haystack import guard so haystack-ai does not need to be
installed in the test environment.
"""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentcop import Sentinel, ViolationRecord
from agentcop.event import SentinelEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(run_id=None):
    """Return a HaystackSentinelAdapter with the import guard bypassed."""
    with patch("agentcop.adapters.haystack._require_haystack"):
        from agentcop.adapters.haystack import HaystackSentinelAdapter

        return HaystackSentinelAdapter(run_id=run_id)


def _mock_modules():
    """Return a sys.modules patch dict that satisfies setup()'s haystack import."""
    mock_haystack = MagicMock()
    mock_tracing = MagicMock()
    mock_haystack.tracing = mock_tracing
    return {
        "haystack": mock_haystack,
        "haystack.tracing": mock_tracing,
    }


# ---------------------------------------------------------------------------
# TestRequireHaystack
# ---------------------------------------------------------------------------


class TestRequireHaystack:
    def test_raises_when_haystack_missing(self):
        with patch.dict("sys.modules", {"haystack": None}):
            if "agentcop.adapters.haystack" in sys.modules:
                del sys.modules["agentcop.adapters.haystack"]
            from agentcop.adapters.haystack import _require_haystack

            with pytest.raises(ImportError, match="haystack-ai"):
                _require_haystack()

    def test_does_not_raise_when_haystack_present(self):
        mock_haystack = MagicMock()
        with patch.dict("sys.modules", {"haystack": mock_haystack}):
            from agentcop.adapters.haystack import _require_haystack

            _require_haystack()  # no exception

    def test_constructor_calls_require(self):
        called = []

        def fake_require():
            called.append(True)

        with patch("agentcop.adapters.haystack._require_haystack", fake_require):
            from agentcop.adapters.haystack import HaystackSentinelAdapter

            HaystackSentinelAdapter()
        assert called == [True]


# ---------------------------------------------------------------------------
# TestAdapterInit
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_source_system(self):
        adapter = _make_adapter()
        assert adapter.source_system == "haystack"

    def test_run_id_none_by_default(self):
        adapter = _make_adapter()
        assert adapter._run_id is None

    def test_run_id_stored(self):
        adapter = _make_adapter(run_id="run-xyz")
        assert adapter._run_id == "run-xyz"

    def test_buffer_starts_empty(self):
        adapter = _make_adapter()
        assert adapter.drain() == []


# ---------------------------------------------------------------------------
# TestFromPipelineEvents
# ---------------------------------------------------------------------------


class TestFromPipelineEvents:
    def setup_method(self):
        self.adapter = _make_adapter(run_id="trace-pipeline")

    def test_pipeline_started_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_started", "pipeline_name": "rag"})
        assert ev.event_type == "pipeline_started"

    def test_pipeline_started_severity(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_started"})
        assert ev.severity == "INFO"

    def test_pipeline_started_source_system(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_started"})
        assert ev.source_system == "haystack"

    def test_pipeline_started_trace_id(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_started"})
        assert ev.trace_id == "trace-pipeline"

    def test_pipeline_started_pipeline_name(self):
        ev = self.adapter.to_sentinel_event(
            {"type": "pipeline_started", "pipeline_name": "my-pipe"}
        )
        assert ev.attributes["pipeline_name"] == "my-pipe"

    def test_pipeline_started_body_contains_name(self):
        ev = self.adapter.to_sentinel_event(
            {"type": "pipeline_started", "pipeline_name": "rag-v2"}
        )
        assert "rag-v2" in ev.body

    def test_pipeline_started_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_started"})
        assert ev.event_id.startswith("haystack-pipeline-")

    def test_pipeline_finished_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_finished", "pipeline_name": "rag"})
        assert ev.event_type == "pipeline_finished"

    def test_pipeline_finished_severity(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_finished"})
        assert ev.severity == "INFO"

    def test_pipeline_finished_output_keys_empty(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_finished"})
        assert ev.attributes["output_keys"] == []

    def test_pipeline_finished_output_keys_present(self):
        ev = self.adapter.to_sentinel_event(
            {
                "type": "pipeline_finished",
                "output_keys": ["llm", "retriever"],
            }
        )
        assert set(ev.attributes["output_keys"]) == {"llm", "retriever"}

    def test_pipeline_error_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_error"})
        assert ev.event_type == "pipeline_error"

    def test_pipeline_error_severity(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_error"})
        assert ev.severity == "ERROR"

    def test_pipeline_error_captures_error(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_error", "error": "OOM"})
        assert ev.attributes["error"] == "OOM"

    def test_pipeline_error_body_contains_error(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_error", "error": "timeout"})
        assert "timeout" in ev.body

    def test_pipeline_error_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "pipeline_error"})
        assert ev.event_id.startswith("haystack-pipeline-")


# ---------------------------------------------------------------------------
# TestFromComponentEvents
# ---------------------------------------------------------------------------


class TestFromComponentEvents:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_component_started_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "component_started", "component_name": "pb"})
        assert ev.event_type == "component_started"

    def test_component_started_severity(self):
        ev = self.adapter.to_sentinel_event({"type": "component_started"})
        assert ev.severity == "INFO"

    def test_component_started_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "component_started"})
        assert ev.event_id.startswith("haystack-component-")

    def test_component_started_component_name(self):
        ev = self.adapter.to_sentinel_event(
            {"type": "component_started", "component_name": "router"}
        )
        assert ev.attributes["component_name"] == "router"

    def test_component_started_component_type(self):
        ev = self.adapter.to_sentinel_event(
            {"type": "component_started", "component_type": "Router"}
        )
        assert ev.attributes["component_type"] == "Router"

    def test_component_finished_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "component_finished"})
        assert ev.event_type == "component_finished"

    def test_component_finished_output_keys_empty(self):
        ev = self.adapter.to_sentinel_event({"type": "component_finished"})
        assert ev.attributes["output_keys"] == []

    def test_component_finished_output_keys_present(self):
        ev = self.adapter.to_sentinel_event({"type": "component_finished", "output_keys": ["x"]})
        assert ev.attributes["output_keys"] == ["x"]

    def test_component_error_severity(self):
        ev = self.adapter.to_sentinel_event({"type": "component_error"})
        assert ev.severity == "ERROR"

    def test_component_error_captures_error(self):
        ev = self.adapter.to_sentinel_event({"type": "component_error", "error": "NullPointer"})
        assert ev.attributes["error"] == "NullPointer"

    def test_component_error_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "component_error"})
        assert ev.event_id.startswith("haystack-component-")

    def test_component_events_unique_event_ids(self):
        ev1 = self.adapter.to_sentinel_event({"type": "component_started"})
        ev2 = self.adapter.to_sentinel_event({"type": "component_started"})
        assert ev1.event_id != ev2.event_id


# ---------------------------------------------------------------------------
# TestFromLLMEvents
# ---------------------------------------------------------------------------


class TestFromLLMEvents:
    def setup_method(self):
        self.adapter = _make_adapter(run_id="trace-llm")

    def test_llm_run_started_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_started"})
        assert ev.event_type == "llm_run_started"

    def test_llm_run_started_severity(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_started"})
        assert ev.severity == "INFO"

    def test_llm_run_started_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_started"})
        assert ev.event_id.startswith("haystack-llm-")

    def test_llm_run_started_component_name(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_started", "component_name": "llm"})
        assert ev.attributes["component_name"] == "llm"

    def test_llm_run_started_model(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_started", "model": "gpt-4o"})
        assert ev.attributes["model"] == "gpt-4o"

    def test_llm_run_started_body_contains_model(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_started", "model": "claude-3"})
        assert "claude-3" in ev.body

    def test_llm_run_finished_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_finished"})
        assert ev.event_type == "llm_run_finished"

    def test_llm_run_finished_reply(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_finished", "reply": "Hello!"})
        assert ev.attributes["reply"] == "Hello!"

    def test_llm_run_finished_no_reply(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_finished"})
        assert ev.attributes["reply"] == ""

    def test_llm_run_error_severity(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_error"})
        assert ev.severity == "ERROR"

    def test_llm_run_error_captures_error(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_error", "error": "rate limit"})
        assert ev.attributes["error"] == "rate limit"

    def test_llm_run_error_captures_model(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_error", "model": "gpt-4o"})
        assert ev.attributes["model"] == "gpt-4o"

    def test_llm_run_error_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_error"})
        assert ev.event_id.startswith("haystack-llm-")

    def test_llm_run_error_body_contains_error(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_error", "error": "timeout"})
        assert "timeout" in ev.body

    def test_llm_trace_id_propagated(self):
        ev = self.adapter.to_sentinel_event({"type": "llm_run_started"})
        assert ev.trace_id == "trace-llm"


# ---------------------------------------------------------------------------
# TestFromRetrieverEvents
# ---------------------------------------------------------------------------


class TestFromRetrieverEvents:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_retriever_run_started_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "retriever_run_started"})
        assert ev.event_type == "retriever_run_started"

    def test_retriever_run_started_severity(self):
        ev = self.adapter.to_sentinel_event({"type": "retriever_run_started"})
        assert ev.severity == "INFO"

    def test_retriever_run_started_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "retriever_run_started"})
        assert ev.event_id.startswith("haystack-retriever-")

    def test_retriever_run_started_query(self):
        ev = self.adapter.to_sentinel_event(
            {"type": "retriever_run_started", "query": "what is RAG"}
        )
        assert ev.attributes["query"] == "what is RAG"

    def test_retriever_run_started_empty_query(self):
        ev = self.adapter.to_sentinel_event({"type": "retriever_run_started"})
        assert ev.attributes["query"] == ""

    def test_retriever_run_finished_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "retriever_run_finished"})
        assert ev.event_type == "retriever_run_finished"

    def test_retriever_run_finished_num_documents(self):
        ev = self.adapter.to_sentinel_event({"type": "retriever_run_finished", "num_documents": 5})
        assert ev.attributes["num_documents"] == 5

    def test_retriever_run_finished_zero_docs(self):
        ev = self.adapter.to_sentinel_event({"type": "retriever_run_finished"})
        assert ev.attributes["num_documents"] == 0

    def test_retriever_run_finished_body_contains_count(self):
        ev = self.adapter.to_sentinel_event({"type": "retriever_run_finished", "num_documents": 3})
        assert "3" in ev.body

    def test_retriever_run_started_component_name(self):
        ev = self.adapter.to_sentinel_event(
            {"type": "retriever_run_started", "component_name": "ret"}
        )
        assert ev.attributes["component_name"] == "ret"


# ---------------------------------------------------------------------------
# TestFromEmbedderEvents
# ---------------------------------------------------------------------------


class TestFromEmbedderEvents:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_embedder_run_started_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "embedder_run_started"})
        assert ev.event_type == "embedder_run_started"

    def test_embedder_run_started_severity(self):
        ev = self.adapter.to_sentinel_event({"type": "embedder_run_started"})
        assert ev.severity == "INFO"

    def test_embedder_run_started_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "embedder_run_started"})
        assert ev.event_id.startswith("haystack-embedder-")

    def test_embedder_run_started_model(self):
        ev = self.adapter.to_sentinel_event(
            {"type": "embedder_run_started", "model": "text-embedding-3-small"}
        )
        assert ev.attributes["model"] == "text-embedding-3-small"

    def test_embedder_run_finished_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "embedder_run_finished"})
        assert ev.event_type == "embedder_run_finished"

    def test_embedder_run_finished_severity(self):
        ev = self.adapter.to_sentinel_event({"type": "embedder_run_finished"})
        assert ev.severity == "INFO"

    def test_embedder_run_finished_model(self):
        ev = self.adapter.to_sentinel_event({"type": "embedder_run_finished", "model": "ada-002"})
        assert ev.attributes["model"] == "ada-002"

    def test_embedder_run_finished_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "embedder_run_finished"})
        assert ev.event_id.startswith("haystack-embedder-")


# ---------------------------------------------------------------------------
# TestFromUnknown
# ---------------------------------------------------------------------------


class TestFromUnknown:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_unknown_type_event_type(self):
        ev = self.adapter.to_sentinel_event({"type": "totally_made_up"})
        assert ev.event_type == "unknown_haystack_event"

    def test_unknown_type_severity(self):
        ev = self.adapter.to_sentinel_event({"type": "totally_made_up"})
        assert ev.severity == "INFO"

    def test_unknown_preserves_original_type(self):
        ev = self.adapter.to_sentinel_event({"type": "my_custom_span"})
        assert ev.attributes["original_type"] == "my_custom_span"

    def test_empty_dict_produces_unknown(self):
        ev = self.adapter.to_sentinel_event({})
        assert ev.event_type == "unknown_haystack_event"

    def test_unknown_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "weird"})
        assert ev.event_id.startswith("haystack-unknown-")

    def test_unknown_body_mentions_type(self):
        ev = self.adapter.to_sentinel_event({"type": "weird_thing"})
        assert "weird_thing" in ev.body


# ---------------------------------------------------------------------------
# TestTimestampParsing
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_parses_iso_timestamp(self):
        ev = self.adapter.to_sentinel_event(
            {
                "type": "pipeline_started",
                "timestamp": "2026-04-01T10:00:00Z",
            }
        )
        assert ev.timestamp.year == 2026
        assert ev.timestamp.month == 4

    def test_parses_timestamp_without_z(self):
        ev = self.adapter.to_sentinel_event(
            {
                "type": "pipeline_started",
                "timestamp": "2026-04-01T10:00:00+00:00",
            }
        )
        assert ev.timestamp.year == 2026

    def test_falls_back_to_now_on_invalid_timestamp(self):
        before = datetime.now(UTC)
        ev = self.adapter.to_sentinel_event(
            {
                "type": "pipeline_started",
                "timestamp": "not-a-date",
            }
        )
        after = datetime.now(UTC)
        assert before <= ev.timestamp <= after

    def test_falls_back_to_now_when_no_timestamp(self):
        before = datetime.now(UTC)
        ev = self.adapter.to_sentinel_event({"type": "pipeline_started"})
        after = datetime.now(UTC)
        assert before <= ev.timestamp <= after


# ---------------------------------------------------------------------------
# TestDrainFlush
# ---------------------------------------------------------------------------


class TestDrainFlush:
    def setup_method(self):
        self.adapter = _make_adapter(run_id="drain-test")

    def _push(self, event_type: str):
        ev = self.adapter.to_sentinel_event({"type": event_type})
        self.adapter._buffer_event(ev)

    def test_drain_returns_all_events(self):
        self._push("pipeline_started")
        self._push("pipeline_finished")
        events = self.adapter.drain()
        assert len(events) == 2

    def test_drain_clears_buffer(self):
        self._push("pipeline_started")
        self.adapter.drain()
        assert self.adapter.drain() == []

    def test_drain_returns_list(self):
        self._push("pipeline_started")
        result = self.adapter.drain()
        assert isinstance(result, list)

    def test_flush_into_ingest(self):
        self._push("pipeline_started")
        sentinel = Sentinel()
        self.adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()
        assert isinstance(violations, list)

    def test_flush_into_clears_buffer(self):
        self._push("pipeline_started")
        sentinel = Sentinel()
        self.adapter.flush_into(sentinel)
        assert self.adapter.drain() == []

    def test_multiple_drain_calls_independent(self):
        self._push("pipeline_started")
        first = self.adapter.drain()
        self._push("pipeline_finished")
        second = self.adapter.drain()
        assert len(first) == 1
        assert len(second) == 1

    def test_buffer_event_appends(self):
        self._push("pipeline_started")
        self._push("component_started")
        self._push("llm_run_started")
        assert len(self.adapter.drain()) == 3

    def test_empty_flush_is_noop(self):
        sentinel = Sentinel()
        self.adapter.flush_into(sentinel)  # no events — should not raise


# ---------------------------------------------------------------------------
# TestBufferThreadSafety
# ---------------------------------------------------------------------------


class TestBufferThreadSafety:
    def test_concurrent_buffer_events(self):
        adapter = _make_adapter()
        errors = []

        def push_events():
            try:
                for _ in range(50):
                    ev = adapter.to_sentinel_event({"type": "pipeline_started"})
                    adapter._buffer_event(ev)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=push_events) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(adapter.drain()) == 250

    def test_drain_concurrent_with_buffer(self):
        adapter = _make_adapter()
        events_drained: list[SentinelEvent] = []
        errors = []

        def producer():
            try:
                for _ in range(100):
                    ev = adapter.to_sentinel_event({"type": "component_started"})
                    adapter._buffer_event(ev)
            except Exception as exc:
                errors.append(exc)

        def consumer():
            try:
                for _ in range(10):
                    events_drained.extend(adapter.drain())
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=producer)
        t2 = threading.Thread(target=consumer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Drain any remainder
        events_drained.extend(adapter.drain())
        assert not errors
        assert len(events_drained) == 100


# ---------------------------------------------------------------------------
# TestSetup
# ---------------------------------------------------------------------------


class TestSetup:
    def test_setup_sets_provided_tracer(self):
        """setup() must replace provided_tracer with a _WrappingTracer."""
        mock_proxy = MagicMock()
        mock_proxy.provided_tracer = None
        with patch.dict("sys.modules", _mock_modules()):
            adapter = _make_adapter()
            adapter.setup(proxy_tracer=mock_proxy)
        new_tracer = mock_proxy.provided_tracer
        # It should no longer be None and should not be a plain MagicMock
        assert new_tracer is not None
        assert not isinstance(new_tracer, MagicMock)

    def test_setup_wrapping_tracer_has_trace_method(self):
        mock_proxy = MagicMock()
        mock_proxy.provided_tracer = None
        with patch.dict("sys.modules", _mock_modules()):
            adapter = _make_adapter()
            adapter.setup(proxy_tracer=mock_proxy)
        new_tracer = mock_proxy.provided_tracer
        assert hasattr(new_tracer, "trace")
        assert callable(new_tracer.trace)

    def test_setup_pipeline_run_emits_start_and_end_events(self):
        """
        Simulate pipeline.run call via the wrapping tracer and verify that
        pipeline_started and pipeline_finished events land in the buffer.
        """
        mock_proxy = MagicMock()
        mock_proxy.provided_tracer = None
        with patch.dict("sys.modules", _mock_modules()):
            adapter = _make_adapter(run_id="setup-test")
            adapter.setup(proxy_tracer=mock_proxy)

        tracer = mock_proxy.provided_tracer
        with tracer.trace(
            "haystack.pipeline.run",
            tags={"haystack.pipeline.name": "test-pipe"},
        ) as span:
            span.set_tag("haystack.pipeline.output_data", {"llm": "answer"})

        events = adapter.drain()
        types = [e.event_type for e in events]
        assert "pipeline_started" in types
        assert "pipeline_finished" in types

    def test_setup_pipeline_run_emits_error_event_on_exception(self):
        mock_proxy = MagicMock()
        mock_proxy.provided_tracer = None
        with patch.dict("sys.modules", _mock_modules()):
            adapter = _make_adapter(run_id="setup-err")
            adapter.setup(proxy_tracer=mock_proxy)

        tracer = mock_proxy.provided_tracer
        with pytest.raises(ValueError):
            with tracer.trace("haystack.pipeline.run", tags={"haystack.pipeline.name": "p"}):
                raise ValueError("boom")

        events = adapter.drain()
        types = [e.event_type for e in events]
        assert "pipeline_error" in types
        err_ev = next(e for e in events if e.event_type == "pipeline_error")
        assert "boom" in err_ev.attributes["error"]

    def test_setup_component_run_llm_emits_llm_events(self):
        mock_proxy = MagicMock()
        mock_proxy.provided_tracer = None
        with patch.dict("sys.modules", _mock_modules()):
            adapter = _make_adapter()
            adapter.setup(proxy_tracer=mock_proxy)

        tracer = mock_proxy.provided_tracer
        with tracer.trace(
            "haystack.component.run",
            tags={
                "haystack.component.name": "llm",
                "haystack.component.type": "haystack.components.generators.openai.OpenAIGenerator",
            },
        ) as span:
            span.set_tag("haystack.component.output.replies", ["The answer is 42."])

        events = adapter.drain()
        types = [e.event_type for e in events]
        assert "llm_run_started" in types
        assert "llm_run_finished" in types

    def test_setup_uses_global_tracer_when_proxy_none(self):
        """setup() without proxy_tracer arg should use haystack.tracing.tracer."""
        mock_tracer = MagicMock()
        mock_tracer.provided_tracer = None
        mock_tracing = MagicMock()
        mock_tracing.tracer = mock_tracer
        mock_haystack = MagicMock()
        mock_haystack.tracing = mock_tracing

        with patch.dict(
            "sys.modules",
            {
                "haystack": mock_haystack,
                "haystack.tracing": mock_tracing,
            },
        ):
            adapter = _make_adapter()
            adapter.setup()  # no proxy_tracer — should use mock_tracing.tracer
        assert mock_tracer.provided_tracer is not None


# ---------------------------------------------------------------------------
# TestOpToRaw
# ---------------------------------------------------------------------------


class TestOpToRaw:
    """Unit tests for the module-level _op_to_raw helper."""

    def setup_method(self):
        with patch("agentcop.adapters.haystack._require_haystack"):
            from agentcop.adapters.haystack import _op_to_raw

            self._op_to_raw = _op_to_raw

    def test_pipeline_start(self):
        result = self._op_to_raw(
            "haystack.pipeline.run",
            {"haystack.pipeline.name": "rag"},
            "start",
        )
        assert result == {"type": "pipeline_started", "pipeline_name": "rag"}

    def test_pipeline_end_with_output_keys(self):
        result = self._op_to_raw(
            "haystack.pipeline.run",
            {},
            "end",
            span_tags={"haystack.pipeline.output_data": {"llm": "x", "ret": "y"}},
        )
        assert result["type"] == "pipeline_finished"
        assert set(result["output_keys"]) == {"llm", "ret"}

    def test_pipeline_error(self):
        result = self._op_to_raw("haystack.pipeline.run", {}, "error", error="OOM")
        assert result["type"] == "pipeline_error"
        assert result["error"] == "OOM"

    def test_llm_component_start(self):
        result = self._op_to_raw(
            "haystack.component.run",
            {
                "haystack.component.name": "llm",
                "haystack.component.type": "haystack.components.generators.openai.OpenAIGenerator",
            },
            "start",
        )
        assert result["type"] == "llm_run_started"
        assert result["component_name"] == "llm"

    def test_retriever_component_start(self):
        result = self._op_to_raw(
            "haystack.component.run",
            {
                "haystack.component.name": "ret",
                "haystack.component.type": "haystack.components.retrievers.InMemoryRetriever",
            },
            "start",
            span_tags={"haystack.component.input.query": "what is RAG"},
        )
        assert result["type"] == "retriever_run_started"
        assert result["query"] == "what is RAG"

    def test_retriever_component_end_count_docs(self):
        result = self._op_to_raw(
            "haystack.component.run",
            {
                "haystack.component.name": "ret",
                "haystack.component.type": "InMemoryRetriever",
            },
            "end",
            span_tags={"haystack.component.output.documents": ["d1", "d2", "d3"]},
        )
        assert result["type"] == "retriever_run_finished"
        assert result["num_documents"] == 3

    def test_embedder_component_start(self):
        result = self._op_to_raw(
            "haystack.component.run",
            {
                "haystack.component.name": "emb",
                "haystack.component.type": "SentenceTransformersTextEmbedder",
            },
            "start",
        )
        assert result["type"] == "embedder_run_started"

    def test_generic_component_start(self):
        result = self._op_to_raw(
            "haystack.component.run",
            {
                "haystack.component.name": "pb",
                "haystack.component.type": "PromptBuilder",
            },
            "start",
        )
        assert result["type"] == "component_started"

    def test_unknown_operation_returns_none(self):
        result = self._op_to_raw("haystack.unknown.thing", {}, "start")
        assert result is None

    def test_llm_reply_extracted_from_replies_tag(self):
        result = self._op_to_raw(
            "haystack.component.run",
            {
                "haystack.component.name": "llm",
                "haystack.component.type": "OpenAIGenerator",
            },
            "end",
            span_tags={"haystack.component.output.replies": ["Hello world"]},
        )
        assert result["reply"] == "Hello world"


# ---------------------------------------------------------------------------
# TestComponentCategory
# ---------------------------------------------------------------------------


class TestComponentCategory:
    def setup_method(self):
        with patch("agentcop.adapters.haystack._require_haystack"):
            from agentcop.adapters.haystack import _component_category

            self._cc = _component_category

    def test_generator_is_llm(self):
        assert self._cc("OpenAIGenerator") == "llm"

    def test_full_path_generator_is_llm(self):
        assert self._cc("haystack.components.generators.openai.OpenAIGenerator") == "llm"

    def test_retriever_is_retriever(self):
        assert self._cc("InMemoryRetriever") == "retriever"

    def test_embedder_is_embedder(self):
        assert self._cc("SentenceTransformersTextEmbedder") == "embedder"

    def test_other_is_component(self):
        assert self._cc("PromptBuilder") == "component"

    def test_empty_string_is_component(self):
        assert self._cc("") == "component"


# ---------------------------------------------------------------------------
# TestExtractModel
# ---------------------------------------------------------------------------


class TestExtractModel:
    def setup_method(self):
        with patch("agentcop.adapters.haystack._require_haystack"):
            from agentcop.adapters.haystack import _extract_model

            self._em = _extract_model

    def test_extracts_from_meta_list(self):
        tags = {"haystack.component.output.meta": [{"model": "gpt-4o"}]}
        assert self._em(tags) == "gpt-4o"

    def test_extracts_model_name_from_meta(self):
        tags = {"haystack.component.output.meta": [{"model_name": "claude-3"}]}
        assert self._em(tags) == "claude-3"

    def test_falls_back_to_llm_model_name(self):
        tags = {"haystack.llm.model_name": "gpt-3.5-turbo"}
        assert self._em(tags) == "gpt-3.5-turbo"

    def test_falls_back_to_model(self):
        tags = {"model": "ada-002"}
        assert self._em(tags) == "ada-002"

    def test_returns_unknown_when_no_model(self):
        assert self._em({}) == "unknown"


# ---------------------------------------------------------------------------
# TestSpanProxy
# ---------------------------------------------------------------------------


class TestSpanProxy:
    def setup_method(self):
        with patch("agentcop.adapters.haystack._require_haystack"):
            from agentcop.adapters.haystack import _SpanProxy

            self._SpanProxy = _SpanProxy

    def test_set_tag_stores_value(self):
        proxy = self._SpanProxy()
        proxy.set_tag("key", "val")
        assert proxy._tags["key"] == "val"

    def test_set_content_tag_stores_value(self):
        proxy = self._SpanProxy()
        proxy.set_content_tag("input", "hello")
        assert proxy._tags["input"] == "hello"

    def test_set_tag_forwards_to_real_span(self):
        real = MagicMock()
        proxy = self._SpanProxy(real)
        proxy.set_tag("k", "v")
        real.set_tag.assert_called_once_with("k", "v")

    def test_set_tag_swallows_real_span_error(self):
        real = MagicMock()
        real.set_tag.side_effect = RuntimeError("oops")
        proxy = self._SpanProxy(real)
        proxy.set_tag("k", "v")  # should not raise
        assert proxy._tags["k"] == "v"

    def test_raw_span_delegates_to_real(self):
        real = MagicMock()
        real.raw_span.return_value = "span_obj"
        proxy = self._SpanProxy(real)
        assert proxy.raw_span() == "span_obj"

    def test_raw_span_none_when_no_real(self):
        proxy = self._SpanProxy()
        assert proxy.raw_span() is None


# ---------------------------------------------------------------------------
# TestProtocolConformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_adapter_conforms_to_sentinel_adapter_protocol(self):
        from agentcop.adapters import SentinelAdapter

        adapter = _make_adapter()
        assert isinstance(adapter, SentinelAdapter)

    def test_has_source_system_attr(self):
        adapter = _make_adapter()
        assert hasattr(adapter, "source_system")
        assert adapter.source_system == "haystack"

    def test_to_sentinel_event_returns_sentinel_event(self):
        adapter = _make_adapter()
        result = adapter.to_sentinel_event({"type": "pipeline_started"})
        assert isinstance(result, SentinelEvent)


# ---------------------------------------------------------------------------
# TestSentinelIntegration
# ---------------------------------------------------------------------------


class TestSentinelIntegration:
    def _make_sentinel_with_adapter(self, events, detectors=None):
        adapter = _make_adapter(run_id="integration")
        for ev_dict in events:
            adapter._buffer_event(adapter.to_sentinel_event(ev_dict))
        sentinel = Sentinel(detectors=detectors or [])
        adapter.flush_into(sentinel)
        return sentinel

    def test_sentinel_ingests_all_events(self):
        from agentcop.violations import DEFAULT_DETECTORS

        sentinel = self._make_sentinel_with_adapter(
            [
                {"type": "pipeline_started"},
                {"type": "llm_run_started"},
                {"type": "pipeline_finished"},
            ],
            detectors=DEFAULT_DETECTORS,
        )
        violations = sentinel.detect_violations()
        assert isinstance(violations, list)

    def test_detect_llm_rate_limit(self):
        def detect_llm_rate_limit(event: SentinelEvent):
            if event.event_type != "llm_run_error":
                return None
            error = event.attributes.get("error", "").lower()
            if "rate limit" not in error:
                return None
            return ViolationRecord(
                violation_type="llm_rate_limited",
                severity="WARN",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={"model": event.attributes.get("model"), "error": error},
            )

        sentinel = self._make_sentinel_with_adapter(
            [{"type": "llm_run_error", "model": "gpt-4o", "error": "rate limit exceeded"}],
            detectors=[detect_llm_rate_limit],
        )
        violations = sentinel.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "llm_rate_limited"

    def test_detect_empty_retrieval(self):
        def detect_empty_retrieval(event: SentinelEvent):
            if event.event_type != "retriever_run_finished":
                return None
            if event.attributes.get("num_documents", -1) != 0:
                return None
            return ViolationRecord(
                violation_type="empty_retrieval",
                severity="WARN",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={},
            )

        sentinel = self._make_sentinel_with_adapter(
            [{"type": "retriever_run_finished", "num_documents": 0}],
            detectors=[detect_empty_retrieval],
        )
        violations = sentinel.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "empty_retrieval"

    def test_detect_component_error(self):
        def detect_component_error(event: SentinelEvent):
            if event.event_type not in ("component_error", "llm_run_error", "pipeline_error"):
                return None
            return ViolationRecord(
                violation_type="component_execution_failed",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={"error": event.attributes.get("error", "")},
            )

        sentinel = self._make_sentinel_with_adapter(
            [{"type": "component_error", "component_name": "router", "error": "NullPointer"}],
            detectors=[detect_component_error],
        )
        violations = sentinel.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "component_execution_failed"

    def test_no_violations_when_no_errors(self):
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

        sentinel = self._make_sentinel_with_adapter(
            [
                {"type": "pipeline_started"},
                {"type": "llm_run_started"},
                {"type": "llm_run_finished"},
                {"type": "pipeline_finished"},
            ],
            detectors=[detect_error],
        )
        violations = sentinel.detect_violations()
        assert violations == []

    def test_multiple_violations_detected(self):
        def detect_llm_error(event: SentinelEvent):
            if event.event_type != "llm_run_error":
                return None
            return ViolationRecord(
                violation_type="llm_failed",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={},
            )

        sentinel = self._make_sentinel_with_adapter(
            [
                {"type": "llm_run_error", "error": "timeout"},
                {"type": "llm_run_error", "error": "OOM"},
            ],
            detectors=[detect_llm_error],
        )
        violations = sentinel.detect_violations()
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# Runtime security tests
# ---------------------------------------------------------------------------


def _make_haystack_runtime(gate=None, permissions=None, sandbox=None, approvals=None):
    with patch("agentcop.adapters.haystack._require_haystack"):
        from agentcop.adapters.haystack import HaystackSentinelAdapter

        return HaystackSentinelAdapter(
            run_id="rt-run",
            gate=gate,
            permissions=permissions,
            sandbox=sandbox,
            approvals=approvals,
        )


class TestRuntimeSecurityHaystack:
    def test_init_stores_none_by_default(self):
        a = _make_haystack_runtime()
        assert a._gate is None
        assert a._permissions is None

    def test_init_stores_runtime_params(self):
        gate = MagicMock()
        perms = MagicMock()
        sandbox = MagicMock()
        a = _make_haystack_runtime(gate=gate, permissions=perms, sandbox=sandbox)
        assert a._gate is gate
        assert a._permissions is perms
        assert a._sandbox is sandbox

    def test_gate_denial_fires_event(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=False, reason="blocked", risk_score=90)
        a = _make_haystack_runtime(gate=gate)
        from agentcop.adapters._runtime import check_tool_call

        with pytest.raises(PermissionError):
            check_tool_call(a, "OpenAIGenerator", {})
        events = a.drain()
        assert any(e.event_type == "gate_denied" for e in events)

    def test_permission_violation_fires_event(self):
        perms = MagicMock()
        perms.verify.return_value = MagicMock(granted=False, reason="forbidden")
        a = _make_haystack_runtime(permissions=perms)
        from agentcop.adapters._runtime import check_tool_call

        with pytest.raises(PermissionError):
            check_tool_call(a, "DocumentRetriever", {})
        events = a.drain()
        assert any(e.event_type == "permission_violation" for e in events)

    def test_no_gate_backward_compatible(self):
        a = _make_haystack_runtime()
        event = a.to_sentinel_event({"type": "pipeline_started", "pipeline_name": "p"})
        assert event.event_type == "pipeline_started"


# ---------------------------------------------------------------------------
# Trust integration
# ---------------------------------------------------------------------------


def _make_adapter_trust(**kwargs):
    with patch("agentcop.adapters.haystack._require_haystack"):
        from agentcop.adapters.haystack import HaystackSentinelAdapter

        return HaystackSentinelAdapter(**kwargs)


class TestTrustIntegration:
    def test_accepts_trust_param(self):
        trust = MagicMock()
        a = _make_adapter_trust(trust=trust)
        assert a._trust is trust

    def test_accepts_attestor_param(self):
        attestor = MagicMock()
        a = _make_adapter_trust(attestor=attestor)
        assert a._attestor is attestor

    def test_accepts_hierarchy_param(self):
        hierarchy = MagicMock()
        a = _make_adapter_trust(hierarchy=hierarchy)
        assert a._hierarchy is hierarchy

    def test_no_trust_defaults_to_none(self):
        a = _make_adapter_trust()
        assert a._trust is None

    def test_trace_calls_record_trust_node_on_success(self):
        trust = MagicMock()
        a = _make_adapter_trust(trust=trust)
        mock_proxy = MagicMock()
        mock_proxy.provided_tracer = None
        with patch.dict("sys.modules", _mock_modules()):
            a.setup(proxy_tracer=mock_proxy)
        tracer = mock_proxy.provided_tracer
        tags = {"haystack.component.name": "llm", "haystack.component.type": "OpenAIGenerator"}
        with tracer.trace("haystack.component.run", tags=tags):
            pass
        trust.add_node.assert_called_once()

    def test_trace_does_not_call_add_node_on_error(self):
        trust = MagicMock()
        a = _make_adapter_trust(trust=trust)
        mock_proxy = MagicMock()
        mock_proxy.provided_tracer = None
        with patch.dict("sys.modules", _mock_modules()):
            a.setup(proxy_tracer=mock_proxy)
        tracer = mock_proxy.provided_tracer
        tags = {"haystack.component.name": "llm", "haystack.component.type": "OpenAIGenerator"}
        with pytest.raises(ValueError):
            with tracer.trace("haystack.component.run", tags=tags):
                raise ValueError("fail")
        trust.add_node.assert_not_called()
