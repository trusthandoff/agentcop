# Sentinel — The Agent Cop

**The cop for agent fleets.**

Every agent fleet needs a cop. Agents delegate, handoff, and execute — and without forensic oversight, violations are invisible until they're incidents. `agentcop` is a universal auditor: ingest events from any agent system, run violation detectors, get structured findings.

OTel-aligned schema. Pluggable detectors. Adapter bridge to your stack. Zero required infrastructure.

```
pip install agentcop
```

---

## How it works

```
your agent system
      │
      ▼
 SentinelAdapter          ← translate domain events to universal schema
      │
      ▼
  Sentinel.ingest()       ← load SentinelEvents into the auditor
      │
      ▼
  detect_violations()     ← run detectors, get ViolationRecords
      │
      ▼
  report() / your sink    ← stdout, OTel, alerting, whatever
```

---

## Quickstart

```python
from agentcop import Sentinel, SentinelEvent

sentinel = Sentinel()

# Feed it events (any source, any schema — adapt first)
sentinel.ingest([
    SentinelEvent(
        event_id="evt-001",
        event_type="packet_rejected",
        timestamp="2026-03-31T12:00:00Z",
        severity="ERROR",
        body="packet rejected — TTL expired",
        source_system="my-agent",
        attributes={"packet_id": "pkt-abc", "reason": "ttl_expired"},
    )
])

violations = sentinel.detect_violations()
# [ViolationRecord(violation_type='rejected_packet', severity='ERROR', ...)]

sentinel.report()
# [ERROR] rejected_packet — packet rejected — TTL expired
#   packet_id: pkt-abc
#   reason: ttl_expired
```

Built-in detectors fire on four event types out of the box:

| `event_type`            | Detector                      | Severity |
|-------------------------|-------------------------------|----------|
| `packet_rejected`       | `detect_rejected_packet`      | ERROR    |
| `capability_stale`      | `detect_stale_capability`     | ERROR    |
| `token_overlap_used`    | `detect_overlap_window`       | WARN     |
| `ai_generated_payload`  | `detect_ai_generated_payload` | WARN     |

---

## Custom detectors

Detectors are plain functions. Register as many as you need.

```python
from agentcop import Sentinel, SentinelEvent, ViolationRecord
from typing import Optional

def detect_unauthorized_tool(event: SentinelEvent) -> Optional[ViolationRecord]:
    if event.event_type != "tool_call":
        return None
    if event.attributes.get("tool") in {"shell", "fs_write"}:
        return ViolationRecord(
            violation_type="unauthorized_tool",
            severity="CRITICAL",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={"tool": event.attributes["tool"]},
        )

sentinel = Sentinel()
sentinel.register_detector(detect_unauthorized_tool)
```

---

## TrustHandoff adapter

[TrustHandoff](https://github.com/trusthandoff/trusthandoff) ships a first-class adapter. If you're using `trusthandoff` for cryptographic delegation, plug it in directly:

```python
from trusthandoff.sentinel_adapter import TrustHandoffSentinelAdapter
from agentcop import Sentinel

adapter = TrustHandoffSentinelAdapter()
sentinel = Sentinel()

# raw_events: list of dicts from trusthandoff's forensic log
sentinel.ingest(adapter.to_sentinel_event(e) for e in raw_events)

violations = sentinel.detect_violations()
sentinel.report()
```

The adapter maps trusthandoff's event fields — `packet_id`, `correlation_id`, `reason`, `event_type` — to the universal `SentinelEvent` schema. Severity is inferred from event type. Everything else lands in `attributes`.

---

## Write your own adapter

Implement the `SentinelAdapter` protocol to bridge any system:

```python
from agentcop import SentinelAdapter, SentinelEvent
from typing import Dict, Any

class MySystemAdapter:
    source_system = "my-system"

    def to_sentinel_event(self, raw: Dict[str, Any]) -> SentinelEvent:
        return SentinelEvent(
            event_id=raw["id"],
            event_type=raw["type"],
            timestamp=raw["ts"],
            severity=raw.get("level", "INFO"),
            body=raw.get("message", ""),
            source_system=self.source_system,
            trace_id=raw.get("trace_id"),
            attributes=raw.get("metadata", {}),
        )
```

---

## LangGraph integration

Plug into any LangGraph graph with zero changes to your graph code. The adapter reads the debug event stream — node starts, node results, checkpoint saves — and translates each into a `SentinelEvent` for violation detection.

```
pip install agentcop[langgraph]
```

Stream a graph in `debug` mode and pipe every event through the adapter:

```python
from agentcop import Sentinel
from agentcop.adapters.langgraph import LangGraphSentinelAdapter

adapter = LangGraphSentinelAdapter(thread_id="run-abc")
sentinel = Sentinel()

sentinel.ingest(
    adapter.iter_events(
        graph.stream({"input": "..."}, config, stream_mode="debug")
    )
)

violations = sentinel.detect_violations()
sentinel.report()
```

Three LangGraph debug event types are translated:

| LangGraph event  | SentinelEvent type        | Severity |
|------------------|---------------------------|----------|
| `task`           | `node_start`              | INFO     |
| `task_result`    | `node_end`                | INFO     |
| `task_result`    | `node_error` (if errored) | ERROR    |
| `checkpoint`     | `checkpoint_saved`        | INFO     |

Each event carries structured `attributes` — `node`, `task_id`, `step`, `triggers`, `checkpoint_id`, `next` — so you can write targeted violation detectors:

```python
from agentcop import ViolationRecord

def detect_node_failure(event):
    if event.event_type == "node_error":
        return ViolationRecord(
            violation_type="node_execution_failed",
            severity="ERROR",
            source_event_id=event.event_id,
            trace_id=event.trace_id,
            detail={
                "node": event.attributes["node"],
                "error": event.attributes["error"],
            },
        )

sentinel = Sentinel(detectors=[detect_node_failure])
```

The `thread_id` passed to `LangGraphSentinelAdapter` is used as `trace_id` on every event, correlating all events from a single graph run.

---

## OpenTelemetry export *(optional)*

`agentcop` events use an OTel-aligned schema out of the box (`trace_id`, `span_id`, severity levels). To export events as OTel log records:

```
pip install agentcop[otel]
```

```python
from agentcop.otel import OtelSentinelExporter
from opentelemetry.sdk._logs import LoggerProvider

exporter = OtelSentinelExporter(logger_provider=LoggerProvider())
exporter.export(events)
```

Attributes are emitted under the `sentinel.*` namespace. `trace_id` and `span_id` are mapped to OTel trace context.

---

## Requirements

- Python 3.11+
- `pydantic>=2.7`

---

## License

MIT
