"""Tests for agentcop.trust.boundaries — ToolTrustBoundary."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from agentcop.trust.boundaries import BoundaryResult, ToolTrustBoundary


class TestBoundaryResultDataclass:
    def test_fields(self):
        r = BoundaryResult(allowed=True, reason="ok", from_tool="A", to_tool="B")
        assert r.allowed is True
        assert r.reason == "ok"
        assert r.from_tool == "A"
        assert r.to_tool == "B"


class TestToolTrustBoundaryBasic:
    def test_no_boundary_declared_allows(self):
        tb = ToolTrustBoundary()
        result = tb.check("tool_a", "tool_b")
        assert result.allowed is True
        assert "no boundary" in result.reason

    def test_declared_allowed_boundary(self):
        tb = ToolTrustBoundary()
        tb.declare_boundary("tool_a", "tool_b", allowed=True, reason="explicitly allowed")
        result = tb.check("tool_a", "tool_b")
        assert result.allowed is True
        assert result.reason == "explicitly allowed"

    def test_declared_denied_boundary(self):
        tb = ToolTrustBoundary()
        tb.declare_boundary("tool_a", "tool_b", allowed=False, reason="cross-tenant denied")
        result = tb.check("tool_a", "tool_b")
        assert result.allowed is False
        assert result.reason == "cross-tenant denied"

    def test_from_and_to_preserved_in_result(self):
        tb = ToolTrustBoundary()
        result = tb.check("X", "Y")
        assert result.from_tool == "X"
        assert result.to_tool == "Y"

    def test_unknown_boundary_uses_allow(self):
        tb = ToolTrustBoundary()
        tb.declare_boundary("A", "B", allowed=False, reason="deny")
        result = tb.check("A", "C")  # different pair
        assert result.allowed is True

    def test_override_boundary(self):
        tb = ToolTrustBoundary()
        tb.declare_boundary("A", "B", allowed=True, reason="first")
        tb.declare_boundary("A", "B", allowed=False, reason="second")
        result = tb.check("A", "B")
        assert result.allowed is False
        assert result.reason == "second"

    def test_multiple_boundaries_coexist(self):
        tb = ToolTrustBoundary()
        tb.declare_boundary("A", "B", allowed=True, reason="ok")
        tb.declare_boundary("C", "D", allowed=False, reason="denied")
        assert tb.check("A", "B").allowed is True
        assert tb.check("C", "D").allowed is False

    def test_directions_independent(self):
        tb = ToolTrustBoundary()
        tb.declare_boundary("A", "B", allowed=True, reason="forward ok")
        tb.declare_boundary("B", "A", allowed=False, reason="reverse denied")
        assert tb.check("A", "B").allowed is True
        assert tb.check("B", "A").allowed is False

    def test_check_with_context_hash(self):
        tb = ToolTrustBoundary()
        tb.declare_boundary("X", "Y", allowed=True, reason="ok")
        result = tb.check("X", "Y", context_hash="abc123")
        assert result.allowed is True


class TestToolTrustBoundarySentinelEvent:
    def test_denied_fires_sentinel_event(self):
        sentinel = MagicMock()
        tb = ToolTrustBoundary(sentinel=sentinel)
        tb.declare_boundary("A", "B", allowed=False, reason="not allowed")
        tb.check("A", "B")
        sentinel.push.assert_called_once()

    def test_allowed_does_not_fire_sentinel_event(self):
        sentinel = MagicMock()
        tb = ToolTrustBoundary(sentinel=sentinel)
        tb.declare_boundary("A", "B", allowed=True, reason="ok")
        tb.check("A", "B")
        sentinel.push.assert_not_called()

    def test_no_sentinel_no_error_on_violation(self):
        tb = ToolTrustBoundary(sentinel=None)
        tb.declare_boundary("A", "B", allowed=False, reason="denied")
        result = tb.check("A", "B")  # should not raise
        assert result.allowed is False

    def test_sentinel_event_contains_tool_names(self):
        sentinel = MagicMock()
        tb = ToolTrustBoundary(sentinel=sentinel)
        tb.declare_boundary("tool_x", "tool_y", allowed=False, reason="blocked")
        tb.check("tool_x", "tool_y")
        event = sentinel.push.call_args[0][0]
        assert "tool_x" in event.body or "tool_x" in str(event.attributes)

    def test_sentinel_event_severity_is_error(self):
        sentinel = MagicMock()
        tb = ToolTrustBoundary(sentinel=sentinel)
        tb.declare_boundary("A", "B", allowed=False, reason="r")
        tb.check("A", "B")
        event = sentinel.push.call_args[0][0]
        assert event.severity == "ERROR"

    def test_sentinel_push_failure_does_not_raise(self):
        sentinel = MagicMock()
        sentinel.push.side_effect = RuntimeError("redis down")
        tb = ToolTrustBoundary(sentinel=sentinel)
        tb.declare_boundary("A", "B", allowed=False, reason="denied")
        result = tb.check("A", "B")  # should not propagate the error
        assert result.allowed is False


class TestToolTrustBoundaryThreadSafety:
    def test_concurrent_declare_and_check(self):
        tb = ToolTrustBoundary()
        errors: list[Exception] = []

        def writer(i: int) -> None:
            try:
                tb.declare_boundary(f"A{i}", f"B{i}", allowed=(i % 2 == 0), reason=f"r{i}")
            except Exception as exc:
                errors.append(exc)

        def reader(i: int) -> None:
            try:
                tb.check(f"A{i}", f"B{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)] + [
            threading.Thread(target=reader, args=(i,)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
