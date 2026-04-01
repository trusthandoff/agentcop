"""
AutoGen adapter for agentcop.

Translates AutoGen agent conversation and function-call events into
SentinelEvents for forensic auditing.

Supports two usage patterns:

**Pull-based** (AutoGen 0.2.x / pyautogen): after a chat completes, pass
``chat_result.chat_history`` to ``iter_messages()`` to translate the full
conversation history in one pass.

**Push-based** (incremental auditing): call ``to_sentinel_event()`` directly
as events occur, buffer them with ``_buffer_event()``, and drain into a
Sentinel via ``flush_into()``.

Both AutoGen 0.2.x (pyautogen) and AutoGen 0.4.x (autogen-agentchat) native
message formats are understood by ``iter_messages()``.

Install the optional dependency to use this adapter:

    pip install agentcop[autogen]

Quickstart (AutoGen 0.2.x)::

    from autogen import AssistantAgent, UserProxyAgent
    from agentcop import Sentinel
    from agentcop.adapters.autogen import AutoGenSentinelAdapter

    adapter = AutoGenSentinelAdapter(run_id="run-001")

    user_proxy = UserProxyAgent("user_proxy", ...)
    assistant  = AssistantAgent("assistant", ...)

    chat_result = user_proxy.initiate_chat(assistant, message="Hello")

    sentinel = Sentinel()
    sentinel.ingest(adapter.iter_messages(chat_result.chat_history))
    violations = sentinel.detect_violations()
    sentinel.report()

``to_sentinel_event(raw)`` accepts a plain dict for manual translation::

    event = adapter.to_sentinel_event({
        "type": "function_call_error",
        "sender": "AssistantAgent",
        "function_name": "search",
        "error": "connection refused",
    })
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional

from agentcop.event import SentinelEvent


def _require_autogen() -> None:
    try:
        import autogen  # noqa: F401
    except ImportError:
        try:
            import autogen_agentchat  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "AutoGen adapter requires 'pyautogen' or 'autogen-agentchat'. "
                "Install it with: pip install agentcop[autogen]"
            ) from exc


class AutoGenSentinelAdapter:
    """
    Adapter that translates AutoGen conversation events into SentinelEvents.

    **Normalized dict schema** (what ``to_sentinel_event`` accepts):

    All dicts must have a ``"type"`` key matching one of the supported event
    type strings. Additional keys are type-specific (see table below).

    +------------------------------+-------------------------------+-----------+
    | type                         | event_type (SentinelEvent)    | severity  |
    +==============================+===============================+===========+
    | conversation_started         | conversation_started          | INFO      |
    | conversation_completed       | conversation_completed        | INFO      |
    | conversation_error           | conversation_error            | ERROR     |
    | message_sent                 | message_sent                  | INFO      |
    | message_filtered             | message_filtered              | WARN      |
    | function_call_started        | function_call_started         | INFO      |
    | function_call_completed      | function_call_completed       | INFO      |
    | function_call_error          | function_call_error           | ERROR     |
    | agent_reply_started          | agent_reply_started           | INFO      |
    | agent_reply_completed        | agent_reply_completed         | INFO      |
    | agent_reply_error            | agent_reply_error             | ERROR     |
    | (anything else)              | unknown_autogen_event         | INFO      |
    +------------------------------+-------------------------------+-----------+

    Parameters
    ----------
    run_id:
        Optional run / session identifier used as ``trace_id`` on every
        translated event. Correlates all events from one conversation run.
    """

    source_system = "autogen"

    def __init__(self, run_id: Optional[str] = None) -> None:
        _require_autogen()
        self._run_id = run_id
        self._buffer: List[SentinelEvent] = []
        self._lock = threading.Lock()

    def to_sentinel_event(self, raw: Dict[str, Any]) -> SentinelEvent:
        """
        Translate one AutoGen event dict into a SentinelEvent.

        ``raw`` must contain a ``"type"`` key. All other keys are
        type-specific. Unknown types are translated to
        ``unknown_autogen_event`` with severity INFO.
        """
        dispatch = {
            "conversation_started":    self._from_conversation_started,
            "conversation_completed":  self._from_conversation_completed,
            "conversation_error":      self._from_conversation_error,
            "message_sent":            self._from_message_sent,
            "message_filtered":        self._from_message_filtered,
            "function_call_started":   self._from_function_call_started,
            "function_call_completed": self._from_function_call_completed,
            "function_call_error":     self._from_function_call_error,
            "agent_reply_started":     self._from_agent_reply_started,
            "agent_reply_completed":   self._from_agent_reply_completed,
            "agent_reply_error":       self._from_agent_reply_error,
        }
        handler = dispatch.get(raw.get("type", ""), self._from_unknown)
        return handler(raw)

    def iter_messages(
        self, messages: Iterable[Dict[str, Any]]
    ) -> Iterator[SentinelEvent]:
        """
        Translate an AutoGen chat history into a sequence of SentinelEvents.

        Accepts both AutoGen 0.2.x message dicts (identified by a ``"role"``
        key) and AutoGen 0.4.x message dicts (identified by a ``"type"`` key
        with known AgentChat type strings). Both formats are detected
        automatically.

        Typical usage::

            # AutoGen 0.2.x
            chat_result = user_proxy.initiate_chat(assistant, message="...")
            sentinel.ingest(adapter.iter_messages(chat_result.chat_history))

            # AutoGen 0.4.x
            result = await team.run(task="...")
            sentinel.ingest(adapter.iter_messages(result.messages))

        Parameters
        ----------
        messages:
            Iterable of message dicts from ``ChatResult.chat_history`` (0.2.x)
            or ``TaskResult.messages`` (0.4.x).
        """
        for msg in messages:
            yield self._translate_native_message(msg)

    def drain(self) -> List[SentinelEvent]:
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
    # Native message translation
    # ------------------------------------------------------------------

    # AutoGen 0.4.x type strings that trigger v4 translation
    _V4_TYPES = frozenset({
        "TextMessage",
        "ToolCallRequestEvent",
        "ToolCallExecutionEvent",
        "ToolCallSummaryMessage",
        "StopMessage",
        "HandoffMessage",
    })

    def _translate_native_message(self, msg: Dict[str, Any]) -> SentinelEvent:
        """
        Detect native AutoGen message format and route to the correct
        translator.

        * AutoGen 0.4.x messages have a ``"type"`` key with an AgentChat
          type string (e.g. ``"TextMessage"``).
        * AutoGen 0.2.x messages have a ``"role"`` key
          (``"user"``, ``"assistant"``, ``"function"``, ``"tool"``).
        * Dicts that already use the normalized ``"type"`` format are passed
          directly to ``to_sentinel_event()``.
        """
        if "type" in msg and msg["type"] in self._V4_TYPES:
            return self._translate_v4_message(msg)
        if "role" in msg:
            return self._translate_v2_message(msg)
        return self.to_sentinel_event(msg)

    def _translate_v2_message(self, msg: Dict[str, Any]) -> SentinelEvent:
        """Translate an AutoGen 0.2.x native message dict."""
        role = msg.get("role", "unknown")
        name = msg.get("name") or role
        timestamp = msg.get("timestamp")

        # role=function: a function result returned by the executor
        if role == "function":
            content = str(msg.get("content", ""))
            func_name = msg.get("name", "unknown")
            if _looks_like_error(content):
                return self.to_sentinel_event({
                    "type": "function_call_error",
                    "function_name": func_name,
                    "error": content[:500],
                    "sender": "unknown",
                    "timestamp": timestamp,
                })
            return self.to_sentinel_event({
                "type": "function_call_completed",
                "function_name": func_name,
                "result": content[:500],
                "timestamp": timestamp,
            })

        # role=tool: OpenAI-style tool result
        if role == "tool":
            content = str(msg.get("content", ""))
            return self.to_sentinel_event({
                "type": "function_call_completed",
                "function_name": msg.get("name", "unknown"),
                "tool_call_id": msg.get("tool_call_id", ""),
                "result": content[:500],
                "timestamp": timestamp,
            })

        # Function call request (legacy function_call dict)
        if msg.get("function_call"):
            fc = msg["function_call"]
            return self.to_sentinel_event({
                "type": "function_call_started",
                "sender": name,
                "function_name": fc.get("name", "unknown"),
                "arguments": fc.get("arguments", ""),
                "timestamp": timestamp,
            })

        # Tool call request (OpenAI tool_calls list)
        if msg.get("tool_calls"):
            first = msg["tool_calls"][0]
            func = first.get("function", {})
            return self.to_sentinel_event({
                "type": "function_call_started",
                "sender": name,
                "function_name": func.get("name", "unknown"),
                "arguments": func.get("arguments", ""),
                "tool_call_id": first.get("id", ""),
                "timestamp": timestamp,
            })

        # Regular text message
        content = msg.get("content") or ""
        return self.to_sentinel_event({
            "type": "message_sent",
            "sender": name,
            "role": role,
            "content": str(content)[:500],
            "timestamp": timestamp,
        })

    def _translate_v4_message(self, msg: Dict[str, Any]) -> SentinelEvent:
        """Translate an AutoGen 0.4.x (autogen-agentchat) native message dict."""
        msg_type = msg.get("type", "")
        source = msg.get("source", "unknown")
        timestamp = msg.get("created_at") or msg.get("timestamp")

        if msg_type == "TextMessage":
            return self.to_sentinel_event({
                "type": "message_sent",
                "sender": source,
                "content": str(msg.get("content", ""))[:500],
                "timestamp": timestamp,
            })

        if msg_type == "ToolCallRequestEvent":
            calls = msg.get("content") or []
            first = calls[0] if calls else {}
            return self.to_sentinel_event({
                "type": "function_call_started",
                "sender": source,
                "function_name": first.get("name", "unknown"),
                "arguments": first.get("arguments", ""),
                "tool_call_id": first.get("id", ""),
                "timestamp": timestamp,
            })

        if msg_type == "ToolCallExecutionEvent":
            results = msg.get("content") or []
            first = results[0] if results else {}
            content = str(first.get("content", ""))
            if _looks_like_error(content):
                return self.to_sentinel_event({
                    "type": "function_call_error",
                    "function_name": "unknown",
                    "tool_call_id": first.get("call_id", ""),
                    "error": content[:500],
                    "sender": source,
                    "timestamp": timestamp,
                })
            return self.to_sentinel_event({
                "type": "function_call_completed",
                "function_name": "unknown",
                "tool_call_id": first.get("call_id", ""),
                "result": content[:500],
                "timestamp": timestamp,
            })

        if msg_type in ("StopMessage", "ToolCallSummaryMessage"):
            return self.to_sentinel_event({
                "type": "conversation_completed",
                "content": str(msg.get("content", ""))[:500],
                "initiator": source,
                "timestamp": timestamp,
            })

        if msg_type == "HandoffMessage":
            return self.to_sentinel_event({
                "type": "message_sent",
                "sender": source,
                "content": f"[handoff] {str(msg.get('content', ''))[:400]}",
                "timestamp": timestamp,
            })

        # Unknown 0.4.x type — original_type will be msg_type via _from_unknown
        return self.to_sentinel_event({
            "type": msg_type,
            "timestamp": timestamp,
        })

    # ------------------------------------------------------------------
    # Timestamp helper
    # ------------------------------------------------------------------

    def _parse_timestamp(self, raw: Dict[str, Any]) -> datetime:
        ts = raw.get("timestamp")
        if ts:
            try:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Private translators
    # ------------------------------------------------------------------

    def _from_conversation_started(self, raw: Dict[str, Any]) -> SentinelEvent:
        initiator = raw.get("initiator", "unknown")
        recipient = raw.get("recipient", "unknown")
        return SentinelEvent(
            event_id=f"autogen-conv-{uuid.uuid4()}",
            event_type="conversation_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"conversation started: {initiator} → {recipient}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"initiator": initiator, "recipient": recipient},
        )

    def _from_conversation_completed(self, raw: Dict[str, Any]) -> SentinelEvent:
        initiator = raw.get("initiator", "unknown")
        message_count = raw.get("message_count", 0)
        content = raw.get("content", "")
        attrs: Dict[str, Any] = {
            "initiator": initiator,
            "message_count": message_count,
        }
        if content:
            attrs["content"] = content
        return SentinelEvent(
            event_id=f"autogen-conv-{uuid.uuid4()}",
            event_type="conversation_completed",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"conversation completed after {message_count} message(s)",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes=attrs,
        )

    def _from_conversation_error(self, raw: Dict[str, Any]) -> SentinelEvent:
        initiator = raw.get("initiator", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"autogen-conv-{uuid.uuid4()}",
            event_type="conversation_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"conversation error: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"initiator": initiator, "error": error},
        )

    def _from_message_sent(self, raw: Dict[str, Any]) -> SentinelEvent:
        sender = raw.get("sender", "unknown")
        content = raw.get("content", "")
        role = raw.get("role", "")
        attrs: Dict[str, Any] = {"sender": sender, "content": content}
        if role:
            attrs["role"] = role
        return SentinelEvent(
            event_id=f"autogen-msg-{uuid.uuid4()}",
            event_type="message_sent",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"[{sender}] {content[:120]}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes=attrs,
        )

    def _from_message_filtered(self, raw: Dict[str, Any]) -> SentinelEvent:
        sender = raw.get("sender", "unknown")
        reason = raw.get("reason", "")
        content = raw.get("content", "")
        return SentinelEvent(
            event_id=f"autogen-msg-{uuid.uuid4()}",
            event_type="message_filtered",
            timestamp=self._parse_timestamp(raw),
            severity="WARN",
            body=f"message from '{sender}' filtered: {reason}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"sender": sender, "reason": reason, "content": content},
        )

    def _from_function_call_started(self, raw: Dict[str, Any]) -> SentinelEvent:
        sender = raw.get("sender", "unknown")
        function_name = raw.get("function_name", "unknown")
        arguments = raw.get("arguments", "")
        tool_call_id = raw.get("tool_call_id", "")
        attrs: Dict[str, Any] = {
            "sender": sender,
            "function_name": function_name,
            "arguments": arguments,
        }
        if tool_call_id:
            attrs["tool_call_id"] = tool_call_id
        return SentinelEvent(
            event_id=f"autogen-func-{uuid.uuid4()}",
            event_type="function_call_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"[{sender}] calling function '{function_name}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes=attrs,
        )

    def _from_function_call_completed(self, raw: Dict[str, Any]) -> SentinelEvent:
        function_name = raw.get("function_name", "unknown")
        result = raw.get("result", "")
        tool_call_id = raw.get("tool_call_id", "")
        attrs: Dict[str, Any] = {"function_name": function_name, "result": result}
        if tool_call_id:
            attrs["tool_call_id"] = tool_call_id
        return SentinelEvent(
            event_id=f"autogen-func-{uuid.uuid4()}",
            event_type="function_call_completed",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"function '{function_name}' completed",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes=attrs,
        )

    def _from_function_call_error(self, raw: Dict[str, Any]) -> SentinelEvent:
        sender = raw.get("sender", "unknown")
        function_name = raw.get("function_name", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"autogen-func-{uuid.uuid4()}",
            event_type="function_call_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"function '{function_name}' error: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"sender": sender, "function_name": function_name, "error": error},
        )

    def _from_agent_reply_started(self, raw: Dict[str, Any]) -> SentinelEvent:
        agent = raw.get("agent", "unknown")
        return SentinelEvent(
            event_id=f"autogen-agent-{uuid.uuid4()}",
            event_type="agent_reply_started",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"agent '{agent}' started generating reply",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"agent": agent},
        )

    def _from_agent_reply_completed(self, raw: Dict[str, Any]) -> SentinelEvent:
        agent = raw.get("agent", "unknown")
        content = raw.get("content", "")
        return SentinelEvent(
            event_id=f"autogen-agent-{uuid.uuid4()}",
            event_type="agent_reply_completed",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"agent '{agent}' reply completed",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"agent": agent, "content": content},
        )

    def _from_agent_reply_error(self, raw: Dict[str, Any]) -> SentinelEvent:
        agent = raw.get("agent", "unknown")
        error = raw.get("error", "")
        return SentinelEvent(
            event_id=f"autogen-agent-{uuid.uuid4()}",
            event_type="agent_reply_error",
            timestamp=self._parse_timestamp(raw),
            severity="ERROR",
            body=f"agent '{agent}' reply error: {error}",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"agent": agent, "error": error},
        )

    def _from_unknown(self, raw: Dict[str, Any]) -> SentinelEvent:
        original_type = raw.get("type", "unknown")
        return SentinelEvent(
            event_id=f"autogen-unknown-{uuid.uuid4()}",
            event_type="unknown_autogen_event",
            timestamp=self._parse_timestamp(raw),
            severity="INFO",
            body=f"unknown AutoGen event type '{original_type}'",
            source_system=self.source_system,
            trace_id=self._run_id,
            attributes={"original_type": original_type},
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_ERROR_SIGNALS = frozenset({"error", "exception", "traceback", "failed", "failure"})


def _looks_like_error(content: str) -> bool:
    """Heuristic: does this function/tool result look like an error message?"""
    lower = content.lower()
    return any(sig in lower for sig in _ERROR_SIGNALS)
