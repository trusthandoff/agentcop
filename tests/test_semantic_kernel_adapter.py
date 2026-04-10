"""
Tests for src/agentcop/adapters/semantic_kernel.py

All tests mock the semantic_kernel import guard so semantic-kernel does not
need to be installed in the test environment. Setup filter tests run the
registered async callables directly via asyncio.run().
"""

from __future__ import annotations

import asyncio
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
    """Return a SemanticKernelSentinelAdapter with the import guard bypassed."""
    with patch("agentcop.adapters.semantic_kernel._require_semantic_kernel"):
        from agentcop.adapters.semantic_kernel import SemanticKernelSentinelAdapter

        return SemanticKernelSentinelAdapter(run_id=run_id)


def _mock_sk_modules():
    """Return a sys.modules patch dict that satisfies setup()'s SK import."""
    mock_sk = MagicMock()
    mock_filter_types = MagicMock()
    # FilterTypes enum values used in setup()
    mock_filter_types.FilterTypes.FUNCTION_INVOCATION = "function_invocation"
    mock_filter_types.FilterTypes.PROMPT_RENDERING = "prompt_rendering"
    mock_filter_types.FilterTypes.AUTO_FUNCTION_INVOCATION = "auto_function_invocation"
    return {
        "semantic_kernel": mock_sk,
        "semantic_kernel.filters": MagicMock(),
        "semantic_kernel.filters.filter_types": mock_filter_types,
    }


def _make_function_mock(plugin_name="MyPlugin", function_name="MyFunction", is_prompt=False):
    """Build a mock KernelFunction with the attributes the filter reads."""
    fn = MagicMock()
    fn.plugin_name = plugin_name
    fn.name = function_name
    fn.is_prompt = is_prompt
    return fn


def _make_function_invocation_context(
    plugin_name="MyPlugin",
    function_name="MyFunction",
    is_prompt=False,
    is_streaming=False,
    arguments=None,
    result=None,
):
    ctx = MagicMock()
    ctx.function = _make_function_mock(plugin_name, function_name, is_prompt)
    ctx.is_streaming = is_streaming
    ctx.arguments = arguments or {}
    ctx.result = result
    return ctx


def _make_prompt_render_context(
    plugin_name="MyPlugin",
    function_name="MyPromptFunc",
    is_streaming=False,
    rendered_prompt=None,
):
    ctx = MagicMock()
    ctx.function = _make_function_mock(plugin_name, function_name, is_prompt=True)
    ctx.is_streaming = is_streaming
    ctx.rendered_prompt = rendered_prompt
    return ctx


def _make_auto_function_context(
    plugin_name="SearchPlugin",
    function_name="Search",
    request_sequence_index=0,
    function_sequence_index=0,
    function_count=1,
    is_streaming=False,
    function_result=None,
    terminate=False,
):
    ctx = MagicMock()
    ctx.function = _make_function_mock(plugin_name, function_name)
    ctx.request_sequence_index = request_sequence_index
    ctx.function_sequence_index = function_sequence_index
    ctx.function_count = function_count
    ctx.is_streaming = is_streaming
    ctx.function_result = function_result
    ctx.terminate = terminate
    return ctx


def _get_registered_filters(mock_kernel):
    """
    Extract the three filter callables registered by setup(), keyed by the
    filter type string passed as the first arg to kernel.add_filter().
    """
    filters = {}
    for call in mock_kernel.add_filter.call_args_list:
        filter_type, callable_ = call.args
        filters[filter_type] = callable_
    return filters


# ---------------------------------------------------------------------------
# TestRequireSemanticKernel
# ---------------------------------------------------------------------------


class TestRequireSemanticKernel:
    def test_raises_when_sk_missing(self):
        with patch.dict("sys.modules", {"semantic_kernel": None}):
            if "agentcop.adapters.semantic_kernel" in sys.modules:
                del sys.modules["agentcop.adapters.semantic_kernel"]
            from agentcop.adapters.semantic_kernel import _require_semantic_kernel

            with pytest.raises(ImportError, match="semantic-kernel"):
                _require_semantic_kernel()

    def test_does_not_raise_when_sk_present(self):
        mock_sk = MagicMock()
        with patch.dict("sys.modules", {"semantic_kernel": mock_sk}):
            from agentcop.adapters.semantic_kernel import _require_semantic_kernel

            _require_semantic_kernel()  # no exception

    def test_constructor_calls_require(self):
        called = []

        def fake_require():
            called.append(True)

        with patch("agentcop.adapters.semantic_kernel._require_semantic_kernel", fake_require):
            from agentcop.adapters.semantic_kernel import SemanticKernelSentinelAdapter

            SemanticKernelSentinelAdapter()
        assert called == [True]


# ---------------------------------------------------------------------------
# TestAdapterInit
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_source_system(self):
        assert _make_adapter().source_system == "semantic_kernel"

    def test_run_id_none_by_default(self):
        assert _make_adapter()._run_id is None

    def test_run_id_stored(self):
        assert _make_adapter(run_id="sess-42")._run_id == "sess-42"

    def test_buffer_starts_empty(self):
        assert _make_adapter().drain() == []


# ---------------------------------------------------------------------------
# TestFromFunctionInvoking
# ---------------------------------------------------------------------------


class TestFromFunctionInvoking:
    def setup_method(self):
        self.adapter = _make_adapter(run_id="trace-fi")

    def _ev(self, **kwargs):
        base = {"type": "function_invoking", "plugin_name": "P", "function_name": "F"}
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_event_type(self):
        assert self._ev().event_type == "function_invoking"

    def test_severity_info(self):
        assert self._ev().severity == "INFO"

    def test_source_system(self):
        assert self._ev().source_system == "semantic_kernel"

    def test_trace_id(self):
        assert self._ev().trace_id == "trace-fi"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("sk-function-")

    def test_plugin_name_in_attributes(self):
        ev = self._ev(plugin_name="MathPlugin")
        assert ev.attributes["plugin_name"] == "MathPlugin"

    def test_function_name_in_attributes(self):
        ev = self._ev(function_name="Add")
        assert ev.attributes["function_name"] == "Add"

    def test_body_contains_plugin_and_function(self):
        ev = self._ev(plugin_name="X", function_name="Y")
        assert "X" in ev.body and "Y" in ev.body

    def test_is_prompt_default_false(self):
        assert self._ev().attributes["is_prompt"] is False

    def test_is_prompt_true(self):
        assert self._ev(is_prompt=True).attributes["is_prompt"] is True

    def test_is_streaming_default_false(self):
        assert self._ev().attributes["is_streaming"] is False

    def test_arguments_empty_by_default(self):
        assert self._ev().attributes["arguments"] == {}

    def test_arguments_preserved(self):
        ev = self._ev(arguments={"input": "hello"})
        assert ev.attributes["arguments"]["input"] == "hello"

    def test_unique_event_ids(self):
        assert self._ev().event_id != self._ev().event_id


# ---------------------------------------------------------------------------
# TestFromFunctionInvoked
# ---------------------------------------------------------------------------


class TestFromFunctionInvoked:
    def setup_method(self):
        self.adapter = _make_adapter(run_id="trace-invoked")

    def _ev(self, **kwargs):
        base = {"type": "function_invoked", "plugin_name": "P", "function_name": "F"}
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_event_type(self):
        assert self._ev().event_type == "function_invoked"

    def test_severity_info(self):
        assert self._ev().severity == "INFO"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("sk-function-")

    def test_result_captured(self):
        ev = self._ev(result="42")
        assert ev.attributes["result"] == "42"

    def test_result_empty_by_default(self):
        assert self._ev().attributes["result"] == ""

    def test_metadata_captured(self):
        ev = self._ev(metadata={"model": "gpt-4o", "usage": "100"})
        assert ev.attributes["metadata"]["model"] == "gpt-4o"

    def test_metadata_empty_by_default(self):
        assert self._ev().attributes["metadata"] == {}

    def test_is_streaming_preserved(self):
        assert self._ev(is_streaming=True).attributes["is_streaming"] is True

    def test_body_contains_plugin_and_function(self):
        ev = self._ev(plugin_name="WeatherPlugin", function_name="GetForecast")
        assert "WeatherPlugin" in ev.body and "GetForecast" in ev.body

    def test_trace_id_propagated(self):
        assert self._ev().trace_id == "trace-invoked"


# ---------------------------------------------------------------------------
# TestFromFunctionError
# ---------------------------------------------------------------------------


class TestFromFunctionError:
    def setup_method(self):
        self.adapter = _make_adapter(run_id="trace-err")

    def _ev(self, **kwargs):
        base = {"type": "function_error", "plugin_name": "P", "function_name": "F"}
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_event_type(self):
        assert self._ev().event_type == "function_error"

    def test_severity_error(self):
        assert self._ev().severity == "ERROR"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("sk-function-")

    def test_error_captured(self):
        ev = self._ev(error="connection refused")
        assert ev.attributes["error"] == "connection refused"

    def test_error_empty_by_default(self):
        assert self._ev().attributes["error"] == ""

    def test_body_contains_error(self):
        ev = self._ev(error="timeout")
        assert "timeout" in ev.body

    def test_is_prompt_captured(self):
        ev = self._ev(is_prompt=True)
        assert ev.attributes["is_prompt"] is True

    def test_trace_id_propagated(self):
        assert self._ev().trace_id == "trace-err"


# ---------------------------------------------------------------------------
# TestFromPromptRendering
# ---------------------------------------------------------------------------


class TestFromPromptRendering:
    def setup_method(self):
        self.adapter = _make_adapter()

    def _ev(self, **kwargs):
        base = {"type": "prompt_rendering", "plugin_name": "P", "function_name": "F"}
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_event_type(self):
        assert self._ev().event_type == "prompt_rendering"

    def test_severity_info(self):
        assert self._ev().severity == "INFO"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("sk-prompt-")

    def test_plugin_name_in_attributes(self):
        ev = self._ev(plugin_name="PromptPlugin")
        assert ev.attributes["plugin_name"] == "PromptPlugin"

    def test_function_name_in_attributes(self):
        ev = self._ev(function_name="SummarizeFunc")
        assert ev.attributes["function_name"] == "SummarizeFunc"

    def test_is_streaming_default_false(self):
        assert self._ev().attributes["is_streaming"] is False

    def test_is_streaming_true(self):
        assert self._ev(is_streaming=True).attributes["is_streaming"] is True

    def test_body_mentions_function(self):
        ev = self._ev(plugin_name="Q", function_name="R")
        assert "Q" in ev.body and "R" in ev.body

    def test_source_system(self):
        assert self._ev().source_system == "semantic_kernel"


# ---------------------------------------------------------------------------
# TestFromPromptRendered
# ---------------------------------------------------------------------------


class TestFromPromptRendered:
    def setup_method(self):
        self.adapter = _make_adapter()

    def _ev(self, **kwargs):
        base = {"type": "prompt_rendered", "plugin_name": "P", "function_name": "F"}
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_event_type(self):
        assert self._ev().event_type == "prompt_rendered"

    def test_severity_info(self):
        assert self._ev().severity == "INFO"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("sk-prompt-")

    def test_rendered_prompt_captured(self):
        ev = self._ev(rendered_prompt="Summarize the following: ...")
        assert ev.attributes["rendered_prompt"] == "Summarize the following: ..."

    def test_rendered_prompt_empty_by_default(self):
        assert self._ev().attributes["rendered_prompt"] == ""

    def test_unique_event_ids(self):
        assert self._ev().event_id != self._ev().event_id


# ---------------------------------------------------------------------------
# TestFromAutoFunctionInvoking
# ---------------------------------------------------------------------------


class TestFromAutoFunctionInvoking:
    def setup_method(self):
        self.adapter = _make_adapter(run_id="trace-auto")

    def _ev(self, **kwargs):
        base = {
            "type": "auto_function_invoking",
            "plugin_name": "SearchPlugin",
            "function_name": "Search",
        }
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_event_type(self):
        assert self._ev().event_type == "auto_function_invoking"

    def test_severity_info(self):
        assert self._ev().severity == "INFO"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("sk-autofunc-")

    def test_plugin_name(self):
        ev = self._ev(plugin_name="CalcPlugin")
        assert ev.attributes["plugin_name"] == "CalcPlugin"

    def test_function_name(self):
        ev = self._ev(function_name="Multiply")
        assert ev.attributes["function_name"] == "Multiply"

    def test_request_sequence_index_default(self):
        assert self._ev().attributes["request_sequence_index"] == 0

    def test_request_sequence_index_set(self):
        ev = self._ev(request_sequence_index=2)
        assert ev.attributes["request_sequence_index"] == 2

    def test_function_sequence_index(self):
        ev = self._ev(function_sequence_index=1)
        assert ev.attributes["function_sequence_index"] == 1

    def test_function_count(self):
        ev = self._ev(function_count=3)
        assert ev.attributes["function_count"] == 3

    def test_is_streaming_default_false(self):
        assert self._ev().attributes["is_streaming"] is False

    def test_body_contains_plugin_and_function(self):
        ev = self._ev(plugin_name="X", function_name="Y")
        assert "X" in ev.body and "Y" in ev.body

    def test_trace_id_propagated(self):
        assert self._ev().trace_id == "trace-auto"


# ---------------------------------------------------------------------------
# TestFromAutoFunctionInvoked
# ---------------------------------------------------------------------------


class TestFromAutoFunctionInvoked:
    def setup_method(self):
        self.adapter = _make_adapter()

    def _ev(self, **kwargs):
        base = {"type": "auto_function_invoked", "plugin_name": "P", "function_name": "F"}
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_event_type(self):
        assert self._ev().event_type == "auto_function_invoked"

    def test_severity_info(self):
        assert self._ev().severity == "INFO"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("sk-autofunc-")

    def test_terminate_default_false(self):
        assert self._ev().attributes["terminate"] is False

    def test_terminate_true(self):
        ev = self._ev(terminate=True)
        assert ev.attributes["terminate"] is True

    def test_result_captured(self):
        ev = self._ev(result="Paris")
        assert ev.attributes["result"] == "Paris"

    def test_result_empty_by_default(self):
        assert self._ev().attributes["result"] == ""

    def test_body_contains_terminate_flag(self):
        ev = self._ev(terminate=True)
        assert "True" in ev.body

    def test_request_sequence_index(self):
        ev = self._ev(request_sequence_index=1)
        assert ev.attributes["request_sequence_index"] == 1

    def test_function_sequence_index(self):
        ev = self._ev(function_sequence_index=2)
        assert ev.attributes["function_sequence_index"] == 2


# ---------------------------------------------------------------------------
# TestFromAutoFunctionError
# ---------------------------------------------------------------------------


class TestFromAutoFunctionError:
    def setup_method(self):
        self.adapter = _make_adapter()

    def _ev(self, **kwargs):
        base = {"type": "auto_function_error", "plugin_name": "P", "function_name": "F"}
        base.update(kwargs)
        return self.adapter.to_sentinel_event(base)

    def test_event_type(self):
        assert self._ev().event_type == "auto_function_error"

    def test_severity_error(self):
        assert self._ev().severity == "ERROR"

    def test_event_id_prefix(self):
        assert self._ev().event_id.startswith("sk-autofunc-")

    def test_error_captured(self):
        ev = self._ev(error="not found")
        assert ev.attributes["error"] == "not found"

    def test_body_contains_error(self):
        ev = self._ev(error="timeout")
        assert "timeout" in ev.body

    def test_plugin_and_function_in_body(self):
        ev = self._ev(plugin_name="SQ", function_name="Run")
        assert "SQ" in ev.body and "Run" in ev.body


# ---------------------------------------------------------------------------
# TestFromUnknown
# ---------------------------------------------------------------------------


class TestFromUnknown:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_unknown_type_maps_to_unknown_event(self):
        ev = self.adapter.to_sentinel_event({"type": "totally_unknown"})
        assert ev.event_type == "unknown_sk_event"

    def test_severity_info(self):
        ev = self.adapter.to_sentinel_event({"type": "weird"})
        assert ev.severity == "INFO"

    def test_original_type_preserved(self):
        ev = self.adapter.to_sentinel_event({"type": "my_custom_span"})
        assert ev.attributes["original_type"] == "my_custom_span"

    def test_empty_dict_produces_unknown(self):
        ev = self.adapter.to_sentinel_event({})
        assert ev.event_type == "unknown_sk_event"

    def test_event_id_prefix(self):
        ev = self.adapter.to_sentinel_event({"type": "weird"})
        assert ev.event_id.startswith("sk-unknown-")

    def test_body_mentions_type(self):
        ev = self.adapter.to_sentinel_event({"type": "exotic_thing"})
        assert "exotic_thing" in ev.body


# ---------------------------------------------------------------------------
# TestTimestampParsing
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    def setup_method(self):
        self.adapter = _make_adapter()

    def test_parses_iso_timestamp(self):
        ev = self.adapter.to_sentinel_event(
            {
                "type": "function_invoking",
                "timestamp": "2026-04-01T10:00:00Z",
            }
        )
        assert ev.timestamp.year == 2026
        assert ev.timestamp.month == 4

    def test_parses_timestamp_with_offset(self):
        ev = self.adapter.to_sentinel_event(
            {
                "type": "function_invoking",
                "timestamp": "2026-04-01T10:00:00+00:00",
            }
        )
        assert ev.timestamp.year == 2026

    def test_falls_back_to_now_on_invalid_timestamp(self):
        before = datetime.now(UTC)
        ev = self.adapter.to_sentinel_event(
            {
                "type": "function_invoking",
                "timestamp": "not-a-date",
            }
        )
        after = datetime.now(UTC)
        assert before <= ev.timestamp <= after

    def test_falls_back_to_now_when_no_timestamp(self):
        before = datetime.now(UTC)
        ev = self.adapter.to_sentinel_event({"type": "function_invoking"})
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
        self._push("function_invoking")
        self._push("function_invoked")
        assert len(self.adapter.drain()) == 2

    def test_drain_clears_buffer(self):
        self._push("function_invoking")
        self.adapter.drain()
        assert self.adapter.drain() == []

    def test_drain_returns_list(self):
        self._push("function_invoking")
        assert isinstance(self.adapter.drain(), list)

    def test_flush_into_ingest(self):
        self._push("function_invoking")
        sentinel = Sentinel()
        self.adapter.flush_into(sentinel)
        assert isinstance(sentinel.detect_violations(), list)

    def test_flush_into_clears_buffer(self):
        self._push("function_invoking")
        self.adapter.flush_into(Sentinel())
        assert self.adapter.drain() == []

    def test_multiple_drain_calls_independent(self):
        self._push("function_invoking")
        first = self.adapter.drain()
        self._push("function_invoked")
        second = self.adapter.drain()
        assert len(first) == 1
        assert len(second) == 1

    def test_buffer_event_appends(self):
        self._push("function_invoking")
        self._push("prompt_rendering")
        self._push("auto_function_invoking")
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

        def push_events():
            try:
                for _ in range(50):
                    ev = adapter.to_sentinel_event({"type": "function_invoking"})
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
        drained: list[SentinelEvent] = []
        errors = []

        def producer():
            try:
                for _ in range(100):
                    ev = adapter.to_sentinel_event({"type": "function_invoking"})
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
    """
    Tests for setup(kernel): filter registration and async filter execution.

    Filter callables are extracted from mock_kernel.add_filter.call_args_list
    and run directly with asyncio.run() using lightweight mock contexts.
    """

    def _run_setup(self, run_id=None):
        """Call setup() with mocked SK modules. Returns (adapter, mock_kernel, filters)."""
        mock_kernel = MagicMock()
        adapter = _make_adapter(run_id=run_id)
        with patch.dict("sys.modules", _mock_sk_modules()):
            adapter.setup(mock_kernel)
        filters = _get_registered_filters(mock_kernel)
        return adapter, mock_kernel, filters

    def test_setup_registers_three_filters(self):
        _, mock_kernel, _ = self._run_setup()
        assert mock_kernel.add_filter.call_count == 3

    def test_setup_registers_function_invocation_filter(self):
        _, _, filters = self._run_setup()
        assert "function_invocation" in filters

    def test_setup_registers_prompt_rendering_filter(self):
        _, _, filters = self._run_setup()
        assert "prompt_rendering" in filters

    def test_setup_registers_auto_function_invocation_filter(self):
        _, _, filters = self._run_setup()
        assert "auto_function_invocation" in filters

    def test_function_invocation_filter_emits_invoking_and_invoked(self):
        adapter, _, filters = self._run_setup(run_id="setup-test")

        async def _run():
            ctx = _make_function_invocation_context(plugin_name="P", function_name="F")
            ctx.result = MagicMock()
            ctx.result.metadata = {"model": "gpt-4o"}

            async def next_fn(c):
                pass

            await filters["function_invocation"](ctx, next_fn)

        asyncio.run(_run())
        events = adapter.drain()
        types = [e.event_type for e in events]
        assert "function_invoking" in types
        assert "function_invoked" in types

    def test_function_invocation_filter_emits_error_on_exception(self):
        adapter, _, filters = self._run_setup()

        async def _run():
            ctx = _make_function_invocation_context()

            async def next_fn(c):
                raise ValueError("boom")

            with pytest.raises(ValueError):
                await filters["function_invocation"](ctx, next_fn)

        asyncio.run(_run())
        events = adapter.drain()
        types = [e.event_type for e in events]
        assert "function_invoking" in types
        assert "function_error" in types
        assert "function_invoked" not in types

    def test_function_error_captures_exception_message(self):
        adapter, _, filters = self._run_setup()

        async def _run():
            ctx = _make_function_invocation_context()

            async def next_fn(c):
                raise RuntimeError("service unavailable")

            with pytest.raises(RuntimeError):
                await filters["function_invocation"](ctx, next_fn)

        asyncio.run(_run())
        err_ev = next(e for e in adapter.drain() if e.event_type == "function_error")
        assert "service unavailable" in err_ev.attributes["error"]

    def test_prompt_rendering_filter_emits_rendering_and_rendered(self):
        adapter, _, filters = self._run_setup()

        async def _run():
            ctx = _make_prompt_render_context(rendered_prompt="Say hello.")

            async def next_fn(c):
                pass

            await filters["prompt_rendering"](ctx, next_fn)

        asyncio.run(_run())
        types = [e.event_type for e in adapter.drain()]
        assert "prompt_rendering" in types
        assert "prompt_rendered" in types

    def test_prompt_rendered_captures_rendered_prompt(self):
        adapter, _, filters = self._run_setup()

        async def _run():
            ctx = _make_prompt_render_context(rendered_prompt="Be concise.")

            async def next_fn(c):
                pass

            await filters["prompt_rendering"](ctx, next_fn)

        asyncio.run(_run())
        rendered_ev = next(e for e in adapter.drain() if e.event_type == "prompt_rendered")
        assert "Be concise." in rendered_ev.attributes["rendered_prompt"]

    def test_auto_function_invocation_filter_emits_invoking_and_invoked(self):
        adapter, _, filters = self._run_setup(run_id="auto-test")

        async def _run():
            ctx = _make_auto_function_context(
                plugin_name="SearchPlugin",
                function_name="Search",
                function_count=2,
                function_result="result text",
            )

            async def next_fn(c):
                pass

            await filters["auto_function_invocation"](ctx, next_fn)

        asyncio.run(_run())
        types = [e.event_type for e in adapter.drain()]
        assert "auto_function_invoking" in types
        assert "auto_function_invoked" in types

    def test_auto_function_invocation_filter_emits_error_on_exception(self):
        adapter, _, filters = self._run_setup()

        async def _run():
            ctx = _make_auto_function_context()

            async def next_fn(c):
                raise ConnectionError("unreachable")

            with pytest.raises(ConnectionError):
                await filters["auto_function_invocation"](ctx, next_fn)

        asyncio.run(_run())
        types = [e.event_type for e in adapter.drain()]
        assert "auto_function_error" in types

    def test_auto_function_invoked_captures_terminate_flag(self):
        adapter, _, filters = self._run_setup()

        async def _run():
            ctx = _make_auto_function_context(terminate=True, function_result="done")

            async def next_fn(c):
                pass

            await filters["auto_function_invocation"](ctx, next_fn)

        asyncio.run(_run())
        invoked_ev = next(e for e in adapter.drain() if e.event_type == "auto_function_invoked")
        assert invoked_ev.attributes["terminate"] is True

    def test_function_invoked_captures_metadata(self):
        adapter, _, filters = self._run_setup()

        async def _run():
            result_mock = MagicMock()
            result_mock.metadata = {"tokens": "150"}
            ctx = _make_function_invocation_context(result=result_mock)

            async def next_fn(c):
                pass

            await filters["function_invocation"](ctx, next_fn)

        asyncio.run(_run())
        invoked_ev = next(e for e in adapter.drain() if e.event_type == "function_invoked")
        assert invoked_ev.attributes["metadata"].get("tokens") == "150"

    def test_trace_id_propagated_through_filters(self):
        adapter, _, filters = self._run_setup(run_id="trace-propagate")

        async def _run():
            ctx = _make_function_invocation_context()
            ctx.result = None

            async def next_fn(c):
                pass

            await filters["function_invocation"](ctx, next_fn)

        asyncio.run(_run())
        for ev in adapter.drain():
            assert ev.trace_id == "trace-propagate"


# ---------------------------------------------------------------------------
# TestExtractArguments
# ---------------------------------------------------------------------------


class TestExtractArguments:
    def setup_method(self):
        with patch("agentcop.adapters.semantic_kernel._require_semantic_kernel"):
            from agentcop.adapters.semantic_kernel import _extract_arguments

            self._extract = _extract_arguments

    def test_extracts_dict_arguments(self):
        ctx = MagicMock()
        ctx.arguments = {"input": "hello", "temperature": "0.5"}
        result = self._extract(ctx)
        assert result["input"] == "hello"
        assert result["temperature"] == "0.5"

    def test_returns_empty_when_no_arguments(self):
        ctx = MagicMock()
        ctx.arguments = None
        result = self._extract(ctx)
        assert result == {}

    def test_swallows_exceptions(self):
        ctx = MagicMock()
        ctx.arguments = MagicMock(side_effect=RuntimeError("boom"))
        # Should not raise; returns empty dict
        result = self._extract(ctx)
        assert isinstance(result, dict)

    def test_values_truncated_to_200_chars(self):
        ctx = MagicMock()
        ctx.arguments = {"key": "x" * 300}
        result = self._extract(ctx)
        assert len(result["key"]) == 200


# ---------------------------------------------------------------------------
# TestProtocolConformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_conforms_to_sentinel_adapter_protocol(self):
        from agentcop.adapters import SentinelAdapter

        assert isinstance(_make_adapter(), SentinelAdapter)

    def test_has_source_system_attr(self):
        adapter = _make_adapter()
        assert hasattr(adapter, "source_system")
        assert adapter.source_system == "semantic_kernel"

    def test_to_sentinel_event_returns_sentinel_event(self):
        result = _make_adapter().to_sentinel_event({"type": "function_invoking"})
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
                {"type": "function_invoking"},
                {"type": "function_invoked"},
                {"type": "prompt_rendering"},
                {"type": "prompt_rendered"},
            ],
            detectors=DEFAULT_DETECTORS,
        )
        assert isinstance(s.detect_violations(), list)

    def test_detect_function_error(self):
        def detect_fn_error(event: SentinelEvent):
            if event.event_type != "function_error":
                return None
            return ViolationRecord(
                violation_type="function_execution_failed",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={"error": event.attributes.get("error")},
            )

        s = self._make_sentinel_with(
            [{"type": "function_error", "error": "auth failed"}],
            detectors=[detect_fn_error],
        )
        violations = s.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "function_execution_failed"

    def test_detect_auto_function_error(self):
        def detect_tool_error(event: SentinelEvent):
            if event.event_type != "auto_function_error":
                return None
            return ViolationRecord(
                violation_type="tool_call_failed",
                severity="ERROR",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={"error": event.attributes.get("error")},
            )

        s = self._make_sentinel_with(
            [{"type": "auto_function_error", "plugin_name": "SearchPlugin", "error": "timeout"}],
            detectors=[detect_tool_error],
        )
        violations = s.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "tool_call_failed"

    def test_detect_restricted_plugin(self):
        RESTRICTED = {"DangerPlugin", "ExecPlugin"}

        def detect_restricted(event: SentinelEvent):
            if event.event_type not in ("function_invoking", "auto_function_invoking"):
                return None
            if event.attributes.get("plugin_name") not in RESTRICTED:
                return None
            return ViolationRecord(
                violation_type="restricted_plugin_called",
                severity="CRITICAL",
                source_event_id=event.event_id,
                trace_id=event.trace_id,
                detail={"plugin_name": event.attributes["plugin_name"]},
            )

        s = self._make_sentinel_with(
            [{"type": "function_invoking", "plugin_name": "DangerPlugin", "function_name": "Run"}],
            detectors=[detect_restricted],
        )
        violations = s.detect_violations()
        assert len(violations) == 1
        assert violations[0].violation_type == "restricted_plugin_called"
        assert violations[0].severity == "CRITICAL"

    def test_no_violations_on_clean_run(self):
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
                {"type": "function_invoking"},
                {"type": "prompt_rendering"},
                {"type": "prompt_rendered"},
                {"type": "function_invoked"},
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
                {"type": "function_error", "error": "fail1"},
                {"type": "auto_function_error", "error": "fail2"},
            ],
            detectors=[detect_any_error],
        )
        assert len(s.detect_violations()) == 2


# ---------------------------------------------------------------------------
# Runtime security tests
# ---------------------------------------------------------------------------


def _make_sk_runtime(gate=None, permissions=None, sandbox=None, approvals=None):
    with patch("agentcop.adapters.semantic_kernel._require_semantic_kernel"):
        from agentcop.adapters.semantic_kernel import SemanticKernelSentinelAdapter

        return SemanticKernelSentinelAdapter(
            run_id="rt-run",
            gate=gate,
            permissions=permissions,
            sandbox=sandbox,
            approvals=approvals,
        )


class TestRuntimeSecuritySemanticKernel:
    def test_init_stores_none_by_default(self):
        a = _make_sk_runtime()
        assert a._gate is None
        assert a._permissions is None

    def test_init_stores_runtime_params(self):
        gate = MagicMock()
        perms = MagicMock()
        a = _make_sk_runtime(gate=gate, permissions=perms)
        assert a._gate is gate
        assert a._permissions is perms

    def test_gate_denial_fires_event(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=False, reason="blocked", risk_score=90)
        a = _make_sk_runtime(gate=gate)
        from agentcop.adapters._runtime import check_tool_call

        with pytest.raises(PermissionError):
            check_tool_call(a, "SearchPlugin.Search", {"query": "test"})
        events = a.drain()
        assert any(e.event_type == "gate_denied" for e in events)

    def test_permission_violation_fires_event(self):
        perms = MagicMock()
        perms.verify.return_value = MagicMock(granted=False, reason="not permitted")
        a = _make_sk_runtime(permissions=perms)
        from agentcop.adapters._runtime import check_tool_call

        with pytest.raises(PermissionError):
            check_tool_call(a, "EmailPlugin.Send", {})
        events = a.drain()
        assert any(e.event_type == "permission_violation" for e in events)

    def test_gate_allow_passes_through(self):
        gate = MagicMock()
        gate.check.return_value = MagicMock(allowed=True, reason="ok", risk_score=5)
        a = _make_sk_runtime(gate=gate)
        from agentcop.adapters._runtime import check_tool_call

        check_tool_call(a, "SafePlugin.Func", {})
        assert a.drain() == []

    def test_no_gate_backward_compatible(self):
        a = _make_sk_runtime()
        event = a.to_sentinel_event(
            {"type": "function_invoking", "plugin_name": "P", "function_name": "F"}
        )
        assert event.event_type == "function_invoking"


# ---------------------------------------------------------------------------
# Trust integration
# ---------------------------------------------------------------------------


def _make_adapter_trust(**kwargs):
    with patch("agentcop.adapters.semantic_kernel._require_semantic_kernel"):
        from agentcop.adapters.semantic_kernel import SemanticKernelSentinelAdapter

        return SemanticKernelSentinelAdapter(**kwargs)


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

    def test_function_invoked_calls_add_node(self):
        import asyncio

        trust = MagicMock()
        a = _make_adapter_trust(trust=trust)

        kernel = MagicMock()
        kernel.add_filter = MagicMock()
        with patch.dict("sys.modules", _mock_sk_modules()):
            a.setup(kernel)

        # Capture the function_invocation filter registered with the kernel
        calls = kernel.add_filter.call_args_list
        assert len(calls) >= 1

        # Simulate the filter execution
        async def _run():
            fn_filter = calls[0][0][1]
            ctx = MagicMock()
            ctx.function.plugin_name = "MyPlugin"
            ctx.function.name = "my_func"
            ctx.function.is_prompt = False
            ctx.is_streaming = False
            ctx.result = None

            async def _next(c):
                pass

            await fn_filter(ctx, _next)

        asyncio.run(_run())
        trust.add_node.assert_called_once()
