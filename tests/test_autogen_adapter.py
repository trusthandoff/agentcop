"""Tests for AutoGenSentinelAdapter. No autogen install required."""

import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentcop import Sentinel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(run_id=None):
    """Construct an AutoGenSentinelAdapter with the autogen guard bypassed."""
    with patch("agentcop.adapters.autogen._require_autogen"):
        from agentcop.adapters.autogen import AutoGenSentinelAdapter

        return AutoGenSentinelAdapter(run_id=run_id)


@pytest.fixture()
def adapter():
    return _make_adapter(run_id="run-001")


@pytest.fixture()
def adapter_no_run():
    return _make_adapter(run_id=None)


# ---------------------------------------------------------------------------
# Guard / import
# ---------------------------------------------------------------------------


class TestRequireAutoGen:
    def test_raises_import_error_when_both_packages_missing(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name in ("autogen", "autogen_agentchat"):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            from agentcop.adapters.autogen import _require_autogen

            with pytest.raises(ImportError, match="pip install agentcop\\[autogen\\]"):
                _require_autogen()

    def test_does_not_raise_when_autogen_present(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "autogen":
                return MagicMock()
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            from agentcop.adapters.autogen import _require_autogen

            _require_autogen()

    def test_does_not_raise_when_autogen_agentchat_present(self):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "autogen":
                raise ImportError("No module named 'autogen'")
            if name == "autogen_agentchat":
                return MagicMock()
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            from agentcop.adapters.autogen import _require_autogen

            _require_autogen()

    def test_constructor_calls_require_autogen(self):
        with patch("agentcop.adapters.autogen._require_autogen") as mock_guard:
            from agentcop.adapters.autogen import AutoGenSentinelAdapter

            AutoGenSentinelAdapter()
            mock_guard.assert_called_once()


# ---------------------------------------------------------------------------
# Adapter init
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_source_system_is_autogen(self, adapter):
        assert adapter.source_system == "autogen"

    def test_run_id_stored(self):
        a = _make_adapter(run_id="my-run")
        assert a._run_id == "my-run"

    def test_run_id_defaults_to_none(self, adapter_no_run):
        assert adapter_no_run._run_id is None

    def test_buffer_starts_empty(self, adapter):
        assert adapter.drain() == []


# ---------------------------------------------------------------------------
# Conversation events
# ---------------------------------------------------------------------------


class TestFromConversationEvents:
    def test_conversation_started_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_started", "initiator": "user_proxy", "recipient": "assistant"}
        )
        assert e.event_type == "conversation_started"

    def test_conversation_started_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_started", "initiator": "u", "recipient": "a"}
        )
        assert e.severity == "INFO"

    def test_conversation_started_body_contains_initiator_and_recipient(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_started", "initiator": "UserProxy", "recipient": "Assistant"}
        )
        assert "UserProxy" in e.body
        assert "Assistant" in e.body

    def test_conversation_started_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_started", "initiator": "u", "recipient": "a"}
        )
        assert e.attributes["initiator"] == "u"
        assert e.attributes["recipient"] == "a"

    def test_conversation_started_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_started", "initiator": "u", "recipient": "a"}
        )
        assert e.event_id.startswith("autogen-conv-")

    def test_conversation_started_source_system(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_started", "initiator": "u", "recipient": "a"}
        )
        assert e.source_system == "autogen"

    def test_conversation_started_trace_id(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_started", "initiator": "u", "recipient": "a"}
        )
        assert e.trace_id == "run-001"

    def test_conversation_started_defaults_unknown(self, adapter):
        e = adapter.to_sentinel_event({"type": "conversation_started"})
        assert e.attributes["initiator"] == "unknown"
        assert e.attributes["recipient"] == "unknown"

    def test_conversation_completed_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_completed", "initiator": "u", "message_count": 5}
        )
        assert e.event_type == "conversation_completed"

    def test_conversation_completed_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_completed", "initiator": "u", "message_count": 3}
        )
        assert e.severity == "INFO"

    def test_conversation_completed_body_contains_count(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_completed", "initiator": "u", "message_count": 7}
        )
        assert "7" in e.body

    def test_conversation_completed_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_completed", "initiator": "u", "message_count": 4}
        )
        assert e.attributes["initiator"] == "u"
        assert e.attributes["message_count"] == 4

    def test_conversation_completed_content_included_when_present(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "conversation_completed",
                "initiator": "u",
                "message_count": 1,
                "content": "done",
            }
        )
        assert e.attributes["content"] == "done"

    def test_conversation_completed_content_absent_when_empty(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_completed", "initiator": "u", "message_count": 1}
        )
        assert "content" not in e.attributes

    def test_conversation_error_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_error", "initiator": "u", "error": "timeout"}
        )
        assert e.event_type == "conversation_error"

    def test_conversation_error_severity_error(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_error", "initiator": "u", "error": "timeout"}
        )
        assert e.severity == "ERROR"

    def test_conversation_error_body_contains_error(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_error", "initiator": "u", "error": "context limit exceeded"}
        )
        assert "context limit exceeded" in e.body

    def test_conversation_error_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "conversation_error", "initiator": "UserProxy", "error": "timeout"}
        )
        assert e.attributes["initiator"] == "UserProxy"
        assert e.attributes["error"] == "timeout"


# ---------------------------------------------------------------------------
# Message events
# ---------------------------------------------------------------------------


class TestFromMessageEvents:
    def test_message_sent_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "message_sent", "sender": "assistant", "content": "Hello"}
        )
        assert e.event_type == "message_sent"

    def test_message_sent_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "message_sent", "sender": "assistant", "content": "Hi"}
        )
        assert e.severity == "INFO"

    def test_message_sent_body_contains_sender_and_content(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "message_sent", "sender": "ResearchAgent", "content": "Found results"}
        )
        assert "ResearchAgent" in e.body
        assert "Found results" in e.body

    def test_message_sent_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "message_sent", "sender": "agent", "content": "text"}
        )
        assert e.attributes["sender"] == "agent"
        assert e.attributes["content"] == "text"

    def test_message_sent_role_included_when_present(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "message_sent", "sender": "a", "content": "c", "role": "assistant"}
        )
        assert e.attributes["role"] == "assistant"

    def test_message_sent_role_absent_when_empty(self, adapter):
        e = adapter.to_sentinel_event({"type": "message_sent", "sender": "a", "content": "c"})
        assert "role" not in e.attributes

    def test_message_sent_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event({"type": "message_sent", "sender": "a", "content": "c"})
        assert e.event_id.startswith("autogen-msg-")

    def test_message_sent_trace_id(self, adapter):
        e = adapter.to_sentinel_event({"type": "message_sent", "sender": "a", "content": "c"})
        assert e.trace_id == "run-001"

    def test_message_filtered_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "message_filtered", "sender": "agent", "reason": "profanity"}
        )
        assert e.event_type == "message_filtered"

    def test_message_filtered_severity_warn(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "message_filtered", "sender": "agent", "reason": "policy"}
        )
        assert e.severity == "WARN"

    def test_message_filtered_body_contains_sender_and_reason(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "message_filtered", "sender": "EvilAgent", "reason": "harmful content"}
        )
        assert "EvilAgent" in e.body
        assert "harmful content" in e.body

    def test_message_filtered_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "message_filtered", "sender": "a", "reason": "r", "content": "blocked text"}
        )
        assert e.attributes["sender"] == "a"
        assert e.attributes["reason"] == "r"
        assert e.attributes["content"] == "blocked text"

    def test_message_filtered_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event({"type": "message_filtered", "sender": "a", "reason": "r"})
        assert e.event_id.startswith("autogen-msg-")


# ---------------------------------------------------------------------------
# Function call events
# ---------------------------------------------------------------------------


class TestFromFunctionCallEvents:
    def test_function_call_started_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_started", "sender": "assistant", "function_name": "search"}
        )
        assert e.event_type == "function_call_started"

    def test_function_call_started_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_started", "sender": "a", "function_name": "fn"}
        )
        assert e.severity == "INFO"

    def test_function_call_started_body_contains_sender_and_function(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "function_call_started",
                "sender": "ResearchAgent",
                "function_name": "web_search",
            }
        )
        assert "ResearchAgent" in e.body
        assert "web_search" in e.body

    def test_function_call_started_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "function_call_started",
                "sender": "assistant",
                "function_name": "calculator",
                "arguments": '{"expr": "2+2"}',
            }
        )
        assert e.attributes["sender"] == "assistant"
        assert e.attributes["function_name"] == "calculator"
        assert e.attributes["arguments"] == '{"expr": "2+2"}'

    def test_function_call_started_tool_call_id_included_when_present(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "function_call_started",
                "sender": "a",
                "function_name": "fn",
                "tool_call_id": "call-abc",
            }
        )
        assert e.attributes["tool_call_id"] == "call-abc"

    def test_function_call_started_tool_call_id_absent_when_empty(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_started", "sender": "a", "function_name": "fn"}
        )
        assert "tool_call_id" not in e.attributes

    def test_function_call_started_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_started", "sender": "a", "function_name": "fn"}
        )
        assert e.event_id.startswith("autogen-func-")

    def test_function_call_completed_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_completed", "function_name": "search", "result": "found"}
        )
        assert e.event_type == "function_call_completed"

    def test_function_call_completed_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_completed", "function_name": "fn", "result": "ok"}
        )
        assert e.severity == "INFO"

    def test_function_call_completed_body_contains_function_name(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_completed", "function_name": "web_search", "result": "results"}
        )
        assert "web_search" in e.body

    def test_function_call_completed_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_completed", "function_name": "fn", "result": "42"}
        )
        assert e.attributes["function_name"] == "fn"
        assert e.attributes["result"] == "42"

    def test_function_call_completed_tool_call_id_included(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "function_call_completed",
                "function_name": "fn",
                "result": "r",
                "tool_call_id": "call-xyz",
            }
        )
        assert e.attributes["tool_call_id"] == "call-xyz"

    def test_function_call_error_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_error", "function_name": "fn", "error": "503"}
        )
        assert e.event_type == "function_call_error"

    def test_function_call_error_severity_error(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_error", "function_name": "fn", "error": "503"}
        )
        assert e.severity == "ERROR"

    def test_function_call_error_body_contains_function_and_error(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_error", "function_name": "web_search", "error": "rate limited"}
        )
        assert "web_search" in e.body
        assert "rate limited" in e.body

    def test_function_call_error_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "function_call_error",
                "sender": "ResearchAgent",
                "function_name": "web_search",
                "error": "429 Too Many Requests",
            }
        )
        assert e.attributes["sender"] == "ResearchAgent"
        assert e.attributes["function_name"] == "web_search"
        assert e.attributes["error"] == "429 Too Many Requests"

    def test_function_call_error_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "function_call_error", "function_name": "fn", "error": "oops"}
        )
        assert e.event_id.startswith("autogen-func-")

    def test_missing_function_name_defaults_to_unknown(self, adapter):
        e = adapter.to_sentinel_event({"type": "function_call_started", "sender": "a"})
        assert e.attributes["function_name"] == "unknown"


# ---------------------------------------------------------------------------
# Agent reply events
# ---------------------------------------------------------------------------


class TestFromAgentReplyEvents:
    def test_agent_reply_started_event_type(self, adapter):
        e = adapter.to_sentinel_event({"type": "agent_reply_started", "agent": "AssistantAgent"})
        assert e.event_type == "agent_reply_started"

    def test_agent_reply_started_severity_info(self, adapter):
        e = adapter.to_sentinel_event({"type": "agent_reply_started", "agent": "a"})
        assert e.severity == "INFO"

    def test_agent_reply_started_body_contains_agent(self, adapter):
        e = adapter.to_sentinel_event({"type": "agent_reply_started", "agent": "PlannerAgent"})
        assert "PlannerAgent" in e.body

    def test_agent_reply_started_attributes(self, adapter):
        e = adapter.to_sentinel_event({"type": "agent_reply_started", "agent": "Coder"})
        assert e.attributes["agent"] == "Coder"

    def test_agent_reply_started_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event({"type": "agent_reply_started", "agent": "a"})
        assert e.event_id.startswith("autogen-agent-")

    def test_agent_reply_completed_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_reply_completed", "agent": "a", "content": "done"}
        )
        assert e.event_type == "agent_reply_completed"

    def test_agent_reply_completed_severity_info(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_reply_completed", "agent": "a", "content": "done"}
        )
        assert e.severity == "INFO"

    def test_agent_reply_completed_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_reply_completed", "agent": "Writer", "content": "article text"}
        )
        assert e.attributes["agent"] == "Writer"
        assert e.attributes["content"] == "article text"

    def test_agent_reply_error_event_type(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_reply_error", "agent": "a", "error": "LLM timeout"}
        )
        assert e.event_type == "agent_reply_error"

    def test_agent_reply_error_severity_error(self, adapter):
        e = adapter.to_sentinel_event({"type": "agent_reply_error", "agent": "a", "error": "fail"})
        assert e.severity == "ERROR"

    def test_agent_reply_error_body_contains_agent_and_error(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_reply_error", "agent": "PlannerAgent", "error": "LLM timeout"}
        )
        assert "PlannerAgent" in e.body
        assert "LLM timeout" in e.body

    def test_agent_reply_error_attributes(self, adapter):
        e = adapter.to_sentinel_event(
            {"type": "agent_reply_error", "agent": "Coder", "error": "SyntaxError"}
        )
        assert e.attributes["agent"] == "Coder"
        assert e.attributes["error"] == "SyntaxError"

    def test_agent_reply_started_defaults_unknown(self, adapter):
        e = adapter.to_sentinel_event({"type": "agent_reply_started"})
        assert e.attributes["agent"] == "unknown"

    def test_trace_id_propagated_to_agent_events(self, adapter):
        e = adapter.to_sentinel_event({"type": "agent_reply_started", "agent": "a"})
        assert e.trace_id == "run-001"

    def test_trace_id_none_when_no_run_id(self, adapter_no_run):
        e = adapter_no_run.to_sentinel_event({"type": "agent_reply_started", "agent": "a"})
        assert e.trace_id is None


# ---------------------------------------------------------------------------
# Unknown events
# ---------------------------------------------------------------------------


class TestFromUnknown:
    def test_unknown_type_gives_unknown_autogen_event(self, adapter):
        e = adapter.to_sentinel_event({"type": "some_future_event"})
        assert e.event_type == "unknown_autogen_event"

    def test_unknown_severity_info(self, adapter):
        e = adapter.to_sentinel_event({"type": "whatever"})
        assert e.severity == "INFO"

    def test_unknown_event_id_prefixed(self, adapter):
        e = adapter.to_sentinel_event({"type": "x"})
        assert e.event_id.startswith("autogen-unknown-")

    def test_unknown_attributes_original_type(self, adapter):
        e = adapter.to_sentinel_event({"type": "mystery_event"})
        assert e.attributes["original_type"] == "mystery_event"

    def test_empty_dict_does_not_raise(self, adapter):
        e = adapter.to_sentinel_event({})
        assert e.event_type == "unknown_autogen_event"

    def test_missing_type_key_goes_to_unknown(self, adapter):
        e = adapter.to_sentinel_event({"sender": "a"})
        assert e.event_type == "unknown_autogen_event"

    def test_trace_id_on_unknown_is_run_id(self, adapter):
        e = adapter.to_sentinel_event({"type": "x"})
        assert e.trace_id == "run-001"

    def test_source_system_on_unknown_is_autogen(self, adapter):
        e = adapter.to_sentinel_event({"type": "x"})
        assert e.source_system == "autogen"


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    def test_iso_z_suffix_parsed(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "conversation_started",
                "initiator": "u",
                "recipient": "a",
                "timestamp": "2026-06-01T12:00:00Z",
            }
        )
        assert e.timestamp == datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    def test_iso_offset_parsed(self, adapter):
        e = adapter.to_sentinel_event(
            {
                "type": "conversation_started",
                "initiator": "u",
                "recipient": "a",
                "timestamp": "2026-06-01T12:00:00+00:00",
            }
        )
        assert e.timestamp.year == 2026

    def test_missing_timestamp_uses_now(self, adapter):
        before = datetime.now(UTC)
        e = adapter.to_sentinel_event(
            {"type": "conversation_started", "initiator": "u", "recipient": "a"}
        )
        after = datetime.now(UTC)
        assert before <= e.timestamp <= after

    def test_invalid_timestamp_falls_back_to_now(self, adapter):
        before = datetime.now(UTC)
        e = adapter.to_sentinel_event(
            {
                "type": "conversation_started",
                "initiator": "u",
                "recipient": "a",
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
                        "initiator": "u",
                        "recipient": "a",
                        "sender": "a",
                        "content": "x",
                        "function_name": "fn",
                        "agent": "a",
                    }
                )
            )

    def test_drain_returns_buffered_events(self, adapter):
        self._push(adapter, "conversation_started", "message_sent")
        events = adapter.drain()
        assert len(events) == 2

    def test_drain_clears_buffer(self, adapter):
        self._push(adapter, "message_sent")
        adapter.drain()
        assert adapter.drain() == []

    def test_drain_empty_buffer_returns_empty_list(self, adapter):
        assert adapter.drain() == []

    def test_drain_returns_correct_event_types(self, adapter):
        self._push(adapter, "conversation_started", "function_call_error")
        events = adapter.drain()
        assert events[0].event_type == "conversation_started"
        assert events[1].event_type == "function_call_error"

    def test_flush_into_ingests_events(self, adapter):
        self._push(adapter, "function_call_error")
        sentinel = Sentinel()
        adapter.flush_into(sentinel)
        assert len(sentinel._events) == 1

    def test_flush_into_clears_buffer(self, adapter):
        self._push(adapter, "message_sent")
        sentinel = Sentinel()
        adapter.flush_into(sentinel)
        assert adapter.drain() == []

    def test_flush_into_empty_buffer_is_noop(self, adapter):
        sentinel = Sentinel()
        adapter.flush_into(sentinel)
        assert sentinel._events == []

    def test_multiple_flush_calls_replace_sentinel_buffer(self, adapter):
        sentinel = Sentinel()
        self._push(adapter, "conversation_started")
        adapter.flush_into(sentinel)
        self._push(adapter, "conversation_completed")
        adapter.flush_into(sentinel)
        # Sentinel.ingest replaces buffer each time
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
                    adapter.to_sentinel_event(
                        {"type": "message_sent", "sender": f"agent-{i}", "content": f"msg-{i}"}
                    )
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
                    adapter.to_sentinel_event(
                        {"type": "message_sent", "sender": f"a-{i}", "content": f"m-{i}"}
                    )
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
# iter_messages — AutoGen 0.2.x native format
# ---------------------------------------------------------------------------


class TestIterMessagesV2:
    def test_user_message_becomes_message_sent(self, adapter):
        msgs = [{"role": "user", "content": "Hello", "name": "user_proxy"}]
        events = list(adapter.iter_messages(msgs))
        assert len(events) == 1
        assert events[0].event_type == "message_sent"

    def test_assistant_message_becomes_message_sent(self, adapter):
        msgs = [{"role": "assistant", "content": "Hi there", "name": "assistant"}]
        events = list(adapter.iter_messages(msgs))
        assert events[0].event_type == "message_sent"

    def test_message_sender_taken_from_name(self, adapter):
        msgs = [{"role": "user", "content": "query", "name": "MyAgent"}]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.attributes["sender"] == "MyAgent"

    def test_message_role_included_in_attributes(self, adapter):
        msgs = [{"role": "assistant", "content": "reply", "name": "assistant"}]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.attributes["role"] == "assistant"

    def test_function_call_request_becomes_function_call_started(self, adapter):
        msgs = [
            {
                "role": "assistant",
                "name": "assistant",
                "content": None,
                "function_call": {"name": "web_search", "arguments": '{"query": "test"}'},
            }
        ]
        events = list(adapter.iter_messages(msgs))
        assert events[0].event_type == "function_call_started"
        assert events[0].attributes["function_name"] == "web_search"

    def test_function_call_arguments_preserved(self, adapter):
        msgs = [
            {
                "role": "assistant",
                "name": "assistant",
                "function_call": {"name": "add", "arguments": '{"a": 1, "b": 2}'},
            }
        ]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.attributes["arguments"] == '{"a": 1, "b": 2}'

    def test_function_result_becomes_function_call_completed(self, adapter):
        msgs = [{"role": "function", "name": "web_search", "content": "search results here"}]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.event_type == "function_call_completed"
        assert e.attributes["function_name"] == "web_search"

    def test_function_error_result_becomes_function_call_error(self, adapter):
        msgs = [{"role": "function", "name": "web_search", "content": "Error: connection failed"}]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.event_type == "function_call_error"

    def test_tool_calls_list_becomes_function_call_started(self, adapter):
        msgs = [
            {
                "role": "assistant",
                "name": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "calculator", "arguments": '{"expr":"2+2"}'},
                    }
                ],
            }
        ]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.event_type == "function_call_started"
        assert e.attributes["function_name"] == "calculator"
        assert e.attributes["tool_call_id"] == "call-1"

    def test_tool_role_becomes_function_call_completed(self, adapter):
        msgs = [{"role": "tool", "name": "calculator", "tool_call_id": "call-1", "content": "4"}]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.event_type == "function_call_completed"
        assert e.attributes["tool_call_id"] == "call-1"

    def test_full_v2_conversation_yields_all_events(self, adapter):
        msgs = [
            {"role": "user", "content": "Compute 2+2", "name": "user_proxy"},
            {
                "role": "assistant",
                "name": "assistant",
                "function_call": {"name": "add", "arguments": '{"a":2,"b":2}'},
            },
            {"role": "function", "name": "add", "content": "4"},
            {"role": "assistant", "content": "The answer is 4.", "name": "assistant"},
        ]
        events = list(adapter.iter_messages(msgs))
        assert len(events) == 4
        types = [e.event_type for e in events]
        assert types == [
            "message_sent",
            "function_call_started",
            "function_call_completed",
            "message_sent",
        ]

    def test_empty_history_yields_nothing(self, adapter):
        assert list(adapter.iter_messages([])) == []


# ---------------------------------------------------------------------------
# iter_messages — AutoGen 0.4.x native format
# ---------------------------------------------------------------------------


class TestIterMessagesV4:
    def test_text_message_becomes_message_sent(self, adapter):
        msgs = [{"type": "TextMessage", "source": "assistant", "content": "Hello"}]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.event_type == "message_sent"
        assert e.attributes["sender"] == "assistant"

    def test_tool_call_request_becomes_function_call_started(self, adapter):
        msgs = [
            {
                "type": "ToolCallRequestEvent",
                "source": "assistant",
                "content": [{"id": "c1", "name": "search", "arguments": '{"q":"test"}'}],
            }
        ]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.event_type == "function_call_started"
        assert e.attributes["function_name"] == "search"
        assert e.attributes["tool_call_id"] == "c1"

    def test_tool_execution_success_becomes_function_call_completed(self, adapter):
        msgs = [
            {
                "type": "ToolCallExecutionEvent",
                "source": "tool",
                "content": [{"call_id": "c1", "content": "result data"}],
            }
        ]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.event_type == "function_call_completed"

    def test_tool_execution_error_becomes_function_call_error(self, adapter):
        msgs = [
            {
                "type": "ToolCallExecutionEvent",
                "source": "tool",
                "content": [{"call_id": "c1", "content": "Error: service unavailable"}],
            }
        ]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.event_type == "function_call_error"

    def test_stop_message_becomes_conversation_completed(self, adapter):
        msgs = [
            {
                "type": "StopMessage",
                "source": "termination_handler",
                "content": "Max rounds reached",
            }
        ]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.event_type == "conversation_completed"

    def test_handoff_message_becomes_message_sent(self, adapter):
        msgs = [
            {"type": "HandoffMessage", "source": "agent_a", "content": "Passing to specialist"}
        ]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.event_type == "message_sent"
        assert "[handoff]" in e.attributes["content"]

    def test_unknown_v4_type_becomes_unknown_autogen_event(self, adapter):
        msgs = [{"type": "FutureMessageType", "source": "agent", "content": "x"}]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.event_type == "unknown_autogen_event"

    def test_v4_source_becomes_sender(self, adapter):
        msgs = [{"type": "TextMessage", "source": "PlannerAgent", "content": "plan ready"}]
        e = list(adapter.iter_messages(msgs))[0]
        assert e.attributes["sender"] == "PlannerAgent"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_satisfies_sentinel_adapter_protocol(self, adapter):
        from agentcop import SentinelAdapter

        assert isinstance(adapter, SentinelAdapter)

    def test_source_system_attribute(self, adapter):
        assert adapter.source_system == "autogen"

    def test_to_sentinel_event_callable(self, adapter):
        assert callable(adapter.to_sentinel_event)


# ---------------------------------------------------------------------------
# Integration with Sentinel
# ---------------------------------------------------------------------------


class TestSentinelIntegration:
    def test_all_error_events_collected(self, adapter):
        sentinel = Sentinel()

        for raw in [
            {"type": "conversation_error", "initiator": "u", "error": "timeout"},
            {
                "type": "function_call_error",
                "function_name": "search",
                "error": "403",
                "sender": "a",
            },
            {"type": "agent_reply_error", "agent": "Planner", "error": "context limit"},
        ]:
            adapter._buffer_event(adapter.to_sentinel_event(raw))

        adapter.flush_into(sentinel)
        assert len(sentinel._events) == 3
        assert all(e.severity == "ERROR" for e in sentinel._events)

    def test_custom_detector_fires_on_function_call_error(self, adapter):
        from agentcop import ViolationRecord

        def detect_func_error(event):
            if event.event_type == "function_call_error":
                return ViolationRecord(
                    violation_type="function_call_failed",
                    severity="ERROR",
                    source_event_id=event.event_id,
                    trace_id=event.trace_id,
                    detail={
                        "function": event.attributes.get("function_name"),
                        "error": event.attributes.get("error"),
                    },
                )

        adapter._buffer_event(
            adapter.to_sentinel_event(
                {
                    "type": "function_call_error",
                    "sender": "assistant",
                    "function_name": "web_search",
                    "error": "rate limit exceeded",
                }
            )
        )

        sentinel = Sentinel(detectors=[detect_func_error])
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()

        assert len(violations) == 1
        assert violations[0].violation_type == "function_call_failed"
        assert violations[0].detail["function"] == "web_search"
        assert "rate limit" in violations[0].detail["error"]

    def test_custom_detector_fires_on_message_filtered(self, adapter):
        from agentcop import ViolationRecord

        def detect_filtered(event):
            if event.event_type == "message_filtered":
                return ViolationRecord(
                    violation_type="message_policy_violation",
                    severity="WARN",
                    source_event_id=event.event_id,
                    trace_id=event.trace_id,
                    detail={
                        "sender": event.attributes.get("sender"),
                        "reason": event.attributes.get("reason"),
                    },
                )

        adapter._buffer_event(
            adapter.to_sentinel_event(
                {
                    "type": "message_filtered",
                    "sender": "EvilAgent",
                    "reason": "harmful content",
                    "content": "do bad things",
                }
            )
        )

        sentinel = Sentinel(detectors=[detect_filtered])
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()

        assert len(violations) == 1
        assert violations[0].detail["sender"] == "EvilAgent"
        assert violations[0].detail["reason"] == "harmful content"

    def test_iter_messages_end_to_end(self, adapter):
        from agentcop import ViolationRecord

        def detect_func_error(event):
            if event.event_type == "function_call_error":
                return ViolationRecord(
                    violation_type="function_call_failed",
                    severity="ERROR",
                    source_event_id=event.event_id,
                    trace_id=event.trace_id,
                    detail={"function": event.attributes.get("function_name")},
                )

        history = [
            {"role": "user", "content": "search for AI news", "name": "user_proxy"},
            {
                "role": "assistant",
                "name": "assistant",
                "function_call": {"name": "web_search", "arguments": "{}"},
            },
            {
                "role": "function",
                "name": "web_search",
                "content": "Error: 429 rate limit exceeded",
            },
        ]

        sentinel = Sentinel(detectors=[detect_func_error])
        sentinel.ingest(adapter.iter_messages(history))
        violations = sentinel.detect_violations()

        assert len(violations) == 1
        assert violations[0].violation_type == "function_call_failed"
        assert violations[0].detail["function"] == "web_search"

    def test_trace_id_consistent_across_run(self):
        a = _make_adapter(run_id="session-abc")
        events_raw = [
            {"type": "conversation_started", "initiator": "u", "recipient": "a"},
            {"type": "message_sent", "sender": "u", "content": "hello"},
            {"type": "function_call_started", "sender": "a", "function_name": "fn"},
            {"type": "conversation_completed", "initiator": "u", "message_count": 3},
        ]
        events = [a.to_sentinel_event(r) for r in events_raw]
        assert all(e.trace_id == "session-abc" for e in events)

    def test_no_violations_for_info_events_with_default_detectors(self, adapter):
        sentinel = Sentinel()  # default detectors only — none match autogen types
        for raw in [
            {"type": "conversation_started", "initiator": "u", "recipient": "a"},
            {"type": "message_sent", "sender": "a", "content": "hi"},
            {"type": "function_call_started", "sender": "a", "function_name": "fn"},
            {"type": "agent_reply_completed", "agent": "a", "content": "done"},
        ]:
            adapter._buffer_event(adapter.to_sentinel_event(raw))
        adapter.flush_into(sentinel)
        assert sentinel.detect_violations() == []


# ---------------------------------------------------------------------------
# Runtime security tests
# ---------------------------------------------------------------------------


def _make_autogen_runtime(gate=None, permissions=None, sandbox=None, approvals=None):
    with patch("agentcop.adapters.autogen._require_autogen"):
        from agentcop.adapters.autogen import AutoGenSentinelAdapter

        return AutoGenSentinelAdapter(
            run_id="rt-run",
            gate=gate,
            permissions=permissions,
            sandbox=sandbox,
            approvals=approvals,
        )


_FUNC_CALL_RAW = {
    "type": "function_call_started",
    "sender": "AssistantAgent",
    "function_name": "search_web",
    "arguments": '{"query": "test"}',
}


class TestRuntimeSecurityAutoGen:
    def test_init_stores_none_by_default(self):
        a = _make_autogen_runtime()
        assert a._gate is None
        assert a._permissions is None
        assert a._sandbox is None
        assert a._approvals is None

    def test_init_stores_runtime_params(self):
        gate = MagicMock()
        perms = MagicMock()
        a = _make_autogen_runtime(gate=gate, permissions=perms)
        assert a._gate is gate
        assert a._permissions is perms

    def test_gate_denial_raises_on_function_call(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=False, reason="blocked", risk_score=90)
        a = _make_autogen_runtime(gate=gate)
        with pytest.raises(PermissionError, match="blocked"):
            a.to_sentinel_event(_FUNC_CALL_RAW)

    def test_gate_denial_fires_gate_denied_event(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=False, reason="blocked", risk_score=90)
        a = _make_autogen_runtime(gate=gate)
        with pytest.raises(PermissionError):
            a.to_sentinel_event(_FUNC_CALL_RAW)
        events = a.drain()
        assert any(e.event_type == "gate_denied" for e in events)

    def test_permission_violation_on_function_call(self):
        perms = MagicMock()
        perms.verify.return_value = MagicMock(granted=False, reason="restricted")
        a = _make_autogen_runtime(permissions=perms)
        with pytest.raises(PermissionError, match="restricted"):
            a.to_sentinel_event(_FUNC_CALL_RAW)
        events = a.drain()
        assert any(e.event_type == "permission_violation" for e in events)

    def test_gate_allow_returns_normal_event(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=True, reason="ok", risk_score=5)
        a = _make_autogen_runtime(gate=gate)
        event = a.to_sentinel_event(_FUNC_CALL_RAW)
        assert event.event_type == "function_call_started"

    def test_no_gate_no_interception(self):
        a = _make_autogen_runtime()
        event = a.to_sentinel_event(_FUNC_CALL_RAW)
        assert event.event_type == "function_call_started"

    def test_sandbox_stored_on_adapter(self):
        sandbox = MagicMock()
        a = _make_autogen_runtime(sandbox=sandbox)
        assert a._sandbox is sandbox

    def test_approval_boundary_wait_on_high_risk(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=True, reason="ok", risk_score=80)
        approvals = MagicMock()
        approvals.requires_approval_above = 70
        req = MagicMock(request_id="req-1")
        approvals.submit.return_value = req
        approvals.wait_for_decision.return_value = MagicMock(denied=False, reason="approved")
        a = _make_autogen_runtime(gate=gate, approvals=approvals)
        a.to_sentinel_event(_FUNC_CALL_RAW)
        approvals.submit.assert_called_once()
        approvals.wait_for_decision.assert_called_once()
