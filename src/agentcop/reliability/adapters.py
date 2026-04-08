"""
Reliability mixin and framework-specific wrappers.

``ReliabilityMixin`` provides shared tracer lifecycle helpers.
Framework adapters (LangChain, CrewAI, AutoGen) inherit from it and hook
into their respective event/callback systems.

The ``@track_reliability`` decorator works with any plain Python function.

All framework adapters guard their optional imports with ``_require_*``
functions — the mixin itself has no dependencies beyond stdlib.
"""

import functools
import time
from typing import Any

from .instrumentation import ReliabilityTracer
from .models import AgentRun

# ---------------------------------------------------------------------------
# ReliabilityMixin
# ---------------------------------------------------------------------------


class ReliabilityMixin:
    """
    Mixin that adds a managed :class:`ReliabilityTracer` to any class.

    Subclasses call :meth:`_start_run` at execution start and
    :meth:`_end_run` at execution end.  The completed
    :class:`~agentcop.reliability.models.AgentRun` is returned by
    :meth:`_end_run` and also stored as ``self._reliability_tracer.run``.
    """

    _reliability_tracer: ReliabilityTracer | None = None

    def _start_run(
        self,
        agent_id: str,
        input_data: Any = None,
        store: Any = None,
        metadata: dict | None = None,
    ) -> ReliabilityTracer:
        self._reliability_tracer = ReliabilityTracer(
            agent_id, input_data=input_data, store=store, metadata=metadata
        )
        self._reliability_tracer._start_time = time.monotonic()
        return self._reliability_tracer

    def _end_run(self, output: Any = None, *, exc: BaseException | None = None) -> AgentRun | None:
        tracer: ReliabilityTracer | None = getattr(self, "_reliability_tracer", None)
        if tracer is None:
            return None
        if output is not None:
            tracer.set_output(output)
        if exc is not None:
            tracer._success = False
        tracer.run = tracer._build_run()
        if tracer._store is not None:
            tracer._store.record_run(tracer.agent_id, tracer.run)
        self._reliability_tracer = None
        return tracer.run

    def _get_tracer(self) -> ReliabilityTracer | None:
        return getattr(self, "_reliability_tracer", None)


# ---------------------------------------------------------------------------
# LangChain callback
# ---------------------------------------------------------------------------


def _require_langchain() -> None:
    try:
        from langchain_core.callbacks import BaseCallbackHandler  # noqa: F401
    except ImportError:
        try:
            from langchain.callbacks.base import BaseCallbackHandler  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "LangChainReliabilityCallback requires 'langchain-core' or 'langchain'. "
                "Install it with: pip install langchain-core"
            ) from exc


class LangChainReliabilityCallback(ReliabilityMixin):
    """
    LangChain ``BaseCallbackHandler`` that captures tool calls and chain steps
    as a :class:`~agentcop.reliability.models.AgentRun`.

    Usage::

        cb = LangChainReliabilityCallback("my-agent", store=store)
        chain.invoke(inputs, config={"callbacks": [cb]})
        run = cb.last_run   # AgentRun after chain completes
    """

    def __init__(self, agent_id: str, *, store: Any = None) -> None:
        _require_langchain()
        self.agent_id = agent_id
        self._store = store
        self.last_run: AgentRun | None = None
        self._reliability_tracer: ReliabilityTracer | None = None

    # LangChain callback interface -------------------------------------------

    def on_chain_start(self, serialized: dict, inputs: dict, **kwargs: Any) -> None:
        if self._reliability_tracer is None:
            self._start_run(self.agent_id, input_data=inputs, store=self._store)

    def on_chain_end(self, outputs: dict, **kwargs: Any) -> None:
        run = self._end_run(output=outputs)
        if run:
            self.last_run = run

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        run = self._end_run(exc=error)
        if run:
            self.last_run = run

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        tracer = self._get_tracer()
        if tracer:
            tracer._metadata["_pending_tool"] = serialized.get("name", "unknown_tool")
            tracer._metadata["_pending_tool_input"] = input_str

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        tracer = self._get_tracer()
        if tracer:
            name = tracer._metadata.pop("_pending_tool", "unknown_tool")
            inp = tracer._metadata.pop("_pending_tool_input", None)
            tracer.record_tool_call(name, inp, output)

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        tracer = self._get_tracer()
        if tracer:
            name = tracer._metadata.pop("_pending_tool", "unknown_tool")
            inp = tracer._metadata.pop("_pending_tool_input", None)
            tracer.record_tool_call(name, inp, str(error), success=False)

    def on_agent_action(self, action: Any, **kwargs: Any) -> None:
        tracer = self._get_tracer()
        if tracer:
            step = getattr(action, "tool", None) or str(action)
            tracer.record_branch(str(step))

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        tracer = self._get_tracer()
        if tracer and hasattr(response, "llm_output"):
            usage = (response.llm_output or {}).get("token_usage", {})
            tracer.record_tokens(
                input=usage.get("prompt_tokens", 0),
                output=usage.get("completion_tokens", 0),
            )


# ---------------------------------------------------------------------------
# CrewAI handler
# ---------------------------------------------------------------------------


def _require_crewai() -> None:
    try:
        import crewai  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "CrewAIReliabilityHandler requires 'crewai'. "
            "Install it with: pip install agentcop[crewai]"
        ) from exc


class CrewAIReliabilityHandler(ReliabilityMixin):
    """
    CrewAI event bus handler that captures task/tool executions.

    Call :meth:`setup` once before :py:meth:`crew.kickoff` to register
    all handlers with the CrewAI event bus.

    Usage::

        handler = CrewAIReliabilityHandler("my-crew", store=store)
        handler.setup()
        crew.kickoff()
        run = handler.last_run
    """

    def __init__(self, agent_id: str, *, store: Any = None) -> None:
        _require_crewai()
        self.agent_id = agent_id
        self._store = store
        self._reliability_tracer: ReliabilityTracer | None = None
        self._pending_tool: str | None = None
        self.last_run: AgentRun | None = None

    def setup(self) -> None:
        """Register with the CrewAI event bus. No-op on older CrewAI versions."""
        try:
            from crewai.utilities.events import crewai_event_bus
        except ImportError:
            return
        crewai_event_bus.on("crew_kickoff_started", self._on_kickoff_started)
        crewai_event_bus.on("crew_kickoff_completed", self._on_kickoff_completed)
        crewai_event_bus.on("crew_kickoff_failed", self._on_kickoff_failed)
        crewai_event_bus.on("tool_usage_started", self._on_tool_started)
        crewai_event_bus.on("tool_usage_finished", self._on_tool_finished)
        crewai_event_bus.on("tool_usage_error", self._on_tool_error)
        crewai_event_bus.on("agent_execution_started", self._on_agent_started)

    def _on_kickoff_started(self, event: Any) -> None:
        self._start_run(self.agent_id, store=self._store)

    def _on_kickoff_completed(self, event: Any) -> None:
        run = self._end_run()
        if run:
            self.last_run = run

    def _on_kickoff_failed(self, event: Any) -> None:
        run = self._end_run(exc=RuntimeError(getattr(event, "error", "kickoff_failed")))
        if run:
            self.last_run = run

    def _on_tool_started(self, event: Any) -> None:
        self._pending_tool = getattr(event, "tool_name", "unknown")

    def _on_tool_finished(self, event: Any) -> None:
        tracer = self._get_tracer()
        if tracer and self._pending_tool:
            tracer.record_tool_call(
                self._pending_tool,
                getattr(event, "tool_args", None),
                getattr(event, "tool_output", None),
            )
        self._pending_tool = None

    def _on_tool_error(self, event: Any) -> None:
        tracer = self._get_tracer()
        if tracer and self._pending_tool:
            tracer.record_tool_call(
                self._pending_tool,
                None,
                getattr(event, "error", "error"),
                success=False,
            )
        self._pending_tool = None

    def _on_agent_started(self, event: Any) -> None:
        tracer = self._get_tracer()
        if tracer:
            role = getattr(event, "agent_role", None) or getattr(event, "agent", "agent")
            tracer.record_branch(str(role))


# ---------------------------------------------------------------------------
# AutoGen wrapper
# ---------------------------------------------------------------------------


def _require_autogen() -> None:
    try:
        import autogen  # noqa: F401
    except ImportError:
        try:
            import autogen_agentchat  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "AutoGenReliabilityWrapper requires 'pyautogen' or 'autogen-agentchat'. "
                "Install it with: pip install agentcop[autogen]"
            ) from exc


class AutoGenReliabilityWrapper(ReliabilityMixin):
    """
    Wraps AutoGen ``function_map`` and conversation turns for reliability tracing.

    Usage::

        wrapper = AutoGenReliabilityWrapper("my-agent", store=store)
        user_proxy.function_map = wrapper.wrap_function_map(user_proxy.function_map)

        with wrapper.track_conversation(initial_message) as tracer:
            user_proxy.initiate_chat(assistant, message=initial_message)

        run = wrapper.last_run
    """

    def __init__(self, agent_id: str, *, store: Any = None) -> None:
        _require_autogen()
        self.agent_id = agent_id
        self._store = store
        self._reliability_tracer: ReliabilityTracer | None = None
        self.last_run: AgentRun | None = None

    def wrap_function_map(self, function_map: dict[str, Any]) -> dict[str, Any]:
        """Return a new function_map with each function wrapped for tracing."""
        return {name: self._tracked_fn(name, fn) for name, fn in function_map.items()}

    def _tracked_fn(self, name: str, fn: Any) -> Any:
        @functools.wraps(fn)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = self._get_tracer()
            try:
                result = fn(*args, **kwargs)
                if tracer:
                    tracer.record_tool_call(name, {"args": args, "kwargs": kwargs}, result)
                    tracer.record_branch(name)
                return result
            except Exception as exc:
                if tracer:
                    tracer.record_tool_call(
                        name, {"args": args, "kwargs": kwargs}, str(exc), success=False
                    )
                raise

        return _wrapper

    def track_conversation(self, initial_message: Any = None) -> "AutoGenReliabilityWrapper":
        """Context manager that scopes a tracer to one conversation."""
        self._pending_input = initial_message
        return self

    def __enter__(self) -> "AutoGenReliabilityWrapper":
        self._start_run(
            self.agent_id,
            input_data=getattr(self, "_pending_input", None),
            store=self._store,
        )
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        run = self._end_run(exc=exc_val)
        if run:
            self.last_run = run


# ---------------------------------------------------------------------------
# @track_reliability decorator
# ---------------------------------------------------------------------------


def track_reliability(
    agent_id: str,
    *,
    store: Any = None,
    input_arg: str | None = None,
) -> Any:
    """
    Decorator that wraps a function and records each call as an
    :class:`~agentcop.reliability.models.AgentRun`.

    The first positional argument (or ``input_arg``-named keyword) is used
    as the input for hashing; the return value becomes the output hash.

    Usage::

        @track_reliability("my-agent", store=store)
        def run_agent(task: str) -> str:
            ...

        @track_reliability("searcher", input_arg="query")
        def search(query: str, limit: int = 10) -> list[str]:
            ...
    """

    def decorator(fn: Any) -> Any:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if input_arg is not None:
                raw_input = kwargs.get(input_arg, args[0] if args else None)
            else:
                raw_input = args[0] if args else None

            with ReliabilityTracer(agent_id, input_data=raw_input, store=store) as tracer:
                result = fn(*args, **kwargs)
                tracer.set_output(result)
            return result

        wrapper._reliability_agent_id = agent_id  # type: ignore[attr-defined]
        return wrapper

    return decorator
