"""Tests for agentcop.permissions — ToolPermissionLayer and capability permission types."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from agentcop.permissions import (
    ExecutePermission,
    NetworkPermission,
    Permission,
    PermissionResult,
    ReadPermission,
    ToolPermissionLayer,
    WritePermission,
)

# ---------------------------------------------------------------------------
# PermissionResult
# ---------------------------------------------------------------------------


class TestPermissionResult:
    def test_granted_fields(self):
        r = PermissionResult(granted=True, reason="ok")
        assert r.granted is True
        assert r.reason == "ok"

    def test_denied_fields(self):
        r = PermissionResult(granted=False, reason="no")
        assert r.granted is False

    def test_frozen(self):
        r = PermissionResult(granted=True, reason="ok")
        with pytest.raises((AttributeError, TypeError)):
            r.granted = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Permission base class
# ---------------------------------------------------------------------------


class TestPermissionBase:
    def test_covers_default_tools_empty(self):
        p = Permission()
        assert p.covers("anything") is False

    def test_covers_custom_tool_names(self):
        p = Permission(tool_names=["my_tool"])
        assert p.covers("my_tool") is True
        assert p.covers("other") is False

    def test_check_scope_base_always_passes(self):
        p = Permission()
        ok, _ = p.check_scope({"anything": 1})
        assert ok is True


# ---------------------------------------------------------------------------
# ReadPermission
# ---------------------------------------------------------------------------


class TestReadPermission:
    def test_covers_default_tools(self):
        perm = ReadPermission(paths=["/data/*"])
        for tool in (
            "file_read",
            "read_file",
            "open_file",
            "read",
            "get_file",
            "load_file",
            "cat",
        ):
            assert perm.covers(tool) is True, f"should cover {tool!r}"

    def test_covers_custom_tool_names(self):
        perm = ReadPermission(paths=["/tmp/*"], tool_names=["my_reader"])
        assert perm.covers("my_reader") is True
        assert perm.covers("file_read") is False  # replaced, not extended

    def test_path_within_glob_allowed(self):
        perm = ReadPermission(paths=["/data/*", "/logs/*.log"])
        ok, reason = perm.check_scope({"path": "/data/file.csv"})
        assert ok is True
        assert "/data/*" in reason

    def test_path_outside_glob_denied(self):
        perm = ReadPermission(paths=["/data/*"])
        ok, reason = perm.check_scope({"path": "/etc/passwd"})
        assert ok is False
        assert "/etc/passwd" in reason

    def test_no_path_arg_passes(self):
        perm = ReadPermission(paths=["/data/*"])
        ok, _ = perm.check_scope({})
        assert ok is True

    def test_empty_paths_allows_any(self):
        perm = ReadPermission(paths=[])
        ok, _ = perm.check_scope({"path": "/etc/shadow"})
        assert ok is True

    def test_file_path_key(self):
        perm = ReadPermission(paths=["/tmp/*"])
        ok, _ = perm.check_scope({"file_path": "/tmp/x.txt"})
        assert ok is True

    def test_filename_key(self):
        perm = ReadPermission(paths=["/tmp/*"])
        ok, _ = perm.check_scope({"filename": "/tmp/report.csv"})
        assert ok is True


# ---------------------------------------------------------------------------
# WritePermission
# ---------------------------------------------------------------------------


class TestWritePermission:
    def test_covers_default_tools(self):
        perm = WritePermission(paths=["/tmp/*"])
        for tool in ("file_write", "write_file", "save_file", "create_file", "write", "put_file"):
            assert perm.covers(tool) is True, f"should cover {tool!r}"

    def test_path_glob_allowed(self):
        perm = WritePermission(paths=["/tmp/*", "/output/*"])
        assert perm.check_scope({"path": "/tmp/result.json"})[0] is True
        assert perm.check_scope({"path": "/output/report.csv"})[0] is True

    def test_path_outside_denied(self):
        perm = WritePermission(paths=["/tmp/*"])
        ok, reason = perm.check_scope({"path": "/etc/cron.d/evil"})
        assert ok is False

    def test_deeply_nested_path(self):
        perm = WritePermission(paths=["/tmp/*"])
        # Python fnmatch '*' matches across '/' — /tmp/* covers /tmp/a/b/c.txt
        ok, _ = perm.check_scope({"path": "/tmp/a/b/c.txt"})
        assert ok is True

    def test_glob_double_star_workaround(self):
        perm = WritePermission(paths=["/tmp/a/b/c.txt"])
        ok, _ = perm.check_scope({"path": "/tmp/a/b/c.txt"})
        assert ok is True

    def test_no_scope_restriction(self):
        perm = WritePermission(paths=[])
        ok, _ = perm.check_scope({"path": "/anywhere/file.txt"})
        assert ok is True


# ---------------------------------------------------------------------------
# NetworkPermission
# ---------------------------------------------------------------------------


class TestNetworkPermission:
    def test_covers_default_tools(self):
        perm = NetworkPermission(domains=["example.com"])
        for tool in ("http_get", "http_post", "http_request", "fetch", "request", "api_call"):
            assert perm.covers(tool) is True, f"should cover {tool!r}"

    def test_exact_domain_allowed(self):
        perm = NetworkPermission(domains=["api.openai.com", "agentcop.live"])
        ok, _ = perm.check_scope({"url": "https://api.openai.com/v1/chat"})
        assert ok is True

    def test_domain_not_in_list_denied(self):
        perm = NetworkPermission(domains=["api.openai.com"])
        ok, reason = perm.check_scope({"url": "https://evil.com/steal"})
        assert ok is False
        assert "evil.com" in reason

    def test_bare_domain_arg(self):
        perm = NetworkPermission(domains=["agentcop.live"])
        ok, _ = perm.check_scope({"domain": "agentcop.live"})
        assert ok is True

    def test_host_arg(self):
        perm = NetworkPermission(domains=["internal.svc"])
        ok, _ = perm.check_scope({"host": "internal.svc"})
        assert ok is True

    def test_subdomain_denied_by_default(self):
        perm = NetworkPermission(domains=["openai.com"])
        ok, _ = perm.check_scope({"url": "https://api.openai.com/"})
        assert ok is False  # subdomain not allowed unless allow_subdomains=True

    def test_subdomain_allowed_with_flag(self):
        perm = NetworkPermission(domains=["openai.com"], allow_subdomains=True)
        ok, _ = perm.check_scope({"url": "https://api.openai.com/"})
        assert ok is True

    def test_empty_domains_allows_any(self):
        perm = NetworkPermission(domains=[])
        ok, _ = perm.check_scope({"url": "https://anywhere.com"})
        assert ok is True

    def test_no_url_arg_passes(self):
        perm = NetworkPermission(domains=["safe.com"])
        ok, _ = perm.check_scope({})
        assert ok is True

    def test_url_with_port_in_host_arg(self):
        perm = NetworkPermission(domains=["localhost"])
        ok, _ = perm.check_scope({"host": "localhost:8080"})
        assert ok is True


# ---------------------------------------------------------------------------
# ExecutePermission
# ---------------------------------------------------------------------------


class TestExecutePermission:
    def test_covers_default_tools(self):
        perm = ExecutePermission(commands=["ls"])
        for tool in ("shell_exec", "run_command", "execute", "bash", "exec", "run"):
            assert perm.covers(tool) is True, f"should cover {tool!r}"

    def test_allowed_command(self):
        perm = ExecutePermission(commands=["ls", "cat", "echo"])
        ok, _ = perm.check_scope({"command": "ls -la /tmp"})
        assert ok is True

    def test_denied_command(self):
        perm = ExecutePermission(commands=["ls"])
        ok, reason = perm.check_scope({"command": "rm -rf /"})
        assert ok is False
        assert "rm" in reason

    def test_cmd_key(self):
        perm = ExecutePermission(commands=["echo"])
        ok, _ = perm.check_scope({"cmd": "echo hello"})
        assert ok is True

    def test_empty_commands_allows_any(self):
        perm = ExecutePermission(commands=[])
        ok, _ = perm.check_scope({"command": "rm -rf /"})
        assert ok is True

    def test_no_command_arg_passes(self):
        perm = ExecutePermission(commands=["ls"])
        ok, _ = perm.check_scope({})
        assert ok is True

    def test_exact_command_string_match(self):
        perm = ExecutePermission(commands=["git status"])
        ok, _ = perm.check_scope({"command": "git status"})
        assert ok is True

    def test_leading_token_extracted(self):
        perm = ExecutePermission(commands=["git"])
        ok, _ = perm.check_scope({"command": "git commit -m 'x'"})
        assert ok is True


# ---------------------------------------------------------------------------
# ToolPermissionLayer — declare / verify
# ---------------------------------------------------------------------------


class TestToolPermissionLayerDeclare:
    def test_undeclared_agent_denied(self):
        layer = ToolPermissionLayer()
        result = layer.verify("ghost", "file_read", {})
        assert result.granted is False
        assert "ghost" in result.reason

    def test_declare_and_verify_allowed(self):
        layer = ToolPermissionLayer()
        layer.declare("agent-1", [ReadPermission(paths=["/data/*"])])
        result = layer.verify("agent-1", "file_read", {"path": "/data/x.csv"})
        assert result.granted is True

    def test_declare_replaces_previous(self):
        layer = ToolPermissionLayer()
        layer.declare("agent-1", [WritePermission(paths=["/anywhere/*"])])
        layer.declare("agent-1", [WritePermission(paths=["/tmp/*"])])
        # Old /anywhere/* no longer valid
        result = layer.verify("agent-1", "file_write", {"path": "/anywhere/x"})
        assert result.granted is False

    def test_multiple_permissions(self):
        layer = ToolPermissionLayer()
        layer.declare(
            "agent-1",
            [
                WritePermission(paths=["/tmp/*"]),
                NetworkPermission(domains=["api.openai.com"]),
            ],
        )
        assert layer.verify("agent-1", "file_write", {"path": "/tmp/x"}).granted is True
        assert (
            layer.verify("agent-1", "http_post", {"url": "https://api.openai.com/"}).granted
            is True
        )

    def test_tool_not_in_any_permission_denied(self):
        layer = ToolPermissionLayer()
        layer.declare("agent-1", [ReadPermission(paths=["/data/*"])])
        result = layer.verify("agent-1", "shell_exec", {"command": "ls"})
        assert result.granted is False
        assert "shell_exec" in result.reason


class TestToolPermissionLayerScope:
    def test_deny_by_default_path_outside_scope(self):
        layer = ToolPermissionLayer()
        layer.declare("agent-1", [WritePermission(paths=["/tmp/*"])])
        result = layer.verify("agent-1", "file_write", {"path": "/etc/passwd"})
        assert result.granted is False

    def test_first_passing_permission_wins(self):
        """If first permission fails scope but second passes, call is allowed."""
        layer = ToolPermissionLayer()
        layer.declare(
            "agent-1",
            [
                WritePermission(paths=["/logs/*"], tool_names=["write"]),
                WritePermission(paths=["/tmp/*"], tool_names=["write"]),
            ],
        )
        result = layer.verify("agent-1", "write", {"path": "/tmp/x"})
        assert result.granted is True

    def test_no_args_passes_when_no_scope_restriction(self):
        layer = ToolPermissionLayer()
        layer.declare("agent-1", [ExecutePermission(commands=[])])
        result = layer.verify("agent-1", "bash", {})
        assert result.granted is True

    def test_network_domain_scope(self):
        layer = ToolPermissionLayer()
        layer.declare("agent-1", [NetworkPermission(domains=["api.openai.com", "agentcop.live"])])
        assert (
            layer.verify("agent-1", "http_get", {"url": "https://api.openai.com/v1"}).granted
            is True
        )
        assert layer.verify("agent-1", "http_get", {"url": "https://evil.com"}).granted is False


# ---------------------------------------------------------------------------
# ToolPermissionLayer — PermissionViolation SentinelEvent
# ---------------------------------------------------------------------------


class TestPermissionViolationEvent:
    def _make_sentinel(self):
        """Return a mock Sentinel that records push() calls."""
        sentinel = MagicMock()
        return sentinel

    def test_violation_event_not_fired_on_success(self):
        sentinel = self._make_sentinel()
        layer = ToolPermissionLayer(sentinel=sentinel)
        layer.declare("agent-1", [ReadPermission(paths=["/data/*"])])
        layer.verify("agent-1", "file_read", {"path": "/data/x.csv"})
        sentinel.push.assert_not_called()

    def test_violation_event_fired_on_undeclared_agent(self):
        sentinel = self._make_sentinel()
        layer = ToolPermissionLayer(sentinel=sentinel)
        layer.verify("ghost", "file_read", {})
        sentinel.push.assert_called_once()
        event = sentinel.push.call_args[0][0]
        assert event.event_type == "permission_violation"
        assert event.severity == "ERROR"
        assert event.source_system == "agentcop.permissions"
        assert "ghost" in event.body

    def test_violation_event_fired_on_undeclared_tool(self):
        sentinel = self._make_sentinel()
        layer = ToolPermissionLayer(sentinel=sentinel)
        layer.declare("agent-1", [ReadPermission(paths=["*"])])
        layer.verify("agent-1", "shell_exec", {})
        sentinel.push.assert_called_once()
        event = sentinel.push.call_args[0][0]
        assert "shell_exec" in event.body or "shell_exec" in str(event.attributes)

    def test_violation_event_fired_on_scope_failure(self):
        sentinel = self._make_sentinel()
        layer = ToolPermissionLayer(sentinel=sentinel)
        layer.declare("agent-1", [WritePermission(paths=["/tmp/*"])])
        layer.verify("agent-1", "file_write", {"path": "/etc/evil"})
        sentinel.push.assert_called_once()
        event = sentinel.push.call_args[0][0]
        assert event.event_type == "permission_violation"
        assert event.attributes["agent_id"] == "agent-1"
        assert event.attributes["tool_name"] == "file_write"

    def test_violation_event_not_fired_without_sentinel(self):
        """No error when no sentinel attached — fire_violation is a no-op."""
        layer = ToolPermissionLayer(sentinel=None)
        result = layer.verify("ghost", "anything", {})  # must not raise
        assert result.granted is False

    def test_violation_event_has_unique_event_ids(self):
        sentinel = self._make_sentinel()
        layer = ToolPermissionLayer(sentinel=sentinel)
        for _ in range(3):
            layer.verify("ghost", "tool", {})
        ids = [c[0][0].event_id for c in sentinel.push.call_args_list]
        assert len(set(ids)) == 3

    def test_violation_event_uses_real_sentinel(self):
        """Integration: push to a real Sentinel and detect violations."""
        from agentcop import Sentinel, SentinelEvent
        from agentcop.event import ViolationRecord as VR

        captured: list[SentinelEvent] = []

        sentinel = Sentinel()

        # Register a detector that captures the permission_violation event
        def _capture(event: SentinelEvent) -> VR | None:
            if event.event_type == "permission_violation":
                captured.append(event)
            return None

        sentinel.register_detector(_capture)
        layer = ToolPermissionLayer(sentinel=sentinel)
        layer.verify("agent-x", "bad_tool", {})
        sentinel.detect_violations()
        assert len(captured) == 1
        assert captured[0].event_type == "permission_violation"


# ---------------------------------------------------------------------------
# ToolPermissionLayer — ExecutionGate integration
# ---------------------------------------------------------------------------


class TestGateIntegration:
    def test_as_gate_policy_allows(self):
        from agentcop.gate import ExecutionGate

        layer = ToolPermissionLayer()
        layer.declare("agent-1", [WritePermission(paths=["/tmp/*"])])
        gate = ExecutionGate(db_path=":memory:")
        gate.register_policy("file_write", layer.as_gate_policy("agent-1", "file_write"))
        d = gate.check("file_write", {"path": "/tmp/x"})
        assert d.allowed is True
        gate.close()

    def test_as_gate_policy_denies_scope_violation(self):
        from agentcop.gate import ExecutionGate

        layer = ToolPermissionLayer()
        layer.declare("agent-1", [WritePermission(paths=["/tmp/*"])])
        gate = ExecutionGate(db_path=":memory:")
        gate.register_policy("file_write", layer.as_gate_policy("agent-1", "file_write"))
        d = gate.check("file_write", {"path": "/etc/passwd"})
        assert d.allowed is False
        gate.close()

    def test_attach_to_gate_registers_all_declared_tools(self):
        from agentcop.gate import ExecutionGate

        layer = ToolPermissionLayer()
        layer.declare(
            "agent-1",
            [
                WritePermission(paths=["/tmp/*"]),
                NetworkPermission(domains=["safe.com"]),
            ],
        )
        gate = ExecutionGate(db_path=":memory:")
        layer.attach_to_gate(gate, "agent-1")

        assert gate.check("file_write", {"path": "/tmp/ok"}).allowed is True
        assert gate.check("file_write", {"path": "/etc/evil"}).allowed is False
        assert gate.check("http_get", {"url": "https://safe.com"}).allowed is True
        assert gate.check("http_get", {"url": "https://evil.com"}).allowed is False
        gate.close()

    def test_attach_to_gate_undeclared_agent_no_tools_registered(self):
        from agentcop.gate import ExecutionGate

        layer = ToolPermissionLayer()
        gate = ExecutionGate(db_path=":memory:")
        layer.attach_to_gate(gate, "unknown-agent")  # must not raise
        # No policies registered — gate default is allow with "no policy"
        d = gate.check("anything", {})
        assert d.reason == "no policy registered"
        gate.close()

    def test_gate_wrap_with_permission_layer(self):
        from agentcop.gate import ExecutionGate

        layer = ToolPermissionLayer()
        layer.declare("agent-1", [ExecutePermission(commands=["echo"])])
        gate = ExecutionGate(db_path=":memory:")
        layer.attach_to_gate(gate, "agent-1")

        @gate.wrap
        def run(command):
            return f"ran:{command}"

        assert run(command="echo hello") == "ran:echo hello"
        with pytest.raises(PermissionError):
            run(command="rm -rf /")
        gate.close()

    def test_violation_events_fired_through_gate(self):
        """PermissionViolation events are emitted even when the gate is the caller."""
        from agentcop.gate import ExecutionGate

        sentinel = self._make_sentinel()
        layer = ToolPermissionLayer(sentinel=sentinel)
        layer.declare("agent-1", [WritePermission(paths=["/tmp/*"])])
        gate = ExecutionGate(db_path=":memory:")
        gate.register_policy("file_write", layer.as_gate_policy("agent-1", "file_write"))

        gate.check("file_write", {"path": "/etc/evil"})

        sentinel.push.assert_called_once()
        event = sentinel.push.call_args[0][0]
        assert event.event_type == "permission_violation"
        gate.close()

    def _make_sentinel(self):
        return MagicMock()


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_declare_and_verify(self):
        layer = ToolPermissionLayer()
        errors: list[Exception] = []

        def declarer():
            for i in range(10):
                layer.declare(f"agent-{i}", [ReadPermission(paths=[f"/data/{i}/*"])])

        def verifier():
            for i in range(10):
                try:
                    layer.verify(f"agent-{i}", "file_read", {"path": f"/data/{i}/x.csv"})
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=declarer) for _ in range(3)] + [
            threading.Thread(target=verifier) for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
