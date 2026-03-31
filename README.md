# sentinel-core

**The cop for agent fleets.**

Every agent fleet needs a cop. Agents delegate, handoff, and execute — and without forensic oversight, violations are invisible until they're incidents. `sentinel-core` is a universal auditor: ingest events from any agent system, run violation detectors, get structured findings.

OTel-aligned schema. Pluggable detectors. Adapter bridge to your stack. Zero required infrastructure.

```
pip install sentinel-core
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
from sentinel_core import Sentinel, SentinelEvent

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
from sentinel_core import Sentinel, SentinelEvent, ViolationRecord
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
from sentinel_core import Sentinel

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
from sentinel_core import SentinelAdapter, SentinelEvent
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

## LangGraph integration *(coming soon)*

Native LangGraph adapter that hooks into graph execution events — node calls, edge transitions, tool invocations — and surfaces violations without any manual instrumentation.

```python
# coming soon
from sentinel_core.adapters.langgraph import LangGraphSentinelAdapter
```

---

## OpenTelemetry export *(optional)*

`sentinel-core` events use an OTel-aligned schema out of the box (`trace_id`, `span_id`, severity levels). To export events as OTel log records:

```
pip install sentinel-core[otel]
```

```python
from sentinel_core.otel import OtelSentinelExporter
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
