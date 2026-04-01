"""
Tests for src/agentcop/adapters/langsmith.py

All tests mock the langsmith import guard so langsmith does not need to be
installed in the test environment. Setup tests use a MagicMock client and
call the intercepted create_run / update_run directly.
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from agentcop import Sentinel, ViolationRecord
from agentcop.event import SentinelEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(run_id=None):
    """Return a LangSmithSentinelAdapter with the import guard bypassed."""
    with patch("agentcop.adapters.langsmith._require_langsmith"):
        from agentcop.adapters.langsmith import LangSmithSentinelAdapter
        return LangSmithSentinelAdapter(run_id=run_id)


def _make_mock_client():
    """Return a MagicMock client with create_run and update_run."""
    client = MagicMock()
    client.create_run = MagicMock(return_value=None)
    client.update_run = MagicMock(return_value=None)
    return client


def _setup_adapter(run_id=None):
    """Return (adapter, mock_client) with interceptors already installed."""
    adapter = _make_adapter(run_id=run_id)
    client = _make_mock_client()
    adapter.setup(client)
    return adapter, client


def _create_run(client, *, name="my-chain", run_type="chain", run_id=None,
                trace_id=None, parent_run_id=None, inputs=None, tags=None,
                extra=None):
    """Call the intercepted create_run with common kwargs."""
    if run_id is None:
        run_id = str(uuid.uuid4())
    client.create_run(
        name=name,
        inputs=inputs or {"question": "What is RAG?"},
        run_type=run_type,
        id=run_id,
        trace_id=trace_id or run_id,
        parent_run_id=parent_run_id or "",
        tags=tags or [],
        extra=extra or {},
    )
    return run_id


def _update_run(client, run_id, *, outputs=None, error=None, extra=None):
    """Call the intercepted update_run."""
    kwargs = {"run_id": run_id}
    if outputs is not None:
        kwargs["outputs"] = outputs
    if error is not None:
        kwargs["error"] = error
    if extra is not None:
        kwargs["extra"] = extra
    client.update_run(run_id, **kwargs)


# ---------------------------------------------------------------------------
# TestRequireLangSmith
# ---------------------------------------------------------------------------

class TestRequireLangSmith:
    def test_raises_import_error_when_langsmith_missing(self):
        """_require_langsmith raises ImportError if langsmith not installed."""
        with patch.dict(sys.modules, {"langsmith": None}):
            with pytest.raises(ImportError, match="langsmith"):
                from agentcop.adapters.langsmith import _require_langsmith
                _require_langsmith()

    def test_passes_when_langsmith_available(self):
        """_require_langsmith is silent when langsmith is importable."""
        fake_langsmith = MagicMock()
        with patch.dict(sys.modules, {"langsmith": fake_langsmith}):
            from agentcop.adapters.langsmith import _require_langsmith
            _require_langsmith()  # must not raise


# ---------------------------------------------------------------------------
# TestAdapterInit
# ---------------------------------------------------------------------------

class TestAdapterInit:
    def test_source_system(self):
        adapter = _make_adapter()
        assert adapter.source_system == "langsmith"

    def test_run_id_none_by_default(self):
        adapter = _make_adapter()
        assert adapter._run_id is None

    def test_run_id_stored(self):
        adapter = _make_adapter(run_id="session-42")
        assert adapter._run_id == "session-42"

    def test_buffer_starts_empty(self):
        adapter = _make_adapter()
        assert adapter.drain() == []

    def test_inflight_starts_empty(self):
        adapter = _make_adapter()
        assert adapter._inflight == {}


# ---------------------------------------------------------------------------
# TestFromRunStarted
# ---------------------------------------------------------------------------

class TestFromRunStarted:
    def test_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started", "run_name": "pipeline"})
        assert ev.event_type == "run_started"

    def test_severity_info(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started"})
        assert ev.severity == "INFO"

    def test_source_system(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started"})
        assert ev.source_system == "langsmith"

    def test_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started"})
        assert ev.event_id.startswith("ls-run-")

    def test_body_contains_run_name(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started", "run_name": "my-pipeline"})
        assert "my-pipeline" in ev.body

    def test_body_contains_run_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started", "run_type": "llm"})
        assert "llm" in ev.body

    def test_trace_id_from_run_id(self):
        adapter = _make_adapter(run_id="run-001")
        ev = adapter.to_sentinel_event({"type": "run_started"})
        assert ev.trace_id == "run-001"

    def test_trace_id_from_ls_trace_id(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started", "ls_trace_id": "abc123"})
        assert ev.trace_id == "abc123"

    def test_attributes_run_name(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started", "run_name": "step1"})
        assert ev.attributes["run_name"] == "step1"

    def test_attributes_run_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started", "run_type": "tool"})
        assert ev.attributes["run_type"] == "tool"

    def test_attributes_tags(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started", "tags": ["prod", "v2"]})
        assert ev.attributes["tags"] == ["prod", "v2"]

    def test_attributes_inputs(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started", "inputs": '{"q": "hi"}'})
        assert ev.attributes["inputs"] == '{"q": "hi"}'

    def test_attributes_metadata(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "run_started",
            "metadata": {"env": "prod"},
        })
        assert ev.attributes["metadata"] == {"env": "prod"}

    def test_attributes_parent_run_id(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "run_started",
            "parent_run_id": "parent-abc",
        })
        assert ev.attributes["parent_run_id"] == "parent-abc"


# ---------------------------------------------------------------------------
# TestFromChainEvents
# ---------------------------------------------------------------------------

class TestFromChainEvents:
    def test_chain_finished_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "chain_finished"})
        assert ev.event_type == "chain_finished"

    def test_chain_finished_severity(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "chain_finished"})
        assert ev.severity == "INFO"

    def test_chain_finished_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "chain_finished"})
        assert ev.event_id.startswith("ls-chain-")

    def test_chain_finished_body(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "chain_finished", "run_name": "rag-chain"})
        assert "rag-chain" in ev.body

    def test_chain_error_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "chain_error"})
        assert ev.event_type == "chain_error"

    def test_chain_error_severity(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "chain_error"})
        assert ev.severity == "ERROR"

    def test_chain_error_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "chain_error"})
        assert ev.event_id.startswith("ls-chain-")

    def test_chain_error_body_contains_error(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "chain_error",
            "run_name": "rag-chain",
            "error": "context length exceeded",
        })
        assert "context length exceeded" in ev.body

    def test_chain_finished_has_outputs(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "chain_finished",
            "outputs": '{"answer": "42"}',
        })
        assert ev.attributes["outputs"] == '{"answer": "42"}'

    def test_chain_error_has_error_attr(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "chain_error",
            "error": "timeout",
        })
        assert ev.attributes["error"] == "timeout"

    def test_prompt_run_type_uses_chain_translator(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="my-prompt", run_type="prompt")
        _update_run(client, run_id, outputs={"text": "Hello"})
        events = adapter.drain()
        end = [e for e in events if "finished" in e.event_type or "error" in e.event_type]
        assert any(e.event_type == "chain_finished" for e in end)

    def test_parser_run_type_uses_chain_translator(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="my-parser", run_type="parser")
        _update_run(client, run_id, outputs={"parsed": True})
        events = adapter.drain()
        assert any(e.event_type == "chain_finished" for e in events)


# ---------------------------------------------------------------------------
# TestFromLLMEvents
# ---------------------------------------------------------------------------

class TestFromLLMEvents:
    def test_llm_finished_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "llm_finished"})
        assert ev.event_type == "llm_finished"

    def test_llm_finished_severity(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "llm_finished"})
        assert ev.severity == "INFO"

    def test_llm_finished_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "llm_finished"})
        assert ev.event_id.startswith("ls-llm-")

    def test_llm_finished_body_contains_model(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "llm_finished",
            "run_name": "gpt-call",
            "model": "gpt-4o-mini",
        })
        assert "gpt-4o-mini" in ev.body

    def test_llm_error_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "llm_error"})
        assert ev.event_type == "llm_error"

    def test_llm_error_severity(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "llm_error"})
        assert ev.severity == "ERROR"

    def test_llm_error_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "llm_error"})
        assert ev.event_id.startswith("ls-llm-")

    def test_llm_error_body_contains_error(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "llm_error",
            "run_name": "gpt-call",
            "model": "gpt-4o-mini",
            "error": "rate limit exceeded",
        })
        assert "rate limit exceeded" in ev.body

    def test_llm_attrs_model(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "llm_finished",
            "model": "gpt-4o",
        })
        assert ev.attributes["model"] == "gpt-4o"

    def test_llm_attrs_provider(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "llm_finished",
            "provider": "openai",
        })
        assert ev.attributes["provider"] == "openai"

    def test_llm_attrs_usage(self):
        adapter = _make_adapter()
        usage = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
        ev = adapter.to_sentinel_event({
            "type": "llm_finished",
            "usage": usage,
        })
        assert ev.attributes["usage"] == usage

    def test_llm_attrs_defaults(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "llm_finished"})
        assert ev.attributes["model"] == "unknown"
        assert ev.attributes["provider"] == "unknown"
        assert ev.attributes["usage"] == {}

    def test_llm_usage_from_metadata_via_setup(self):
        """usage_metadata in extra.metadata is picked up by the interceptor."""
        adapter, client = _setup_adapter()
        usage = {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}
        extra = {"metadata": {"ls_model_name": "gpt-4o-mini", "ls_provider": "openai",
                               "usage_metadata": usage}}
        run_id = _create_run(client, name="gpt-call", run_type="llm", extra=extra)
        _update_run(client, run_id, outputs={"text": "Hello"})
        events = adapter.drain()
        llm_fin = next(e for e in events if e.event_type == "llm_finished")
        assert llm_fin.attributes["usage"] == usage
        assert llm_fin.attributes["model"] == "gpt-4o-mini"
        assert llm_fin.attributes["provider"] == "openai"


# ---------------------------------------------------------------------------
# TestFromToolEvents
# ---------------------------------------------------------------------------

class TestFromToolEvents:
    def test_tool_finished_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "tool_finished"})
        assert ev.event_type == "tool_finished"

    def test_tool_finished_severity(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "tool_finished"})
        assert ev.severity == "INFO"

    def test_tool_finished_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "tool_finished"})
        assert ev.event_id.startswith("ls-tool-")

    def test_tool_finished_body(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "tool_finished", "run_name": "search"})
        assert "search" in ev.body

    def test_tool_error_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "tool_error"})
        assert ev.event_type == "tool_error"

    def test_tool_error_severity(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "tool_error"})
        assert ev.severity == "ERROR"

    def test_tool_error_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "tool_error"})
        assert ev.event_id.startswith("ls-tool-")

    def test_tool_error_body_contains_error(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "tool_error",
            "run_name": "search",
            "error": "connection refused",
        })
        assert "connection refused" in ev.body

    def test_tool_finished_has_outputs(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "tool_finished",
            "outputs": '{"results": []}',
        })
        assert ev.attributes["outputs"] == '{"results": []}'


# ---------------------------------------------------------------------------
# TestFromRetrieverEvents
# ---------------------------------------------------------------------------

class TestFromRetrieverEvents:
    def test_retriever_finished_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "retriever_finished"})
        assert ev.event_type == "retriever_finished"

    def test_retriever_finished_severity(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "retriever_finished"})
        assert ev.severity == "INFO"

    def test_retriever_finished_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "retriever_finished"})
        assert ev.event_id.startswith("ls-retriever-")

    def test_retriever_finished_body(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "retriever_finished", "run_name": "faiss"})
        assert "faiss" in ev.body

    def test_retriever_error_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "retriever_error"})
        assert ev.event_type == "retriever_error"

    def test_retriever_error_severity(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "retriever_error"})
        assert ev.severity == "ERROR"

    def test_retriever_error_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "retriever_error"})
        assert ev.event_id.startswith("ls-retriever-")

    def test_retriever_error_body_contains_error(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "retriever_error",
            "run_name": "faiss",
            "error": "index not found",
        })
        assert "index not found" in ev.body


# ---------------------------------------------------------------------------
# TestFromEmbeddingEvents
# ---------------------------------------------------------------------------

class TestFromEmbeddingEvents:
    def test_embedding_finished_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "embedding_finished"})
        assert ev.event_type == "embedding_finished"

    def test_embedding_finished_severity(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "embedding_finished"})
        assert ev.severity == "INFO"

    def test_embedding_finished_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "embedding_finished"})
        assert ev.event_id.startswith("ls-embedding-")

    def test_embedding_finished_body(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "embedding_finished", "run_name": "ada-002"})
        assert "ada-002" in ev.body

    def test_embedding_error_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "embedding_error"})
        assert ev.event_type == "embedding_error"

    def test_embedding_error_severity(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "embedding_error"})
        assert ev.severity == "ERROR"

    def test_embedding_error_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "embedding_error"})
        assert ev.event_id.startswith("ls-embedding-")

    def test_embedding_error_body_contains_error(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "embedding_error",
            "run_name": "ada-002",
            "error": "quota exceeded",
        })
        assert "quota exceeded" in ev.body


# ---------------------------------------------------------------------------
# TestFromUnknown
# ---------------------------------------------------------------------------

class TestFromUnknown:
    def test_unknown_event_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "some_new_event"})
        assert ev.event_type == "unknown_langsmith_event"

    def test_unknown_severity(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "some_new_event"})
        assert ev.severity == "INFO"

    def test_unknown_event_id_prefix(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "some_new_event"})
        assert ev.event_id.startswith("ls-unknown-")

    def test_unknown_body_contains_type(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "some_new_event"})
        assert "some_new_event" in ev.body

    def test_unknown_original_type_attr(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "some_new_event"})
        assert ev.attributes["original_type"] == "some_new_event"

    def test_empty_type_maps_to_unknown(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({})
        assert ev.event_type == "unknown_langsmith_event"

    def test_missing_type_body(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({})
        assert "unknown" in ev.body


# ---------------------------------------------------------------------------
# TestTimestampParsing
# ---------------------------------------------------------------------------

class TestTimestampParsing:
    def test_iso_timestamp_parsed(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "run_started",
            "timestamp": "2024-01-15T10:30:00+00:00",
        })
        assert ev.timestamp.year == 2024
        assert ev.timestamp.month == 1
        assert ev.timestamp.day == 15

    def test_z_suffix_timestamp_parsed(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "run_started",
            "timestamp": "2024-06-01T12:00:00Z",
        })
        assert ev.timestamp.year == 2024
        assert ev.timestamp.month == 6

    def test_invalid_timestamp_falls_back_to_now(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({
            "type": "run_started",
            "timestamp": "not-a-date",
        })
        assert isinstance(ev.timestamp, datetime)

    def test_missing_timestamp_falls_back_to_now(self):
        adapter = _make_adapter()
        ev = adapter.to_sentinel_event({"type": "run_started"})
        assert isinstance(ev.timestamp, datetime)


# ---------------------------------------------------------------------------
# TestDrainFlush
# ---------------------------------------------------------------------------

class TestDrainFlush:
    def test_drain_returns_events(self):
        adapter = _make_adapter()
        adapter._buffer_event(adapter.to_sentinel_event({"type": "run_started"}))
        events = adapter.drain()
        assert len(events) == 1

    def test_drain_clears_buffer(self):
        adapter = _make_adapter()
        adapter._buffer_event(adapter.to_sentinel_event({"type": "run_started"}))
        adapter.drain()
        assert adapter.drain() == []

    def test_drain_returns_copy(self):
        adapter = _make_adapter()
        adapter._buffer_event(adapter.to_sentinel_event({"type": "run_started"}))
        events = adapter.drain()
        events.clear()
        assert adapter.drain() == []  # buffer was already cleared

    def test_flush_into_ingest(self):
        adapter = _make_adapter()
        sentinel = Sentinel()
        adapter._buffer_event(adapter.to_sentinel_event({"type": "run_started"}))
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()
        assert isinstance(violations, list)

    def test_flush_into_clears_buffer(self):
        adapter = _make_adapter()
        sentinel = Sentinel()
        adapter._buffer_event(adapter.to_sentinel_event({"type": "run_started"}))
        adapter.flush_into(sentinel)
        assert adapter.drain() == []

    def test_multiple_events_drained(self):
        adapter = _make_adapter()
        for t in ["run_started", "chain_finished", "llm_finished"]:
            adapter._buffer_event(adapter.to_sentinel_event({"type": t}))
        events = adapter.drain()
        assert len(events) == 3


# ---------------------------------------------------------------------------
# TestBufferThreadSafety
# ---------------------------------------------------------------------------

class TestBufferThreadSafety:
    def test_concurrent_buffer_writes(self):
        """Multiple threads can buffer events without data corruption."""
        adapter = _make_adapter()
        errors = []

        def worker():
            try:
                for _ in range(50):
                    adapter._buffer_event(
                        adapter.to_sentinel_event({"type": "run_started"})
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        events = adapter.drain()
        assert len(events) == 500

    def test_drain_thread_safe(self):
        """Drain called from multiple threads does not double-return events."""
        adapter = _make_adapter()
        for _ in range(100):
            adapter._buffer_event(adapter.to_sentinel_event({"type": "run_started"}))

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
# TestSetup — intercept cycle tests
# ---------------------------------------------------------------------------

class TestSetup:
    def test_setup_wraps_create_run(self):
        adapter, client = _setup_adapter()
        original = MagicMock()
        # After setup, create_run is the interceptor, not the original mock
        assert client.create_run is not original

    def test_create_run_emits_run_started(self):
        adapter, client = _setup_adapter()
        _create_run(client, name="test-chain", run_type="chain")
        events = adapter.drain()
        assert any(e.event_type == "run_started" for e in events)

    def test_create_run_stores_inflight(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="test-chain", run_type="chain")
        assert run_id in adapter._inflight

    def test_update_run_removes_inflight(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="test-chain", run_type="chain")
        _update_run(client, run_id, outputs={"result": "done"})
        assert run_id not in adapter._inflight

    def test_full_chain_cycle_emits_two_events(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="rag-chain", run_type="chain")
        _update_run(client, run_id, outputs={"answer": "42"})
        events = adapter.drain()
        types = [e.event_type for e in events]
        assert "run_started" in types
        assert "chain_finished" in types

    def test_full_llm_cycle(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="gpt-call", run_type="llm")
        _update_run(client, run_id, outputs={"text": "hello"})
        events = adapter.drain()
        assert any(e.event_type == "llm_finished" for e in events)

    def test_full_tool_cycle(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="search", run_type="tool")
        _update_run(client, run_id, outputs={"results": []})
        events = adapter.drain()
        assert any(e.event_type == "tool_finished" for e in events)

    def test_full_retriever_cycle(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="faiss", run_type="retriever")
        _update_run(client, run_id, outputs={"docs": []})
        events = adapter.drain()
        assert any(e.event_type == "retriever_finished" for e in events)

    def test_full_embedding_cycle(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="ada-002", run_type="embedding")
        _update_run(client, run_id, outputs={"vectors": []})
        events = adapter.drain()
        assert any(e.event_type == "embedding_finished" for e in events)

    def test_error_run_emits_chain_error(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="chain", run_type="chain")
        _update_run(client, run_id, error="something went wrong")
        events = adapter.drain()
        assert any(e.event_type == "chain_error" for e in events)

    def test_error_run_emits_llm_error(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="gpt-call", run_type="llm")
        _update_run(client, run_id, error="rate limit")
        events = adapter.drain()
        assert any(e.event_type == "llm_error" for e in events)

    def test_error_run_emits_tool_error(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="search", run_type="tool")
        _update_run(client, run_id, error="connection refused")
        events = adapter.drain()
        assert any(e.event_type == "tool_error" for e in events)

    def test_error_run_emits_retriever_error(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="faiss", run_type="retriever")
        _update_run(client, run_id, error="index missing")
        events = adapter.drain()
        assert any(e.event_type == "retriever_error" for e in events)

    def test_error_run_emits_embedding_error(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="embed", run_type="embedding")
        _update_run(client, run_id, error="quota exceeded")
        events = adapter.drain()
        assert any(e.event_type == "embedding_error" for e in events)

    def test_update_without_create_does_not_crash(self):
        """update_run for an unknown run_id is silently ignored."""
        adapter, client = _setup_adapter()
        _update_run(client, "nonexistent-run-id", outputs={"x": 1})
        events = adapter.drain()
        assert events == []

    def test_original_create_run_is_called(self):
        adapter, client = _setup_adapter()
        original_mock = MagicMock()
        # Re-wrap: verify the original is forwarded
        adapter2 = _make_adapter()
        mock_client2 = _make_mock_client()
        original_create = mock_client2.create_run
        adapter2.setup(mock_client2)
        _create_run(mock_client2, name="test", run_type="chain")
        original_create.assert_called_once()

    def test_original_update_run_is_called(self):
        adapter, client = _setup_adapter()
        # Capture original mock before setup (already captured in _make_mock_client)
        adapter2 = _make_adapter()
        mock_client2 = _make_mock_client()
        original_update = mock_client2.update_run
        adapter2.setup(mock_client2)
        run_id = _create_run(mock_client2, name="test", run_type="chain")
        _update_run(mock_client2, run_id)
        original_update.assert_called_once()

    def test_run_id_propagated_as_trace_id(self):
        adapter, client = _setup_adapter(run_id="session-99")
        _create_run(client, name="chain", run_type="chain")
        events = adapter.drain()
        assert all(e.trace_id == "session-99" for e in events)

    def test_ls_trace_id_used_when_no_run_id(self):
        adapter, client = _setup_adapter(run_id=None)
        ls_trace = str(uuid.uuid4())
        run_id = _create_run(client, name="chain", run_type="chain", trace_id=ls_trace)
        _update_run(client, run_id, outputs={})
        events = adapter.drain()
        for ev in events:
            if ev.event_type != "run_started":
                assert ev.trace_id == ls_trace

    def test_tags_propagated(self):
        adapter, client = _setup_adapter()
        run_id = _create_run(client, name="chain", run_type="chain",
                              tags=["prod", "v2"])
        _update_run(client, run_id, outputs={})
        events = adapter.drain()
        for ev in events:
            assert ev.attributes.get("tags") == ["prod", "v2"]

    def test_metadata_merged_from_create_and_update(self):
        """Metadata from create_run and update_run extra are merged."""
        adapter, client = _setup_adapter()
        create_extra = {"metadata": {"env": "prod"}}
        run_id = _create_run(client, name="chain", run_type="chain", extra=create_extra)
        update_extra = {"metadata": {"version": "2"}}
        _update_run(client, run_id, outputs={}, extra=update_extra)
        events = adapter.drain()
        end_ev = next(e for e in events if e.event_type == "chain_finished")
        assert end_ev.attributes["metadata"].get("env") == "prod"
        assert end_ev.attributes["metadata"].get("version") == "2"

    def test_positional_args_to_create_run(self):
        """Positional args (name, inputs, run_type) are extracted correctly."""
        adapter, client = _setup_adapter()
        run_id = str(uuid.uuid4())
        # Call with positional args as some LangSmith internals may do
        client.create_run(
            "pos-chain",
            {"q": "test"},
            "chain",
            id=run_id,
            trace_id=run_id,
            parent_run_id="",
            tags=[],
            extra={},
        )
        events = adapter.drain()
        started = next(e for e in events if e.event_type == "run_started")
        assert started.attributes["run_name"] == "pos-chain"

    def test_inflight_thread_safe(self):
        """Concurrent create_run / update_run calls don't corrupt _inflight."""
        adapter, client = _setup_adapter()
        errors = []

        def full_cycle():
            try:
                run_id = _create_run(client, name="chain", run_type="chain")
                _update_run(client, run_id, outputs={"ok": True})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=full_cycle) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert adapter._inflight == {}  # all runs completed


# ---------------------------------------------------------------------------
# TestSafeJson
# ---------------------------------------------------------------------------

class TestSafeJson:
    def test_none_returns_empty_string(self):
        from agentcop.adapters.langsmith import _safe_json
        assert _safe_json(None) == ""

    def test_string_passthrough(self):
        from agentcop.adapters.langsmith import _safe_json
        assert _safe_json("hello") == "hello"

    def test_string_truncated(self):
        from agentcop.adapters.langsmith import _safe_json
        long = "x" * 600
        assert len(_safe_json(long)) == 500

    def test_dict_serialized(self):
        from agentcop.adapters.langsmith import _safe_json
        result = _safe_json({"key": "value"})
        assert "key" in result
        assert "value" in result

    def test_dict_truncated_to_500(self):
        from agentcop.adapters.langsmith import _safe_json
        big = {"k" * i: "v" * 50 for i in range(1, 30)}
        result = _safe_json(big)
        assert len(result) <= 500

    def test_list_serialized(self):
        from agentcop.adapters.langsmith import _safe_json
        result = _safe_json([1, 2, 3])
        assert result == "[1, 2, 3]"

    def test_non_serializable_falls_back_to_str(self):
        from agentcop.adapters.langsmith import _safe_json

        class Weird:
            def __repr__(self):
                return "Weird()"

        result = _safe_json(Weird())
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# TestProtocolConformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    def test_is_sentinel_adapter(self):
        from agentcop.adapters.base import SentinelAdapter
        adapter = _make_adapter()
        assert isinstance(adapter, SentinelAdapter)

    def test_has_source_system(self):
        adapter = _make_adapter()
        assert hasattr(adapter, "source_system")
        assert isinstance(adapter.source_system, str)

    def test_has_to_sentinel_event(self):
        adapter = _make_adapter()
        assert callable(adapter.to_sentinel_event)

    def test_to_sentinel_event_returns_sentinel_event(self):
        adapter = _make_adapter()
        result = adapter.to_sentinel_event({"type": "run_started"})
        assert isinstance(result, SentinelEvent)


# ---------------------------------------------------------------------------
# TestSentinelIntegration
# ---------------------------------------------------------------------------

class TestSentinelIntegration:
    def test_flush_into_sentinel_ingests_all_events(self):
        adapter, client = _setup_adapter(run_id="test-run")
        run_id = _create_run(client, name="rag-chain", run_type="chain")
        _update_run(client, run_id, outputs={"answer": "42"})
        sentinel = Sentinel()
        adapter.flush_into(sentinel)
        assert len(sentinel._events) == 2  # run_started + chain_finished

    def test_custom_detector_fires_on_chain_error(self):
        from typing import Optional

        def detect_chain_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
            if event.event_type != "chain_error":
                return None
            return ViolationRecord(
                violation_type="chain_failed",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={"error": event.attributes.get("error", "")},
            )

        adapter, client = _setup_adapter(run_id="ci-run")
        run_id = _create_run(client, name="rag-chain", run_type="chain")
        _update_run(client, run_id, error="context length exceeded")
        sentinel = Sentinel(detectors=[detect_chain_failure])
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "chain_failed"

    def test_custom_detector_fires_on_llm_error(self):
        from typing import Optional

        def detect_llm_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
            if event.event_type != "llm_error":
                return None
            return ViolationRecord(
                violation_type="llm_failed",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={"model": event.attributes.get("model", "unknown")},
            )

        adapter, client = _setup_adapter(run_id="ci-run")
        run_id = _create_run(client, name="gpt-call", run_type="llm",
                              extra={"metadata": {"ls_model_name": "gpt-4o"}})
        _update_run(client, run_id, error="quota exceeded")
        sentinel = Sentinel(detectors=[detect_llm_failure])
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "llm_failed"
        assert violations[0].detail["model"] == "gpt-4o"

    def test_no_violations_on_clean_run(self):
        from typing import Optional

        def detect_errors(event: SentinelEvent) -> Optional[ViolationRecord]:
            if "error" not in event.event_type:
                return None
            return ViolationRecord(
                violation_type="run_error",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={},
            )

        adapter, client = _setup_adapter(run_id="clean-run")
        run_id = _create_run(client, name="chain", run_type="chain")
        _update_run(client, run_id, outputs={"result": "ok"})
        sentinel = Sentinel(detectors=[detect_errors])
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()
        assert violations == []

    def test_multiple_run_cycles(self):
        adapter, client = _setup_adapter(run_id="multi-run")
        for i in range(3):
            run_id = _create_run(client, name=f"chain-{i}", run_type="chain")
            _update_run(client, run_id, outputs={"i": i})
        events = adapter.drain()
        started = [e for e in events if e.event_type == "run_started"]
        finished = [e for e in events if e.event_type == "chain_finished"]
        assert len(started) == 3
        assert len(finished) == 3

    def test_mixed_run_types_in_pipeline(self):
        adapter, client = _setup_adapter(run_id="pipeline-run")
        chain_id = _create_run(client, name="rag-chain", run_type="chain")
        retriever_id = _create_run(client, name="faiss", run_type="retriever",
                                   parent_run_id=chain_id)
        llm_id = _create_run(client, name="gpt-call", run_type="llm",
                              parent_run_id=chain_id)
        _update_run(client, retriever_id, outputs={"docs": []})
        _update_run(client, llm_id, outputs={"text": "answer"})
        _update_run(client, chain_id, outputs={"answer": "answer"})
        events = adapter.drain()
        types = {e.event_type for e in events}
        assert "run_started" in types
        assert "retriever_finished" in types
        assert "llm_finished" in types
        assert "chain_finished" in types
