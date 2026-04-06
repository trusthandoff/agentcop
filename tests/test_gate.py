"""Tests for agentcop.gate — ExecutionGate and policy types."""

from __future__ import annotations

import time

import pytest

from agentcop.gate import (
    AllowPolicy,
    ConditionalPolicy,
    DenyPolicy,
    ExecutionGate,
    GateDecision,
    RateLimitPolicy,
)


# ---------------------------------------------------------------------------
# GateDecision
# ---------------------------------------------------------------------------


class TestGateDecision:
    def test_fields(self):
        d = GateDecision(allowed=True, reason="ok", risk_score=10)
        assert d.allowed is True
        assert d.reason == "ok"
        assert d.risk_score == 10

    def test_frozen(self):
        d = GateDecision(allowed=False, reason="no", risk_score=50)
        with pytest.raises((AttributeError, TypeError)):
            d.allowed = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AllowPolicy
# ---------------------------------------------------------------------------


class TestAllowPolicy:
    def test_always_allows(self):
        policy = AllowPolicy()
        decision = policy.check("any_tool", {"x": 1}, {})
        assert decision.allowed is True
        assert decision.risk_score == 0


# ---------------------------------------------------------------------------
# DenyPolicy
# ---------------------------------------------------------------------------


class TestDenyPolicy:
    def test_always_denies(self):
        policy = DenyPolicy("blocked")
        decision = policy.check("any_tool", {}, {})
        assert decision.allowed is False
        assert decision.risk_score == 100

    def test_custom_reason(self):
        policy = DenyPolicy("my custom reason")
        assert policy.check("t", {}, {}).reason == "my custom reason"

    def test_default_reason(self):
        policy = DenyPolicy()
        assert "deny" in policy.check("t", {}, {}).reason.lower()


# ---------------------------------------------------------------------------
# ConditionalPolicy
# ---------------------------------------------------------------------------


class TestConditionalPolicy:
    def test_allows_when_condition_met(self):
        policy = ConditionalPolicy(allow_if=lambda args: args.get("safe") is True)
        d = policy.check("tool", {"safe": True}, {})
        assert d.allowed is True
        assert d.risk_score == 0

    def test_denies_when_condition_not_met(self):
        policy = ConditionalPolicy(
            allow_if=lambda args: args.get("safe") is True,
            deny_reason="not safe",
            risk_score_if_denied=75,
        )
        d = policy.check("tool", {"safe": False}, {})
        assert d.allowed is False
        assert d.reason == "not safe"
        assert d.risk_score == 75

    def test_path_prefix_example(self):
        policy = ConditionalPolicy(
            allow_if=lambda args: args.get("path", "").startswith("/tmp/"),
            deny_reason="file_write outside /tmp/ blocked",
        )
        assert policy.check("file_write", {"path": "/tmp/foo.txt"}, {}).allowed is True
        assert policy.check("file_write", {"path": "/etc/passwd"}, {}).allowed is False


# ---------------------------------------------------------------------------
# RateLimitPolicy
# ---------------------------------------------------------------------------


class TestRateLimitPolicy:
    def test_allows_within_limit(self):
        policy = RateLimitPolicy(max_calls=3, window_seconds=60.0)
        for _ in range(3):
            assert policy.check("t", {}, {}).allowed is True

    def test_denies_when_limit_exceeded(self):
        policy = RateLimitPolicy(max_calls=2, window_seconds=60.0)
        policy.check("t", {}, {})
        policy.check("t", {}, {})
        d = policy.check("t", {}, {})
        assert d.allowed is False
        assert d.risk_score == 60

    def test_window_resets(self):
        policy = RateLimitPolicy(max_calls=1, window_seconds=0.05)
        assert policy.check("t", {}, {}).allowed is True
        assert policy.check("t", {}, {}).allowed is False
        time.sleep(0.1)
        assert policy.check("t", {}, {}).allowed is True


# ---------------------------------------------------------------------------
# ExecutionGate
# ---------------------------------------------------------------------------


@pytest.fixture
def gate():
    g = ExecutionGate(db_path=":memory:")
    yield g
    g.close()


class TestExecutionGate:
    def test_no_policy_allows(self, gate):
        d = gate.check("unknown_tool", {"x": 1})
        assert d.allowed is True
        assert d.reason == "no policy registered"

    def test_allow_policy(self, gate):
        gate.register_policy("read", AllowPolicy())
        assert gate.check("read", {}).allowed is True

    def test_deny_policy(self, gate):
        gate.register_policy("exec", DenyPolicy("exec blocked"))
        d = gate.check("exec", {})
        assert d.allowed is False
        assert d.reason == "exec blocked"

    def test_conditional_policy(self, gate):
        gate.register_policy(
            "file_write",
            ConditionalPolicy(
                allow_if=lambda args: args.get("path", "").startswith("/tmp/"),
                deny_reason="file_write outside /tmp/ blocked",
            ),
        )
        assert gate.check("file_write", {"path": "/tmp/x"}).allowed is True
        assert gate.check("file_write", {"path": "/etc/hosts"}).allowed is False

    def test_register_policy_replaces(self, gate):
        gate.register_policy("t", AllowPolicy())
        gate.register_policy("t", DenyPolicy("new"))
        assert gate.check("t", {}).allowed is False

    def test_decisions_logged(self, gate):
        gate.register_policy("tool", DenyPolicy("no"))
        gate.check("tool", {"a": 1})
        gate.check("tool", {"b": 2})
        log = gate.decision_log()
        assert len(log) >= 2
        assert log[0]["tool"] == "tool"
        assert log[0]["allowed"] is False

    def test_decision_log_limit(self, gate):
        for i in range(10):
            gate.check(f"tool_{i}", {})
        log = gate.decision_log(limit=3)
        assert len(log) == 3

    def test_decision_log_newest_first(self, gate):
        gate.check("first", {})
        gate.check("second", {})
        log = gate.decision_log()
        assert log[0]["tool"] == "second"

    def test_decision_log_has_risk_score(self, gate):
        gate.register_policy("t", DenyPolicy("x"))
        gate.check("t", {})
        log = gate.decision_log(limit=1)
        assert "risk_score" in log[0]
        assert log[0]["risk_score"] == 100

    def test_wrap_allows(self, gate):
        gate.register_policy("add", AllowPolicy())

        @gate.wrap
        def add(a, b):
            return a + b

        assert add(1, 2) == 3

    def test_wrap_denies_raises_permission_error(self, gate):
        gate.register_policy("rm", DenyPolicy("denied"))

        @gate.wrap
        def rm(path):
            pass

        with pytest.raises(PermissionError, match="ExecutionGate denied 'rm'"):
            rm("/etc")

    def test_wrap_conditional_with_kwargs(self, gate):
        gate.register_policy(
            "file_write",
            ConditionalPolicy(
                allow_if=lambda args: args.get("path", "").startswith("/tmp/"),
                deny_reason="outside /tmp/",
            ),
        )

        @gate.wrap
        def file_write(path, content):
            return f"written:{path}"

        assert file_write(path="/tmp/ok.txt", content="hi") == "written:/tmp/ok.txt"
        with pytest.raises(PermissionError):
            file_write(path="/etc/evil", content="bad")

    def test_wrap_logs_decision(self, gate):
        gate.register_policy("fn", AllowPolicy())

        @gate.wrap
        def fn(x):
            return x

        fn(42)
        log = gate.decision_log(limit=1)
        assert log[0]["tool"] == "fn"
        assert log[0]["allowed"] is True

    def test_context_parameter_ignored_for_no_policy(self, gate):
        d = gate.check("t", {}, context={"user": "alice"})
        assert d.allowed is True

    def test_thread_safety(self, gate):
        import threading

        gate.register_policy("concurrent", AllowPolicy())
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(20):
                    gate.check("concurrent", {"n": 1})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(gate.decision_log(limit=200)) == 100
