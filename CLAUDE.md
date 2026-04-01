# CLAUDE.md — agentcop

Working context for Claude Code. Read this before touching any file.

---

## Architecture map

```
src/agentcop/
├── __init__.py          ← public API re-exports only; never imports optional submodules
├── event.py             ← SentinelEvent, ViolationRecord — pure Pydantic models, no logic
├── violations.py        ← four built-in detector functions + DEFAULT_DETECTORS list
├── sentinel.py          ← Sentinel class — orchestrator, owns the threading.Lock
├── otel.py              ← optional OTel integration; _require_otel() guard in __init__
└── adapters/
    ├── __init__.py      ← re-exports SentinelAdapter only
    ├── base.py          ← SentinelAdapter @runtime_checkable Protocol
    └── langgraph.py     ← optional LangGraph adapter; _require_langgraph() guard in __init__

tests/
├── test_event.py             ← SentinelEvent + ViolationRecord schema tests
├── test_violations.py        ← four built-in detectors + DEFAULT_DETECTORS
├── test_sentinel.py          ← Sentinel ingest/detect/report + thread safety
├── test_adapter.py           ← SentinelAdapter protocol conformance
└── test_langgraph_adapter.py ← LangGraphSentinelAdapter (mocks langgraph import guard)
```

**Dependency graph** (no cycles):

```
pydantic
  └── event.py
        ├── violations.py
        ├── sentinel.py  ← also imports violations.py
        ├── adapters/base.py
        ├── adapters/langgraph.py  (+ optional: langgraph)
        └── otel.py                (+ optional: opentelemetry-sdk)
```

`__init__.py` imports from all core modules. It must never import from `otel` or
`adapters/langgraph` — those are opt-in submodules that users import directly.

---

## Security invariants

These must never be broken. Treat them like load-bearing walls.

**1. Detectors are pure functions.**
A `ViolationDetector` is `(SentinelEvent) -> ViolationRecord | None`. No I/O, no
network calls, no mutable state, no side effects. This is what makes detection safe
to run from multiple threads and reproducible across runs.

**2. `Sentinel._lock` guards all mutations.**
`_events` and `_detectors` are only ever read or written while holding `self._lock`.
`detect_violations()` snapshots both under the lock, then runs detectors outside it
— this is intentional and correct. Never move detector execution back inside the lock.

**3. Optional dependencies are never imported at module top level.**
`otel.py` and `adapters/langgraph.py` each have a `_require_*()` guard that is
called inside `__init__`. The module-level import of `from agentcop.event import
SentinelEvent` is fine because `event.py` has no optional deps. If you add a new
optional integration, follow the same pattern exactly.

**4. `ViolationRecord.severity` cannot be INFO.**
Enforced by `Literal["WARN", "ERROR", "CRITICAL"]` in the Pydantic model. A
violation with INFO severity is a contradiction. Do not change this constraint.

**5. `SentinelEvent` and `ViolationRecord` are immutable after construction.**
They are Pydantic `BaseModel` instances. Treat them as value objects. Detectors
must not mutate the event they receive.

**6. `__init__.py` is the public API surface.**
Anything not re-exported from `__init__.py` is private. Adding a name to `__all__`
is a public commitment. Be deliberate — removals are breaking changes.

---

## Fragile files

Files where a careless edit causes hard-to-debug downstream failures:

**`src/agentcop/event.py`**
Field names and types are the serialization contract. Renaming `event_id`,
`event_type`, `trace_id`, or any other field silently breaks every adapter,
every detector, and every downstream system consuming ViolationRecords.
The `severity` Literals on both models are also load-bearing — test validation
before changing them.

**`src/agentcop/violations.py`**
The `violation_type` strings (`"rejected_packet"`, `"stale_capability"`,
`"overlap_window_used"`, `"ai_generated_payload"`) are part of the public API.
Downstream systems match on these strings. Rename them only with a major version bump.

**`src/agentcop/sentinel.py` — the lock pattern**
`ingest()` builds the list outside the lock, then assigns under it. `detect_violations()`
snapshots under the lock, then iterates outside it. This two-phase pattern is
deliberate: it keeps the lock duration minimal and prevents deadlock if a detector
ever called back into the Sentinel. Do not move logic in or out of `with self._lock`.

**`src/agentcop/adapters/langgraph.py` — event ID prefixes**
`lg-task-{id}`, `lg-result-{id}`, `lg-checkpoint-{id}` are the stable event ID
schemes. Changing them breaks any downstream system that joins task and task_result
events by correlating their IDs. The `{id}` part comes directly from LangGraph's
own task ID — this is intentional so `lg-task-X` and `lg-result-X` share the
same suffix and are trivially joinable.

---

## Test commands

```bash
# Full suite (run this before every commit)
.venv/bin/pytest tests/ -v

# Single file
.venv/bin/pytest tests/test_event.py -v
.venv/bin/pytest tests/test_violations.py -v
.venv/bin/pytest tests/test_sentinel.py -v
.venv/bin/pytest tests/test_adapter.py -v
.venv/bin/pytest tests/test_langgraph_adapter.py -v

# Thread safety only
.venv/bin/pytest tests/test_sentinel.py::TestThreadSafety -v

# Quick smoke check (no output noise)
.venv/bin/pytest tests/ -q
```

Tests for optional adapters (LangGraph, OTel) must mock their import guard so
the test suite runs without the optional package installed. Pattern:

```python
with patch("agentcop.adapters.langgraph._require_langgraph"):
    from agentcop.adapters.langgraph import LangGraphSentinelAdapter
    adapter = LangGraphSentinelAdapter()
```

---

## Workflow rules

**Version bumps touch two files.**
`pyproject.toml` (`version = "..."`) and `src/agentcop/__init__.py`
(`__version__ = "..."`). Both must be updated together. The publish workflow
triggers on version tags (`v*`), so always push the tag after the commit.

**New adapters follow the established pattern.**
1. Create `src/agentcop/adapters/<name>.py`.
2. Add a `_require_<name>()` guard, called in `__init__`.
3. Set `source_system = "<name>"` as a class attribute.
4. Implement `to_sentinel_event(self, raw: dict) -> SentinelEvent`.
5. Do not import from `adapters/__init__.py` or `agentcop/__init__.py` —
   users import the adapter directly from its submodule path.
6. Add `<name> = ["<package>>=<version>"]` to `[project.optional-dependencies]`.
7. Write tests that mock the import guard.

**New detectors follow the established pattern.**
1. Add a `detect_<name>(event: SentinelEvent) -> Optional[ViolationRecord]` function
   to `violations.py`.
2. Append it to `DEFAULT_DETECTORS`.
3. Re-export it from `__init__.py` and add it to `__all__`.
4. The `violation_type` string in the returned `ViolationRecord` is public API.

**Do not add imports of optional submodules to `__init__.py`.**
`from .otel import ...` or `from .adapters.langgraph import ...` at the top of
`__init__.py` would break `import agentcop` for users who haven't installed the
optional dependency. Optional modules are always imported by the user directly.

**Commit message format.**
Follow the existing log: `type: short description` where type is one of
`feat`, `fix`, `test`, `docs`, `refactor`, `chore`. Keep the subject under 72
characters. Co-author line required:
`Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
