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

    ``source_system`` **must** be a non-empty string.  Use
    :func:`validate_adapter` to assert this at runtime.

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


def validate_adapter(adapter: Any) -> None:
    """Assert that *adapter* is a valid :class:`SentinelAdapter`.

    Raises:
        TypeError: if *adapter* does not implement the :class:`SentinelAdapter` protocol.
        ValueError: if ``adapter.source_system`` is not a non-empty string.
    """
    if not isinstance(adapter, SentinelAdapter):
        raise TypeError(
            f"{adapter!r} does not implement the SentinelAdapter protocol "
            "(must have a 'source_system' attribute and a 'to_sentinel_event' method)"
        )
    if not isinstance(adapter.source_system, str) or not adapter.source_system.strip():
        raise ValueError(
            f"SentinelAdapter.source_system must be a non-empty string, "
            f"got {adapter.source_system!r}"
        )
