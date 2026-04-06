"""
Semantic Kernel adapter for agentcop.

Translates Semantic Kernel 1.x filter events — function invocation, prompt
rendering, and auto-function invocation (LLM tool calls) — into SentinelEvents
for forensic auditing.

Semantic Kernel instruments every kernel call through an async filter chain.
This adapter registers three filters on a ``Kernel`` instance via
``kernel.add_filter()``, buffers translated SentinelEvents for each span, and
exposes them via ``flush_into(sentinel)`` after the call completes.

Supported event categories and their SentinelEvent mapping:

  Function:      function_invoking / function_invoked / function_error
  Prompt:        prompt_rendering / prompt_rendered
  Auto-function: auto_function_invoking / auto_function_invoked / auto_function_error

Install the optional dependency to use this adapter:

    pip install agentcop[semantic-kernel]

Quickstart::

    import asyncio
    from semantic_kernel import Kernel
    from agentcop import Sentinel
    from agentcop.adapters.semantic_kernel import SemanticKernelSentinelAdapter

    kernel = Kernel()
    # ... add plugins and AI services ...

    adapter = SemanticKernelSentinelAdapter(run_id="run-001")
    adapter.setup(kernel)   # registers three filters on the kernel

    async def main():
        result = await kernel.invoke("MyPlugin", "MyFunction", input="hello")

        sentinel = Sentinel()
        adapter.flush_into(sentinel)
        violations = sentinel.detect_violations()
        sentinel.report()

    asyncio.run(main())

``to_sentinel_event(raw)`` also accepts plain dicts for manual translation
or testing without a live kernel::

    event = adapter.to_sentinel_event({
        "type": "function_error",
        "plugin_name": "MyPlugin",
        "function_name": "Search",
        "error": "connection refused",
    })
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from agentcop.adapters._runtime import check_tool_call
from agentcop.event import SentinelEvent


def _require_semantic_kernel() -> None:
    try:
        import semantic_kernel  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Semantic Kernel adapter requires 'semantic-kernel'. "
            "Install it with: pip install agentcop[semantic-kernel]"
        ) from exc


class SemanticKernelSentinelAdapter:
    """
    Adapter that translates Semantic Kernel filter events into SentinelEvents.

    Semantic Kernel uses a filter middleware chain for observability: three
    filter types (FUNCTION_INVOCATION, PROMPT_RENDERING,
    AUTO_FUNCTION_INVOCATION) wrap every kernel function call as async
    middleware. This adapter registers one filter per type on the provided
    ``Kernel`` instance, buffers translated events, and exposes them via
    ``flush_into(sentinel)``.

    **Normalized dict schema** (what ``to_sentinel_event`` accepts):

    All dicts must have a ``"type"`` key matching one of the supported event
    type strings. Additional keys are type-specific (see table below).

    +---------------------------+-------------------------------+-----------+
    | type                      | event_type (SentinelEvent)    | severity  |
    +===========================+===============================+===========+
    | function_invoking         | function_invoking             | INFO      |
    | function_invoked          | function_invoked              | INFO      |
    | function_error            | function_error                | ERROR     |
    | prompt_rendering          | prompt_rendering              | INFO      |
    | prompt_rendered           | prompt_rendered               | INFO      |
    | auto_function_invoking    | auto_function_invoking        | INFO      |
    | auto_function_invoked     | auto_function_invoked         | INFO      |
    | auto_function_error       | auto_function_error           | ERROR     |
    | (anything else)           | unknown_sk_event              | INFO      |
    +---------------------------+-------------------------------+-----------+

    Parameters
    ----------
    run_id:
        Optional run / session identifier used as ``trace_id`` on every
        translated event. Recommended: pass a UUID per kernel session.
    """

    source_system = "semantic_kernel"

    def __init__(
        self,
        run_id: str | None = None,
        *,
        gate=None,
        permissions=None,
        sandbox=None,
        approvals=None,
        identity=None,
    ) -> None:
        _require_semantic_kernel()
        self._run_id = run_id
        self._gate = gate
        self._permissions = permissions
        self._sandbox = sandbox
        self._approvals = approvals
        self._identity = identity
        self._buffer: list[SentinelEvent] = []
        self._lock = threading.Lock()

    def setup(self, kernel) -> None:
        """
        Register agentcop observation filters on a Semantic Kernel ``Kernel``.

        Adds three async filters:

        - ``FUNCTION_INVOCATION`` — emits ``function_invoking`` before and
          ``function_invoked`` (or ``function_error``) after every kernel
          function call.
        - ``PROMPT_RENDERING`` — emits ``prompt_rendering`` before and
          ``prompt_rendered`` after every prompt template render.
        - ``AUTO_FUNCTION_INVOCATION`` — emits ``auto_function_invoking``
          before and ``auto_function_invoked`` (or ``auto_function_error``)
          after every LLM-initiated tool call.

        Parameters
        ----------
        kernel:
            A ``semantic_kernel.Kernel`` instance. Filters are added
            in-place; the kernel is otherwise unchanged.
        """
        from semantic_kernel.filters.filter_types import FilterTypes  # type: ignore[import]

        adapter_self = self

        async def _function_invocation_filter(context, next) -> None:
            plugin_name = str(getattr(context.function, "plugin_name", None) or "unknown")
            function_name = str(getattr(context.function, "name", None) or "unknown")
            is_prompt = bool(getattr(context.function, "is_prompt", False))
            is_streaming = bool(getattr(context, "is_streaming", False))

            arguments = _extract_arguments(context)
            adapter_self._buffer_event(
                adapter_self.to_sentinel_event(
                    {
                        "type": "function_invoking",
                        "plugin_name": plugin_name,
                        "function_name": function_name,
                        "is_prompt": is_prompt,
                        "is_streaming": is_streaming,
                        "arguments": arguments,
                    }
                )
            )

            if adapter_self._gate or adapter_self._permissions:
                check_tool_call(
                    adapter_self,
                    f"{plugin_name}.{function_name}",
                    dict(arguments),
                    context={"is_prompt": is_prompt, "is_streaming": is_streaming},
                )

            try:
                await next(context)
            except Exception as exc:
                adapter_self._buffer_event(
                    adapter_self.to_sentinel_event(
                        {
                            "type": "function_error",
                            "plugin_name": plugin_name,
                            "function_name": function_name,
                            "is_prompt": is_prompt,
                            "error": str(exc),
                        }
                    )
                )
                raise

            result = getattr(context, "result", None)
            result_str = str(result)[:500] if result is not None else ""
            metadata: dict[str, str] = {}
            if result is not None:
                meta = getattr(result, "metadata", None)
                if isinstance(meta, dict):
                    metadata = {k: str(v) for k, v in meta.items()}

            adapter_self._buffer_event(
                adapter_self.to_sentinel_event(
                    {
                        "type": "function_invoked",
                        "plugin_name": plugin_name,
                        "function_name": function_name,
                        "is_prompt": is_prompt,
                        "is_streaming": is_streaming,
                        "result": result_str,
                        "metadata": metadata,
                    }
                )
            )

        async def _prompt_rendering_filter(context, next) -> None:
            plugin_name = str(getattr(context.function, "plugin_name", None) or "unknown")
            function_name = str(getattr(context.function, "name", None) or "unknown")
            is_streaming = bool(getattr(context, "is_streaming", False))

            adapter_self._buffer_event(
                adapter_self.to_sentinel_event(
                    {
                        "type": "prompt_rendering",
                        "plugin_name": plugin_name,
                        "function_name": function_name,
                        "is_streaming": is_streaming,
                    }
                )
            )

            await next(context)

            rendered_prompt = str(getattr(context, "rendered_prompt", None) or "")
            adapter_self._buffer_event(
                adapter_self.to_sentinel_event(
                    {
                        "type": "prompt_rendered",
                        "plugin_name": plugin_name,
                        "function_name": function_name,
                        "rendered_prompt": rendered_prompt[:500],
                    }
                )
            )

        async def _auto_function_invocation_filter(context, next) -> None:
            plugin_name = str(getattr(context.function, "plugin_name", None) or "unknown")
            function_name = str(getattr(context.function, "name", None) or "unknown")
            request_seq = int(getattr(context, "request_sequence_index", 0))
            function_seq = int(getattr(context, "function_sequence_index", 0))
            function_count = int(getattr(context, "function_count", 0))
            is_streaming = bool(getattr(context, "is_streaming", False))

            adapter_self._buffer_event(
                adapter_self.to_sentinel_event(
                    {
                        "type": "auto_function_invoking",
                        "plugin_name": plugin_name,
                        "function_name": function_name,
                        "request_sequence_index": request_seq,
                        "function_sequence_index": function_seq,
                        "function_count": function_count,
                        "is_streaming": is_streaming,
                    }
                )
            )

            try:
                await next(context)
            except Exception as exc:
                adapter_self._buffer_event(
                    adapter_self.to_sentinel_event(
                        {
                            "type": "auto_function_error",
                            "plugin_name": plugin_name,
                            "function_name": function_name,
                            "error": str(exc),
                        }
                    )
                )
                raise

            result = getattr(context, "function_result", None)
            result_str = str(result)[:500] if result is not None else ""
            terminate = bool(getattr(context, "terminate", False))

            adapter_self._buffer_event(
                adapter_self.to_sentinel_event(
                    {
                        "type": "auto_function_invoked",
                        "plugin_name": plugin_name,
                        "function_name": function_name,
                        "request_sequence_index": request_seq,
                        "function_sequence_index": function_seq,
                        "terminate": terminate,
                        "result": result_str,
                    }
                )
            )

        kernel.add_filter(FilterTypes.FUNCTION_INVOCATION, _function_invocation_filter)
        kernel.add_filter(FilterTypes.PROMPT_RENDERING, _prompt_rendering_filter)
        kernel.add_filter(FilterTypes.AUTO_FUNCTION_INVOCATION, _auto_function_invocation_filter)

    def to_sentinel_event(self, raw: dict[str, Any]) -> SentinelEvent:
        """
        Translate one Semantic Kernel event dict into a SentinelEvent.

        ``raw`` must contain a ``"type"`` key. All other keys are
        type-specific. Unknown types are translated to ``unknown_sk_event``
        with severity INFO.
        """
        dispatch = {
            "function_invoking": self._from_function_invoking,
            "function_invoked": self._from_function_invoked,
            "function_error": self._from_function_error,
            "prompt_rendering": self._from_prompt_rendering,
            "prompt_rendered": self._from_prompt_rendered,
            "auto_function_invoking": self._from_auto_function_invoking,
            "auto_function_invoked": self._from_auto_function_invoked,
            "auto_function_error": self._from_auto_function_error,
        }
        handler = dispatch.get(raw.get("type", ""), self._from_unknown)
        return handler(raw)

    def drain(self) -> list[SentinelEvent]:
        """Return all buffered SentinelEvents and clear the buffer."""
        with self._lock:
            events = list(self._buffer)
            self._buffer.clear()
            return events

    def flush_into(self, sentinel) -> None:
        """Ingest all buffered events into a Sentinel instance, then clear."""
        sentinel.ingest(self.drain())

    # ------------------------------------------------------------------
    # Internal buffer
    # ------------------------------------------------------------------

    def _buffer_event(self, event: SentinelEvent) -> None:
        with self._lock:
            self._buffer.append(event)

    # ------------------------------------------------------------------
    # Timestamp helper
    # ------------------------------------------------------------------

    def _parse_timestamp(self, raw: dict[str, Any]) -> datetime:
        ts = raw.get("timestamp")
        if ts:
            try:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(UTC)

    # ------------------------------------------------------------------
    # Private translators — function invocation
    # ------------------------------------------------------------------

    def _from_function_invoking(self, raw: dict[str, Any]) -> SentinelEvent:
        plugin_name = raw.get("plugin_name", "unknown")
        function_name = raw.get("function_name", "unknown")
        is_prompt = bool(raw.get("is_prompt", False))
        is_streaming = bool(raw.get("is_streaming", False))
        arguments = raw.get("arguments") or {}
        return SentinelEvent(
            event_id=f"sk-function-{uuid.uuid4()}",
            event_type="function_invoking",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"function '{plugin_name}.{function_name}' invoking",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "plugin_name": plugin_name,
                "function_name": function_name,
                "is_prompt": is_prompt,
                "is_streaming": is_streaming,
                "arguments": dict(arguments),
            },
        )

    def _from_function_invoked(self, raw: dict[str, Any]) -> SentinelEvent:
        plugin_name = raw.get("plugin_name", "unknown")
        function_name = raw.get("function_name", "unknown")
        is_prompt = bool(raw.get("is_prompt", False))
        is_streaming = bool(raw.get("is_streaming", False))
        result = raw.get("result", "")
        metadata = raw.get("metadata") or {}
        return SentinelEvent(
            event_id=f"sk-function-{uuid.uuid4()}",
            event_type="function_invoked",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"function '{plugin_name}.{function_name}' invoked",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "plugin_name": plugin_name,
                "function_name": function_name,
                "is_prompt": is_prompt,
                "is_streaming": is_streaming,
                "result": result,
                "metadata": dict(metadata),
            },
        )

    def _from_function_error(self, raw: dict[str, Any]) -> SentinelEvent:
        plugin_name = raw.get("plugin_name", "unknown")
        function_name = raw.get("function_name", "unknown")
        is_prompt = bool(raw.get("is_prompt", False))
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"sk-function-{uuid.uuid4()}",
            event_type="function_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"function '{plugin_name}.{function_name}' error: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "plugin_name": plugin_name,
                "function_name": function_name,
                "is_prompt": is_prompt,
                "error": error,
            },
        )

    # ------------------------------------------------------------------
    # Private translators — prompt rendering
    # ------------------------------------------------------------------

    def _from_prompt_rendering(self, raw: dict[str, Any]) -> SentinelEvent:
        plugin_name = raw.get("plugin_name", "unknown")
        function_name = raw.get("function_name", "unknown")
        is_streaming = bool(raw.get("is_streaming", False))
        return SentinelEvent(
            event_id=f"sk-prompt-{uuid.uuid4()}",
            event_type="prompt_rendering",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"prompt rendering for '{plugin_name}.{function_name}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "plugin_name": plugin_name,
                "function_name": function_name,
                "is_streaming": is_streaming,
            },
        )

    def _from_prompt_rendered(self, raw: dict[str, Any]) -> SentinelEvent:
        plugin_name = raw.get("plugin_name", "unknown")
        function_name = raw.get("function_name", "unknown")
        rendered_prompt = raw.get("rendered_prompt", "")
        return SentinelEvent(
            event_id=f"sk-prompt-{uuid.uuid4()}",
            event_type="prompt_rendered",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"prompt rendered for '{plugin_name}.{function_name}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "plugin_name": plugin_name,
                "function_name": function_name,
                "rendered_prompt": rendered_prompt,
            },
        )

    # ------------------------------------------------------------------
    # Private translators — auto-function invocation (LLM tool calls)
    # ------------------------------------------------------------------

    def _from_auto_function_invoking(self, raw: dict[str, Any]) -> SentinelEvent:
        plugin_name = raw.get("plugin_name", "unknown")
        function_name = raw.get("function_name", "unknown")
        request_seq = int(raw.get("request_sequence_index", 0))
        function_seq = int(raw.get("function_sequence_index", 0))
        function_count = int(raw.get("function_count", 0))
        is_streaming = bool(raw.get("is_streaming", False))
        return SentinelEvent(
            event_id=f"sk-autofunc-{uuid.uuid4()}",
            event_type="auto_function_invoking",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=(
                f"auto-function '{plugin_name}.{function_name}' invoking "
                f"(req={request_seq}, fn={function_seq}/{function_count})"
            ),
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "plugin_name": plugin_name,
                "function_name": function_name,
                "request_sequence_index": request_seq,
                "function_sequence_index": function_seq,
                "function_count": function_count,
                "is_streaming": is_streaming,
            },
        )

    def _from_auto_function_invoked(self, raw: dict[str, Any]) -> SentinelEvent:
        plugin_name = raw.get("plugin_name", "unknown")
        function_name = raw.get("function_name", "unknown")
        request_seq = int(raw.get("request_sequence_index", 0))
        function_seq = int(raw.get("function_sequence_index", 0))
        terminate = bool(raw.get("terminate", False))
        result = raw.get("result", "")
        return SentinelEvent(
            event_id=f"sk-autofunc-{uuid.uuid4()}",
            event_type="auto_function_invoked",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"auto-function '{plugin_name}.{function_name}' invoked (terminate={terminate})",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "plugin_name": plugin_name,
                "function_name": function_name,
                "request_sequence_index": request_seq,
                "function_sequence_index": function_seq,
                "terminate": terminate,
                "result": result,
            },
        )

    def _from_auto_function_error(self, raw: dict[str, Any]) -> SentinelEvent:
        plugin_name = raw.get("plugin_name", "unknown")
        function_name = raw.get("function_name", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"sk-autofunc-{uuid.uuid4()}",
            event_type="auto_function_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"auto-function '{plugin_name}.{function_name}' error: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={
                "plugin_name": plugin_name,
                "function_name": function_name,
                "error": error,
            },
        )

    # ------------------------------------------------------------------
    # Private translator — unknown
    # ------------------------------------------------------------------

    def _from_unknown(self, raw: dict[str, Any]) -> SentinelEvent:
        original_type = raw.get("type", "unknown")
        return SentinelEvent(
            event_id=f"sk-unknown-{uuid.uuid4()}",
            event_type="unknown_sk_event",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"unknown Semantic Kernel event type '{original_type}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"original_type": original_type},
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _extract_arguments(context) -> dict[str, str]:
    """
    Safely extract ``KernelArguments`` from a filter context as a plain dict.

    ``KernelArguments`` behaves like a mapping. Values are truncated to 200
    chars and converted to strings. Swallows all exceptions so the filter
    never raises due to argument introspection.
    """
    try:
        args = getattr(context, "arguments", None)
        if args is None:
            return {}
        return {str(k): str(v)[:200] for k, v in dict(args).items()}
    except Exception:
        return {}
