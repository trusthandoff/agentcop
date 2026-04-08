# AutoGen adapter

Plug agentcop into any AutoGen workflow with two lines of setup. The adapter
translates AutoGen conversation messages and function-call events into
`SentinelEvent` objects, which you can then run through a `Sentinel` instance
for violation detection.

Supports **AutoGen 0.2.x** (`pyautogen`) via `iter_messages()` over a completed
chat history, and **AutoGen 0.4.x** (`autogen-agentchat`) via the same API on
`TaskResult.messages`. Both message formats are auto-detected.

---

## Installation

```bash
pip install agentcop[autogen]
```

---

## How it works

```
AutoGen 0.2.x
  user_proxy.initiate_chat(assistant, ...)
      │
      │  ChatResult.chat_history  (list of message dicts)
      │
      ▼
AutoGen 0.4.x
  await team.run(task=...)
      │
      │  TaskResult.messages  (list of message dicts)
      │
      ▼
adapter.iter_messages(messages)
      │
      ▼
SentinelEvent stream
      │
      ▼
sentinel.detect_violations() → ViolationRecord list
```

The adapter translates 11 event types across four categories:

| Category     | type                   | `event_type`            | `severity` |
|--------------|------------------------|-------------------------|------------|
| Conversation | `conversation_started` | `conversation_started`  | INFO       |
| Conversation | `conversation_completed` | `conversation_completed` | INFO     |
| Conversation | `conversation_error`   | `conversation_error`    | ERROR      |
| Message      | `message_sent`         | `message_sent`          | INFO       |
| Message      | `message_filtered`     | `message_filtered`      | WARN       |
| Function     | `function_call_started` | `function_call_started` | INFO      |
| Function     | `function_call_completed` | `function_call_completed` | INFO   |
| Function     | `function_call_error`  | `function_call_error`   | ERROR      |
| Agent        | `agent_reply_started`  | `agent_reply_started`   | INFO       |
| Agent        | `agent_reply_completed` | `agent_reply_completed` | INFO      |
| Agent        | `agent_reply_error`    | `agent_reply_error`     | ERROR      |

---

## Quickstart (AutoGen 0.2.x)

```python
from autogen import AssistantAgent, UserProxyAgent
from agentcop import Sentinel
from agentcop.adapters.autogen import AutoGenSentinelAdapter

# --- Your agents (unchanged) ---

assistant = AssistantAgent(
    name="assistant",
    llm_config={"model": "gpt-4o"},
)

user_proxy = UserProxyAgent(
    name="user_proxy",
    human_input_mode="NEVER",
    max_consecutive_auto_reply=5,
)

# --- Audit layer ---

adapter = AutoGenSentinelAdapter(run_id="run-001")

chat_result = user_proxy.initiate_chat(
    assistant,
    message="Write a Python function that checks if a number is prime.",
)

sentinel = Sentinel()
sentinel.ingest(adapter.iter_messages(chat_result.chat_history))
violations = sentinel.detect_violations()
sentinel.report()
```

No changes to agents or prompts required.

---

## Quickstart (AutoGen 0.4.x)

```python
import asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_ext.models import OpenAIChatCompletionClient
from agentcop import Sentinel
from agentcop.adapters.autogen import AutoGenSentinelAdapter

async def main():
    model = OpenAIChatCompletionClient(model="gpt-4o")
    agent = AssistantAgent("assistant", model_client=model)
    team  = RoundRobinGroupChat([agent], max_turns=5)

    adapter = AutoGenSentinelAdapter(run_id="run-001")

    result = await team.run(task="Summarize the latest AI safety research.")

    sentinel = Sentinel()
    sentinel.ingest(adapter.iter_messages(result.messages))
    violations = sentinel.detect_violations()
    sentinel.report()

asyncio.run(main())
```

---

## Writing detectors for AutoGen events

### Detect a function call that failed

```python
from typing import Optional
from agentcop import SentinelEvent, ViolationRecord

def detect_function_failure(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "function_call_error":
        return None
    return ViolationRecord(
        violation_type="function_call_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "function": event.attributes["function_name"],
            "error": event.attributes["error"],
            "sender": event.attributes["sender"],
        },
    )
```

### Detect a conversation that errored out

```python
def detect_conversation_error(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "conversation_error":
        return None
    return ViolationRecord(
        violation_type="conversation_failed",
        severity="ERROR",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "initiator": event.attributes["initiator"],
            "error": event.attributes["error"],
        },
    )
```

### Detect a filtered message (policy violation)

```python
def detect_filtered_message(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "message_filtered":
        return None
    return ViolationRecord(
        violation_type="message_policy_violation",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "sender": event.attributes["sender"],
            "reason": event.attributes["reason"],
        },
    )
```

### Detect a restricted function being called

```python
RESTRICTED_FUNCTIONS = {"exec_shell", "write_file", "delete_file"}

def detect_restricted_function_call(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "function_call_started":
        return None
    fn = event.attributes.get("function_name", "")
    if fn not in RESTRICTED_FUNCTIONS:
        return None
    return ViolationRecord(
        violation_type="restricted_function_called",
        severity="CRITICAL",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "function": fn,
            "sender": event.attributes.get("sender"),
            "arguments": event.attributes.get("arguments"),
        },
    )
```

### Detect a rate-limited tool call

```python
RATE_LIMIT_SIGNALS = {"429", "rate limit", "quota exceeded", "too many requests"}

def detect_rate_limit(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "function_call_error":
        return None
    error = event.attributes.get("error", "").lower()
    if not any(sig in error for sig in RATE_LIMIT_SIGNALS):
        return None
    return ViolationRecord(
        violation_type="function_rate_limited",
        severity="WARN",
        source_event_id=event.event_id,
        trace_id=event.trace_id,
        detail={
            "function": event.attributes["function_name"],
            "error": event.attributes["error"],
        },
    )
```

### Register detectors

```python
sentinel = Sentinel(detectors=[
    detect_function_failure,
    detect_conversation_error,
    detect_filtered_message,
    detect_restricted_function_call,
    detect_rate_limit,
])
sentinel.ingest(adapter.iter_messages(chat_result.chat_history))
violations = sentinel.detect_violations()
```

---

## `run_id` and trace correlation

Pass a `run_id` to correlate all events from one conversation run:

```python
import uuid
run_id = str(uuid.uuid4())
adapter = AutoGenSentinelAdapter(run_id=run_id)
```

Every `SentinelEvent` produced during the run will carry `trace_id=run_id`.
When you inspect a `ViolationRecord`, `violation.trace_id` traces back to the
same run.

---

## Translating events manually

`to_sentinel_event(raw)` accepts a plain dict and is useful for offline
processing, replaying logged events, or testing detectors without running a
real conversation:

```python
event = adapter.to_sentinel_event({
    "type": "function_call_error",
    "sender": "AssistantAgent",
    "function_name": "web_search",
    "error": "429 rate limit exceeded",
    "timestamp": "2026-04-01T12:00:00Z",
})
```

Required key: `"type"`. All other keys are optional — missing values default
to `"unknown"` or empty string.

---

## Multi-agent group chats

For `GroupChat` workflows, use one adapter and accumulate all messages:

```python
import autogen

groupchat = autogen.GroupChat(agents=[agent_a, agent_b, agent_c], messages=[])
manager  = autogen.GroupChatManager(groupchat=groupchat)

adapter = AutoGenSentinelAdapter(run_id="group-run-001")
chat_result = user_proxy.initiate_chat(manager, message="Design a REST API.")

sentinel = Sentinel(detectors=[
    detect_function_failure,
    detect_restricted_function_call,
])
sentinel.ingest(adapter.iter_messages(chat_result.chat_history))
violations = sentinel.detect_violations()

if violations:
    for v in violations:
        print(f"[{v.severity}] {v.violation_type}: {v.detail}")
```

---

## Push-based buffering

For scenarios where you want to buffer events incrementally (e.g. wrapping
agents with reply hooks), use the push-based API:

```python
adapter = AutoGenSentinelAdapter(run_id="run-001")

# Manually record events as they happen:
adapter._buffer_event(adapter.to_sentinel_event({
    "type": "function_call_started",
    "sender": "assistant",
    "function_name": "search",
    "arguments": '{"q": "AI safety"}',
}))

# After execution, flush into Sentinel:
sentinel = Sentinel(detectors=[detect_function_failure])
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```

`drain()` returns and clears the buffer without ingesting:

```python
events = adapter.drain()          # returns List[SentinelEvent], clears buffer
sentinel.ingest(events)           # ingest manually
```

---

## Assertion-style auditing in CI

Raise on violations to hard-fail a pipeline:

```python
adapter = AutoGenSentinelAdapter(run_id="ci-run")
chat_result = user_proxy.initiate_chat(assistant, message="...")

sentinel = Sentinel(detectors=[
    detect_function_failure,
    detect_conversation_error,
    detect_restricted_function_call,
])
sentinel.ingest(adapter.iter_messages(chat_result.chat_history))
violations = sentinel.detect_violations()

if violations:
    for v in violations:
        print(f"[{v.severity}] {v.violation_type}: {v.detail}")
    raise RuntimeError(f"Agent run failed audit — {len(violations)} violation(s)")
```

---

## Differences from other adapters

|                   | LangGraph              | CrewAI                  | AutoGen                    |
|-------------------|------------------------|-------------------------|----------------------------|
| Event delivery    | Pull (debug stream)    | Push (event bus)        | Pull (chat history)        |
| Primary API       | `iter_events(stream)`  | `setup()` + `flush_into()` | `iter_messages(history)` |
| Correlation ID    | LangGraph task UUID    | `run_id` you provide    | `run_id` you provide       |
| Native formats    | `stream_mode="debug"`  | CrewAI event objects    | 0.2.x dicts, 0.4.x dicts  |
| Auto-detect format | No (one format)       | No (event bus objects)  | Yes (0.2.x vs 0.4.x)       |

---

## Attributes reference

### Conversation events (`conversation_*`)

| Key             | Type   | Present in           | Description                        |
|-----------------|--------|----------------------|------------------------------------|
| `initiator`     | `str`  | all conversation events | Agent or proxy that started the chat |
| `recipient`     | `str`  | `started` only       | Target agent                       |
| `message_count` | `int`  | `completed` only     | Number of messages exchanged       |
| `content`       | `str`  | `completed` only (if set) | Final output or stop reason   |
| `error`         | `str`  | `error` only         | Error message                      |

### Message events (`message_*`)

| Key       | Type  | Present in           | Description                      |
|-----------|-------|----------------------|----------------------------------|
| `sender`  | `str` | all message events   | Agent name or role               |
| `content` | `str` | all message events   | Message text (≤500 chars)        |
| `role`    | `str` | `sent` only (if set) | AutoGen role (user/assistant)    |
| `reason`  | `str` | `filtered` only      | Why the message was blocked      |

### Function call events (`function_call_*`)

| Key            | Type  | Present in             | Description                        |
|----------------|-------|------------------------|------------------------------------|
| `sender`       | `str` | `started`, `error`     | Agent that issued the call         |
| `function_name`| `str` | all function events    | Name of the function/tool          |
| `arguments`    | `str` | `started` only         | JSON-encoded argument string       |
| `tool_call_id` | `str` | `started`, `completed` (if set) | OpenAI tool call ID       |
| `result`       | `str` | `completed` only       | Function return value (≤500 chars) |
| `error`        | `str` | `error` only           | Error message                      |

### Agent reply events (`agent_reply_*`)

| Key       | Type  | Present in           | Description                     |
|-----------|-------|----------------------|---------------------------------|
| `agent`   | `str` | all agent events     | Agent name                      |
| `content` | `str` | `completed` only     | Reply content (≤500 chars)      |
| `error`   | `str` | `error` only         | Error message                   |

---

## API reference

### `AutoGenSentinelAdapter(run_id=None)`

**Parameters**

- `run_id` (`str | None`) — Optional run identifier used as `trace_id` on
  every translated event. Recommended: pass a UUID per conversation run.

**Methods**

- `iter_messages(messages: Iterable[dict]) -> Iterator[SentinelEvent]` —
  Translate an AutoGen chat history into SentinelEvents. Auto-detects AutoGen
  0.2.x (`"role"` key) and 0.4.x (`"type"` key with AgentChat type strings).

- `to_sentinel_event(raw: dict) -> SentinelEvent` — Translate one normalized
  event dict. Dispatches on `raw["type"]`. Unknown types map to
  `unknown_autogen_event`. Never raises; missing keys fall back to safe defaults.

- `drain() -> list[SentinelEvent]` — Return and clear all buffered events.
  Thread-safe.

- `flush_into(sentinel: Sentinel) -> None` — Call `sentinel.ingest(self.drain())`.
  Ingest all buffered events and clear the buffer in one step.

- `_buffer_event(event: SentinelEvent) -> None` — Append one event to the
  internal thread-safe buffer.

**Class attribute**

- `source_system = "autogen"` — appears on every translated `SentinelEvent`.

---

## Runtime security

`AutoGenSentinelAdapter` supports the full agentcop runtime security stack via four optional
constructor parameters. All default to `None` — existing code requires no changes.

### Constructor params

```python
AutoGenSentinelAdapter(
    run_id="run-001",
    gate=None,        # ExecutionGate
    permissions=None, # ToolPermissionLayer
    sandbox=None,     # AgentSandbox
    approvals=None,   # ApprovalBoundary
    identity=None,    # AgentIdentity
)
```

### What gets intercepted

The gate fires inside `_from_function_call_started()` — the method that translates both
AutoGen 0.2.x `function_call` messages and 0.4.x `ToolCallRequestEvent` messages — before
the `SentinelEvent` is returned.  The sender name is used as `agent_id` for the permission
layer.  If denied, `PermissionError` is raised and a security SentinelEvent is buffered.

### Example

```python
from agentcop.adapters.autogen import AutoGenSentinelAdapter
from agentcop.gate import ExecutionGate, ConditionalPolicy
from agentcop.permissions import ToolPermissionLayer, WritePermission
from agentcop.approvals import ApprovalBoundary

gate = ExecutionGate()
gate.register_policy("file_write", ConditionalPolicy(
    allow_if=lambda args: args.get("path", "").startswith("/tmp/"),
    deny_reason="writes outside /tmp are blocked",
))

permissions = ToolPermissionLayer()
permissions.declare("AssistantAgent", [WritePermission(paths=["/tmp/*"])])

approvals = ApprovalBoundary(requires_approval_above=80)

adapter = AutoGenSentinelAdapter(
    run_id="run-001",
    gate=gate,
    permissions=permissions,
    approvals=approvals,
)

chat_result = user_proxy.initiate_chat(assistant, message="Write a report to /etc/cron.d/")

sentinel = Sentinel()
sentinel.ingest(adapter.iter_messages(chat_result.chat_history))
adapter.flush_into(sentinel)
violations = sentinel.detect_violations()
```

---

## Reliability Tracking

Track message-chain stability, tool retry counts, and path consistency across
AutoGen conversations. Use `AutoGenReliabilityWrapper` or instrument individual
function maps with `ReliabilityTracer`.

```python
from agentcop import ReliabilityStore
from agentcop.reliability.adapters import AutoGenReliabilityWrapper

store = ReliabilityStore("agentcop.db")
wrapper = AutoGenReliabilityWrapper(agent_id="autogen-agent", store=store)

# Wrap the function map so tool calls are tracked automatically
wrapped_fn_map = wrapper.wrap_function_map(assistant.function_map)
assistant.function_map = wrapped_fn_map

# Use track_conversation() as context manager for the full turn
with wrapper.track_conversation():
    user_proxy.initiate_chat(assistant, message="Summarise the quarterly report.")

# After several conversations
report = store.get_report("autogen-agent", window_hours=24)
print(report.reliability_tier)   # STABLE | VARIABLE | UNSTABLE | CRITICAL
```

Or wrap the adapter directly:

```python
from agentcop import wrap_for_reliability
from agentcop.adapters.autogen import AutoGenSentinelAdapter

adapter = AutoGenSentinelAdapter(run_id="run-001")
wrapped = wrap_for_reliability(adapter, agent_id="autogen-agent", store=store)
```

See [docs/guides/reliability.md](../guides/reliability.md) for the full guide.
