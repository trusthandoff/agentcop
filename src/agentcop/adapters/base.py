"""
SentinelAdapter protocol.

Implement this to bridge any system's raw event dicts into SentinelEvents.
The TrustHandoff adapter lives in trusthandoff.sentinel_adapter.
"""

from typing import Any, Protocol, runtime_checkable

from agentcop.event import SentinelEvent


@runtime_checkable
class SentinelAdapter(Protocol):
    """
    Protocol for system-specific event adapters.

    Implementors translate raw event dicts (or any domain object) into
    the universal SentinelEvent schema.

    Example::

        class MySystemAdapter:
            source_system = "my-system"

            def to_sentinel_event(self, raw: dict) -> SentinelEvent:
                return SentinelEvent(
                    event_id=raw["id"],
                    event_type=raw["type"],
                    ...
                )
    """

    source_system: str

    def to_sentinel_event(self, raw: dict[str, Any]) -> SentinelEvent: ...
