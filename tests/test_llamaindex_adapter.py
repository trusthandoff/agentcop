"""Tests for LlamaIndexSentinelAdapter. No llama-index install required."""

import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentcop import Sentinel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(run_id=None):
    """Construct a LlamaIndexSentinelAdapter with the guard bypassed."""
    with patch("agentcop.adapters.llamaindex._require_llamaindex"):
        from agentcop.adapters.llamaindex import LlamaIndexSentinelAdapter

        return LlamaIndexSentinelAdapter(run_id=run_id)


@pytest.fixture()
def adapter():
    return _make_adapter(run_id="run-001")


@pytest.fixture()
def adapter_no_run():
    return _make_adapter(run_id=None)


# ---------------------------------------------------------------------------
# Guard / import
# ---------------------------------------------------------------------------


class TestRequireLlamaIndex:
    def test_raises_import_error_when_llamaindex_missing(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name in ("llama_index", "llama_index.core"):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            from agentcop.adapters.llamaindex import _require_llamaindex

            with pytest.raises(ImportError, match="pip install agentcop\\[llamaindex\\]"):
                _require_llamaindex()

    def test_does_not_raise_when_llamaindex_present(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name in ("llama_index", "llama_index.core"):
                return MagicMock()
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            from agentcop.adapters.llamaindex import _require_llamaindex

            _require_llamaindex()

    def test_constructor_calls_require_llamaindex(self):
        with patch("agentcop.adapters.llamaindex._require_llamaindex") as mock_guard:
            from agentcop.adapters.llamaindex import LlamaIndexSentinelAdapter

            LlamaIndexSentinelAdapter()
            mock_guard.assert_called_once()


# ---------------------------------------------------------------------------
# Adapter init
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_source_system_is_llamaindex(self, adapter):
        assert adapter.source_system == "llamaindex"

    def test_run_id_stored(self):
        a = _make_adapter(run_id="my-run")
        assert a._run_id == "my-run"

    def test_run_id_defaults_to_none(self, adapter_no_run):
        assert adapter_no_run._run_id is None

    def test_buffer_starts_empty(self, adapter):
        assert adapter.drain() == []


# ---------------------------------------------------------------------------
# Query events
# ---------------------------------------------------------------------------


class TestFromQueryEvents:
    def test_query_started_event_type(self, adapter):
        e = adapter.to_sentinel_event({"type": "query_started", "query_str": "What is RAG?"})
        assert e.event_type == "query_started"

    def test_query_started_severity_info(self, adapter):
        e = adapter.to_sentinel_event({"type": "query_started", "query_str": "test"})
        assert e.severity == "INFO"

    def test_query_started_body_contains_query(self, adapter):
        e = adapter.to_sentinel_event({"type": "query_started", "query_str": "What is RAG?"})
        assert "What is RAG?" in e.body

    def test_query_started_attributes(self, adapter):
        e = adapter.to_sentinel_event({"type": "query_started", "query_str": "What is RAG?"})
        assert e.attributes["query_str"] == "What is RAG?"

    def test_query_started_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event({"type": "query_started", "query_str": "q"})
        assert e.event_id.startswith("llamaindex-query-")

    def test_query_started_source_system(self, adapter):
        e = adapter.to_sentinel_event({"type": "query_started", "query_str": "q"})
        assert e.source_system == "llamaindex"

    def test_query_started_trace_id(self, adapter):
        e = adapter.to_sentinel_event({"type": "query_started", "query_str": "q"})
        assert e.trace_id == "run-001"

    def test_query_started_defaults_empty_query(self, adapter):
        e = adapter.to_sentinel_event({"type": "query_started"})
        assert e.attributes["query_str"] == ""

    def test_query_finished_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "query_finished", "query_str": "q", "response": "answer"}
        )
        assert e.event_type == "query_finished"

    def test_query_finished_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "query_finished", "query_str": "q", "response": "r"}
        )
        assert e.severity == "INFO"

    def test_query_finished_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "query_finished", "query_str": "What is RAG?", "response": "RAG is..."}
        )
        assert e.attributes["query_str"] == "What is RAG?"
        assert e.attributes["response"] == "RAG is..."

    def test_query_error_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "query_error", "query_str": "q", "error": "timeout"}
        )
        assert e.event_type == "query_error"

    def test_query_error_severity_error(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "query_error", "query_str": "q", "error": "timeout"}
        )
        assert e.severity == "ERROR"

    def test_query_error_body_contains_error(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "query_error", "query_str": "q", "error": "index not found"}
        )
        assert "index not found" in e.body

    def test_query_error_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "query_error", "query_str": "q", "error": "timeout"}
        )
        assert e.attributes["query_str"] == "q"
        assert e.attributes["error"] == "timeout"


# ---------------------------------------------------------------------------
# Retrieval events
# ---------------------------------------------------------------------------


class TestFromRetrievalEvents:
    def test_retrieval_started_event_type(self, adapter):
        e = adapter.to_sentinel_event({"type": "retrieval_started", "query_str": "q"})
        assert e.event_type == "retrieval_started"

    def test_retrieval_started_severity_info(self, adapter):
        e = adapter.to_sentinel_event({"type": "retrieval_started", "query_str": "q"})
        assert e.severity == "INFO"

    def test_retrieval_started_attributes(self, adapter):
        e = adapter.to_sentinel_event({"type": "retrieval_started", "query_str": "best practices"})
        assert e.attributes["query_str"] == "best practices"

    def test_retrieval_started_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event({"type": "retrieval_started", "query_str": "q"})
        assert e.event_id.startswith("llamaindex-retrieval-")

    def test_retrieval_finished_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "retrieval_finished", "query_str": "q", "num_nodes": 5}
        )
        assert e.event_type == "retrieval_finished"

    def test_retrieval_finished_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "retrieval_finished", "query_str": "q", "num_nodes": 3}
        )
        assert e.severity == "INFO"

    def test_retrieval_finished_body_contains_node_count(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "retrieval_finished", "query_str": "q", "num_nodes": 7}
        )
        assert "7" in e.body

    def test_retrieval_finished_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "retrieval_finished", "query_str": "q", "num_nodes": 4}
        )
        assert e.attributes["num_nodes"] == 4
        assert e.attributes["query_str"] == "q"

    def test_retrieval_finished_zero_nodes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "retrieval_finished", "query_str": "q", "num_nodes": 0}
        )
        assert e.attributes["num_nodes"] == 0

    def test_retrieval_error_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "retrieval_error", "query_str": "q", "error": "vectordb down"}
        )
        assert e.event_type == "retrieval_error"

    def test_retrieval_error_severity_error(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "retrieval_error", "query_str": "q", "error": "down"}
        )
        assert e.severity == "ERROR"

    def test_retrieval_error_body_contains_error(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "retrieval_error", "query_str": "q", "error": "connection refused"}
        )
        assert "connection refused" in e.body

    def test_retrieval_error_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "retrieval_error", "query_str": "q", "error": "timeout"}
        )
        assert e.attributes["query_str"] == "q"
        assert e.attributes["error"] == "timeout"


# ---------------------------------------------------------------------------
# LLM events
# ---------------------------------------------------------------------------


class TestFromLLMEvents:
    def test_llm_predict_started_event_type(self, adapter):
        e = adapter.to_sentinel_event({"type": "llm_predict_started", "model_name": "gpt-4o"})
        assert e.event_type == "llm_predict_started"

    def test_llm_predict_started_severity_info(self, adapter):
        e = adapter.to_sentinel_event({"type": "llm_predict_started", "model_name": "gpt-4o"})
        assert e.severity == "INFO"

    def test_llm_predict_started_body_contains_model(self, adapter):
        e = adapter.to_sentinel_event({"type": "llm_predict_started", "model_name": "gpt-4o"})
        assert "gpt-4o" in e.body

    def test_llm_predict_started_attributes(self, adapter):
        e = adapter.to_sentinel_event({"type": "llm_predict_started", "model_name": "gpt-4o"})
        assert e.attributes["model_name"] == "gpt-4o"

    def test_llm_predict_started_query_str_included_when_present(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "llm_predict_started", "model_name": "gpt-4o", "query_str": "prompt"}
        )
        assert e.attributes["query_str"] == "prompt"

    def test_llm_predict_started_query_str_absent_when_empty(self, adapter):
        e = adapter.to_sentinel_event({"type": "llm_predict_started", "model_name": "gpt-4o"})
        assert "query_str" not in e.attributes

    def test_llm_predict_started_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event({"type": "llm_predict_started", "model_name": "m"})
        assert e.event_id.startswith("llamaindex-llm-")

    def test_llm_predict_finished_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "llm_predict_finished", "model_name": "gpt-4o", "response": "result"}
        )
        assert e.event_type == "llm_predict_finished"

    def test_llm_predict_finished_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "llm_predict_finished", "model_name": "gpt-4o", "response": "r"}
        )
        assert e.severity == "INFO"

    def test_llm_predict_finished_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "llm_predict_finished", "model_name": "gpt-4o", "response": "42"}
        )
        assert e.attributes["model_name"] == "gpt-4o"
        assert e.attributes["response"] == "42"

    def test_llm_predict_error_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "llm_predict_error", "model_name": "gpt-4o", "error": "rate limit"}
        )
        assert e.event_type == "llm_predict_error"

    def test_llm_predict_error_severity_error(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "llm_predict_error", "model_name": "gpt-4o", "error": "429"}
        )
        assert e.severity == "ERROR"

    def test_llm_predict_error_body_contains_model_and_error(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "llm_predict_error", "model_name": "gpt-4o", "error": "rate limit exceeded"}
        )
        assert "gpt-4o" in e.body
        assert "rate limit exceeded" in e.body

    def test_llm_predict_error_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "llm_predict_error", "model_name": "gpt-4o", "error": "timeout"}
        )
        assert e.attributes["model_name"] == "gpt-4o"
        assert e.attributes["error"] == "timeout"

    def test_llm_predict_started_defaults_unknown_model(self, adapter):
        e = adapter.to_sentinel_event({"type": "llm_predict_started"})
        assert e.attributes["model_name"] == "unknown"

    def test_trace_id_propagated(self, adapter):
        e = adapter.to_sentinel_event({"type": "llm_predict_started", "model_name": "m"})
        assert e.trace_id == "run-001"

    def test_trace_id_none_when_no_run_id(self, adapter_no_run):
        e = adapter_no_run.to_sentinel_event({"type": "llm_predict_started", "model_name": "m"})
        assert e.trace_id is None


# ---------------------------------------------------------------------------
# Agent events
# ---------------------------------------------------------------------------


class TestFromAgentEvents:
    def test_agent_step_started_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_step_started", "task_id": "t1", "step_num": 1, "input": "plan"}
        )
        assert e.event_type == "agent_step_started"

    def test_agent_step_started_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_step_started", "task_id": "t1", "step_num": 0, "input": ""}
        )
        assert e.severity == "INFO"

    def test_agent_step_started_body_contains_step_and_task(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_step_started", "task_id": "task-99", "step_num": 3, "input": ""}
        )
        assert "3" in e.body
        assert "task-99" in e.body

    def test_agent_step_started_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_step_started", "task_id": "t1", "step_num": 2, "input": "do X"}
        )
        assert e.attributes["task_id"] == "t1"
        assert e.attributes["step_num"] == 2
        assert e.attributes["input"] == "do X"

    def test_agent_step_started_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_step_started", "task_id": "t", "step_num": 0, "input": ""}
        )
        assert e.event_id.startswith("llamaindex-agent-")

    def test_agent_step_finished_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "agent_step_finished",
                "task_id": "t1",
                "step_num": 1,
                "output": "done",
                "is_last": False,
            }
        )
        assert e.event_type == "agent_step_finished"

    def test_agent_step_finished_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "agent_step_finished",
                "task_id": "t",
                "step_num": 0,
                "output": "",
                "is_last": True,
            }
        )
        assert e.severity == "INFO"

    def test_agent_step_finished_body_contains_is_last(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "agent_step_finished",
                "task_id": "t",
                "step_num": 1,
                "output": "",
                "is_last": True,
            }
        )
        assert "True" in e.body

    def test_agent_step_finished_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "agent_step_finished",
                "task_id": "t1",
                "step_num": 2,
                "output": "result",
                "is_last": True,
            }
        )
        assert e.attributes["task_id"] == "t1"
        assert e.attributes["step_num"] == 2
        assert e.attributes["output"] == "result"
        assert e.attributes["is_last"] is True

    def test_agent_step_finished_is_last_false(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "agent_step_finished",
                "task_id": "t",
                "step_num": 0,
                "output": "",
                "is_last": False,
            }
        )
        assert e.attributes["is_last"] is False

    def test_agent_tool_call_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_tool_call", "tool_name": "web_search", "tool_input": '{"q":"test"}'}
        )
        assert e.event_type == "agent_tool_call"

    def test_agent_tool_call_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_tool_call", "tool_name": "fn", "tool_input": ""}
        )
        assert e.severity == "INFO"

    def test_agent_tool_call_body_contains_tool_name(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_tool_call", "tool_name": "calculator", "tool_input": ""}
        )
        assert "calculator" in e.body

    def test_agent_tool_call_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_tool_call", "tool_name": "web_search", "tool_input": '{"q":"AI"}'}
        )
        assert e.attributes["tool_name"] == "web_search"
        assert e.attributes["tool_input"] == '{"q":"AI"}'

    def test_agent_tool_call_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_tool_call", "tool_name": "fn", "tool_input": ""}
        )
        assert e.event_id.startswith("llamaindex-agent-")

    def test_agent_tool_call_defaults_unknown_tool(self, adapter):
        e = adapter.to_sentinel_event({"type": "agent_tool_call"})
        assert e.attributes["tool_name"] == "unknown"


# ---------------------------------------------------------------------------
# Embedding events
# ---------------------------------------------------------------------------


class TestFromEmbeddingEvents:
    def test_embedding_started_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "embedding_started", "model_name": "text-embedding-3-small", "num_chunks": 10}
        )
        assert e.event_type == "embedding_started"

    def test_embedding_started_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "embedding_started", "model_name": "m", "num_chunks": 0}
        )
        assert e.severity == "INFO"

    def test_embedding_started_body_contains_model_and_chunks(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "embedding_started", "model_name": "ada-002", "num_chunks": 5}
        )
        assert "ada-002" in e.body
        assert "5" in e.body

    def test_embedding_started_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "embedding_started", "model_name": "ada-002", "num_chunks": 8}
        )
        assert e.attributes["model_name"] == "ada-002"
        assert e.attributes["num_chunks"] == 8

    def test_embedding_started_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "embedding_started", "model_name": "m", "num_chunks": 0}
        )
        assert e.event_id.startswith("llamaindex-embed-")

    def test_embedding_finished_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "embedding_finished", "model_name": "ada-002", "num_chunks": 5}
        )
        assert e.event_type == "embedding_finished"

    def test_embedding_finished_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "embedding_finished", "model_name": "m", "num_chunks": 0}
        )
        assert e.severity == "INFO"

    def test_embedding_finished_body_contains_model_and_chunks(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "embedding_finished", "model_name": "ada-002", "num_chunks": 12}
        )
        assert "ada-002" in e.body
        assert "12" in e.body

    def test_embedding_finished_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "embedding_finished", "model_name": "ada-002", "num_chunks": 3}
        )
        assert e.attributes["model_name"] == "ada-002"
        assert e.attributes["num_chunks"] == 3

    def test_embedding_finished_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "embedding_finished", "model_name": "m", "num_chunks": 0}
        )
        assert e.event_id.startswith("llamaindex-embed-")


# ---------------------------------------------------------------------------
# Unknown events
# ---------------------------------------------------------------------------


class TestFromUnknown:
    def test_unknown_type_gives_unknown_llamaindex_event(self, adapter):
        e = adapter.to_sentinel_event({"type": "some_future_event"})
        assert e.event_type == "unknown_llamaindex_event"

    def test_unknown_severity_info(self, adapter):
        e = adapter.to_sentinel_event({"type": "whatever"})
        assert e.severity == "INFO"

    def test_unknown_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event({"type": "x"})
        assert e.event_id.startswith("llamaindex-unknown-")

    def test_unknown_attributes_original_type(self, adapter):
        e = adapter.to_sentinel_event({"type": "mystery_event"})
        assert e.attributes["original_type"] == "mystery_event"

    def test_empty_dict_does_not_raise(self, adapter):
        e = adapter.to_sentinel_event({})
        assert e.event_type == "unknown_llamaindex_event"

    def test_missing_type_key_goes_to_unknown(self, adapter):
        e = adapter.to_sentinel_event({"query_str": "q"})
        assert e.event_type == "unknown_llamaindex_event"

    def test_trace_id_on_unknown_is_run_id(self, adapter):
        e = adapter.to_sentinel_event({"type": "x"})
        assert e.trace_id == "run-001"

    def test_source_system_on_unknown_is_llamaindex(self, adapter):
        e = adapter.to_sentinel_event({"type": "x"})
        assert e.source_system == "llamaindex"


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    def test_iso_z_suffix_parsed(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "query_started",
                "query_str": "q",
                "timestamp": "2026-06-01T12:00:00Z",
            }
        )
        assert e.timestamp == datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    def test_iso_offset_parsed(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "query_started",
                "query_str": "q",
                "timestamp": "2026-06-01T12:00:00+00:00",
            }
        )
        assert e.timestamp.year == 2026

    def test_missing_timestamp_uses_now(self, adapter):
        before = datetime.now(UTC)
        e = adapter.to_sentinel_event({"type": "query_started", "query_str": "q"})
        after = datetime.now(UTC)
        assert before <= e.timestamp <= after

    def test_invalid_timestamp_falls_back_to_now(self, adapter):
        before = datetime.now(UTC)
        e = adapter.to_sentinel_event(
            {
                "type": "query_started",
                "query_str": "q",
                "timestamp": "not-a-date",
            }
        )
        after = datetime.now(UTC)
        assert before <= e.timestamp <= after


# ---------------------------------------------------------------------------
# drain() and flush_into()
# ---------------------------------------------------------------------------


class TestDrainFlush:
    def _push(self, adapter, *types):
        for t in types:
            adapter._buffer_event(
                adapter.to_sentinel_event(
                    {
                        "type": t,
                        "query_str": "q",
                        "response": "r",
                        "error": "e",
                        "model_name": "m",
                        "num_chunks": 0,
                        "task_id": "t",
                        "step_num": 0,
                        "input": "",
                        "output": "",
                        "is_last": False,
                        "tool_name": "fn",
                        "tool_input": "",
                    }
                )
            )

    def test_drain_returns_buffered_events(self, adapter):
        self._push(adapter, "query_started", "retrieval_started")
        events = adapter.drain()
        assert len(events) == 2

    def test_drain_clears_buffer(self, adapter):
        self._push(adapter, "query_started")
        adapter.drain()
        assert adapter.drain() == []

    def test_drain_empty_buffer_returns_empty_list(self, adapter):
        assert adapter.drain() == []

    def test_drain_returns_correct_event_types(self, adapter):
        self._push(adapter, "query_started", "query_error")
        events = adapter.drain()
        assert events[0].event_type == "query_started"
        assert events[1].event_type == "query_error"

    def test_flush_into_ingests_events(self, adapter):
        self._push(adapter, "llm_predict_error")
        sentinel = Sentinel()
        adapter.flush_into(sentinel)
        assert len(sentinel._events) == 1

    def test_flush_into_clears_buffer(self, adapter):
        self._push(adapter, "query_started")
        sentinel = Sentinel()
        adapter.flush_into(sentinel)
        assert adapter.drain() == []

    def test_flush_into_empty_buffer_is_noop(self, adapter):
        sentinel = Sentinel()
        adapter.flush_into(sentinel)
        assert sentinel._events == []

    def test_multiple_flush_calls_replace_sentinel_buffer(self, adapter):
        sentinel = Sentinel()
        self._push(adapter, "query_started")
        adapter.flush_into(sentinel)
        self._push(adapter, "query_finished")
        adapter.flush_into(sentinel)
        # Sentinel.ingest replaces buffer each call
        assert len(sentinel._events) == 1


# ---------------------------------------------------------------------------
# Buffer thread safety
# ---------------------------------------------------------------------------


class TestBufferThreadSafety:
    def test_concurrent_buffer_event_does_not_corrupt(self, adapter):
        errors = []

        def worker(i):
            try:
                adapter._buffer_event(
                    adapter.to_sentinel_event({"type": "query_started", "query_str": f"q-{i}"})
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(adapter.drain()) == 50

    def test_concurrent_drain_and_buffer_does_not_raise(self, adapter):
        errors = []

        def bufferer(i):
            try:
                adapter._buffer_event(
                    adapter.to_sentinel_event({"type": "retrieval_started", "query_str": f"q-{i}"})
                )
            except Exception as e:
                errors.append(e)

        def drainer():
            try:
                adapter.drain()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=bufferer, args=(i,)) for i in range(25)] + [
            threading.Thread(target=drainer) for _ in range(25)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ---------------------------------------------------------------------------
# setup() — mocked dispatcher
# ---------------------------------------------------------------------------


class TestSetup:
    def _mock_modules(self):
        """Return a sys.modules patch dict with all LlamaIndex submodules mocked."""
        base = MagicMock()
        return {
            "llama_index": base,
            "llama_index.core": base,
            "llama_index.core.instrumentation": MagicMock(),
            "llama_index.core.instrumentation.event_handlers": MagicMock(),
            "llama_index.core.instrumentation.events": MagicMock(),
            "llama_index.core.instrumentation.events.query": MagicMock(),
            "llama_index.core.instrumentation.events.retrieval": MagicMock(),
            "llama_index.core.instrumentation.events.llm": MagicMock(),
            "llama_index.core.instrumentation.events.agent": MagicMock(),
            "llama_index.core.instrumentation.events.embedding": MagicMock(),
        }

    def test_setup_registers_handler_with_dispatcher(self):
        adapter = _make_adapter()
        mock_dispatcher = MagicMock()

        with patch.dict("sys.modules", self._mock_modules()):
            adapter.setup(mock_dispatcher)

        mock_dispatcher.add_event_handler.assert_called_once()

    def test_setup_uses_default_dispatcher_when_none_given(self):
        adapter = _make_adapter()
        mock_dispatcher = MagicMock()
        mock_instrumentation = MagicMock()
        mock_instrumentation.get_dispatcher.return_value = mock_dispatcher

        modules = self._mock_modules()
        modules["llama_index.core.instrumentation"] = mock_instrumentation

        with patch.dict("sys.modules", modules):
            adapter.setup()  # no dispatcher argument

        mock_dispatcher.add_event_handler.assert_called_once()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_sentinel_adapter_protocol(self, adapter):
        from agentcop import SentinelAdapter

        assert isinstance(adapter, SentinelAdapter)

    def test_source_system_attribute(self, adapter):
        assert adapter.source_system == "llamaindex"

    def test_to_sentinel_event_callable(self, adapter):
        assert callable(adapter.to_sentinel_event)


# ---------------------------------------------------------------------------
# Integration with Sentinel
# ---------------------------------------------------------------------------


class TestSentinelIntegration:
    def test_all_error_events_collected(self, adapter):
        sentinel = Sentinel()

        for raw in [
            {"type": "query_error", "query_str": "q", "error": "index missing"},
            {"type": "retrieval_error", "query_str": "q", "error": "vectordb down"},
            {"type": "llm_predict_error", "model_name": "gpt-4o", "error": "rate limit"},
        ]:
            adapter._buffer_event(adapter.to_sentinel_event(raw))

        adapter.flush_into(sentinel)
        assert len(sentinel._events) == 3
        assert all(e.severity == "ERROR" for e in sentinel._events)

    def test_custom_detector_fires_on_llm_error(self, adapter):
        from agentcop import ViolationRecord

        def detect_llm_failure(event):
            if event.event_type == "llm_predict_error":
                return ViolationRecord(
                    violation_type="llm_call_failed",
                    severity="ERROR",
                    source_event_id=event.event_id,
                    trace_id=event.trace_id,
                    detail={
                        "model": event.attributes.get("model_name"),
                        "error": event.attributes.get("error"),
                    },
                )

        adapter._buffer_event(
            adapter.to_sentinel_event(
                {
                    "type": "llm_predict_error",
                    "model_name": "gpt-4o",
                    "error": "rate limit exceeded",
                }
            )
        )

        sentinel = Sentinel(detectors=[detect_llm_failure])
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()

        assert len(violations) == 1
        assert violations[0].violation_type == "llm_call_failed"
        assert violations[0].detail["model"] == "gpt-4o"
        assert "rate limit" in violations[0].detail["error"]

    def test_custom_detector_fires_on_empty_retrieval(self, adapter):
        from agentcop import ViolationRecord

        def detect_empty_retrieval(event):
            if (
                event.event_type == "retrieval_finished"
                and event.attributes.get("num_nodes", -1) == 0
            ):
                return ViolationRecord(
                    violation_type="empty_retrieval",
                    severity="WARN",
                    source_event_id=event.event_id,
                    trace_id=event.trace_id,
                    detail={"query_str": event.attributes.get("query_str")},
                )

        adapter._buffer_event(
            adapter.to_sentinel_event(
                {
                    "type": "retrieval_finished",
                    "query_str": "obscure query",
                    "num_nodes": 0,
                }
            )
        )

        sentinel = Sentinel(detectors=[detect_empty_retrieval])
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()

        assert len(violations) == 1
        assert violations[0].violation_type == "empty_retrieval"
        assert violations[0].detail["query_str"] == "obscure query"

    def test_custom_detector_fires_on_restricted_tool_call(self, adapter):
        from agentcop import ViolationRecord

        RESTRICTED = {"exec_code", "write_file"}

        def detect_restricted_tool(event):
            if event.event_type == "agent_tool_call":
                tool = event.attributes.get("tool_name", "")
                if tool in RESTRICTED:
                    return ViolationRecord(
                        violation_type="restricted_tool_called",
                        severity="CRITICAL",
                        source_event_id=event.event_id,
                        trace_id=event.trace_id,
                        detail={"tool_name": tool},
                    )

        adapter._buffer_event(
            adapter.to_sentinel_event(
                {
                    "type": "agent_tool_call",
                    "tool_name": "exec_code",
                    "tool_input": "os.system('rm -rf /')",
                }
            )
        )

        sentinel = Sentinel(detectors=[detect_restricted_tool])
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()

        assert len(violations) == 1
        assert violations[0].violation_type == "restricted_tool_called"
        assert violations[0].severity == "CRITICAL"

    def test_trace_id_consistent_across_run(self):
        a = _make_adapter(run_id="session-xyz")
        events_raw = [
            {"type": "query_started", "query_str": "q"},
            {"type": "retrieval_started", "query_str": "q"},
            {"type": "llm_predict_started", "model_name": "gpt-4o"},
            {"type": "query_finished", "query_str": "q", "response": "answer"},
        ]
        events = [a.to_sentinel_event(r) for r in events_raw]
        assert all(e.trace_id == "session-xyz" for e in events)

    def test_no_violations_for_info_events_with_default_detectors(self, adapter):
        sentinel = Sentinel()
        for raw in [
            {"type": "query_started", "query_str": "q"},
            {"type": "retrieval_started", "query_str": "q"},
            {"type": "llm_predict_started", "model_name": "gpt-4o"},
            {"type": "agent_tool_call", "tool_name": "search", "tool_input": "{}"},
            {"type": "embedding_started", "model_name": "ada-002", "num_chunks": 3},
        ]:
            adapter._buffer_event(adapter.to_sentinel_event(raw))
        adapter.flush_into(sentinel)
        assert sentinel.detect_violations() == []


# ---------------------------------------------------------------------------
# Runtime security tests
# ---------------------------------------------------------------------------


def _make_llamaindex_runtime(gate=None, permissions=None, sandbox=None, approvals=None):
    with patch("agentcop.adapters.llamaindex._require_llamaindex"):
        from agentcop.adapters.llamaindex import LlamaIndexSentinelAdapter

        return LlamaIndexSentinelAdapter(
            run_id="rt-run",
            gate=gate,
            permissions=permissions,
            sandbox=sandbox,
            approvals=approvals,
        )


class TestRuntimeSecurityLlamaIndex:
    def test_init_stores_none_by_default(self):
        a = _make_llamaindex_runtime()
        assert a._gate is None
        assert a._permissions is None

    def test_init_stores_runtime_params(self):
        gate = MagicMock()
        perms = MagicMock()
        a = _make_llamaindex_runtime(gate=gate, permissions=perms)
        assert a._gate is gate
        assert a._permissions is perms

    def test_gate_denial_on_agent_tool_call_fires_event(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=False, reason="blocked", risk_score=90)
        a = _make_llamaindex_runtime(gate=gate)
        from agentcop.adapters._runtime import check_tool_call

        with pytest.raises(PermissionError):
            check_tool_call(a, "web_search", {"query": "test"})
        events = a.drain()
        assert any(e.event_type == "gate_denied" for e in events)

    def test_permission_violation_fires_event(self):
        perms = MagicMock()
        perms.verify.return_value = MagicMock(granted=False, reason="not permitted")
        a = _make_llamaindex_runtime(permissions=perms)
        from agentcop.adapters._runtime import check_tool_call

        with pytest.raises(PermissionError):
            check_tool_call(a, "file_reader", {})
        events = a.drain()
        assert any(e.event_type == "permission_violation" for e in events)

    def test_gate_allow_does_not_buffer_error(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=True, reason="ok", risk_score=0)
        a = _make_llamaindex_runtime(gate=gate)
        from agentcop.adapters._runtime import check_tool_call

        check_tool_call(a, "safe_tool", {})
        assert a.drain() == []

    def test_no_gate_backward_compatible(self):
        a = _make_llamaindex_runtime()
        event = a.to_sentinel_event({"type": "agent_tool_call", "tool_name": "search"})
        assert event.event_type == "agent_tool_call"
