"""Tests for agentcop.sandbox — AgentSandbox and SandboxTimeoutError."""

from __future__ import annotations

import subprocess
import threading
import time
import urllib.request

import pytest

from agentcop.sandbox import (
    AgentSandbox,
    SandboxTimeoutError,
    SandboxViolation,
    _REAL_OPEN,
    _REAL_SUBPROCESS_RUN,
    _REAL_URLOPEN,
)


# ---------------------------------------------------------------------------
# SandboxTimeoutError
# ---------------------------------------------------------------------------


class TestSandboxTimeoutError:
    def test_no_arg_construction(self):
        """PyThreadState_SetAsyncExc calls the class with no args."""
        exc = SandboxTimeoutError()
        assert exc.violation_type == "timeout"
        assert "time limit" in str(exc).lower()

    def test_is_sandbox_violation(self):
        assert isinstance(SandboxTimeoutError(), SandboxViolation)

    def test_custom_message(self):
        exc = SandboxTimeoutError("custom", violation_type="timeout")
        assert str(exc) == "custom"


# ---------------------------------------------------------------------------
# AgentSandbox — init and context manager
# ---------------------------------------------------------------------------


class TestAgentSandboxInit:
    def test_default_no_restrictions(self):
        sb = AgentSandbox()
        assert sb.allowed_paths == []
        assert sb.allowed_domains == []
        assert sb.max_execution_time is None
        assert sb.intercept_syscalls is True

    def test_custom_config(self):
        sb = AgentSandbox(allowed_paths=["/tmp/*"], allowed_domains=["safe.com"])
        assert sb.allowed_paths == ["/tmp/*"]
        assert sb.allowed_domains == ["safe.com"]

    def test_context_manager_returns_self(self):
        sb = AgentSandbox(intercept_syscalls=False)
        with sb as entered:
            assert entered is sb

    def test_thread_id_set_on_enter_cleared_on_exit(self):
        sb = AgentSandbox(intercept_syscalls=False)
        assert sb._thread_id is None
        with sb:
            assert sb._thread_id == threading.get_ident()
        assert sb._thread_id is None

    def test_timer_started_and_cancelled(self):
        sb = AgentSandbox(max_execution_time=60, intercept_syscalls=False)
        with sb:
            assert sb._timer is not None
            assert sb._timer.is_alive()
        assert sb._timer is None  # cancelled and cleared


# ---------------------------------------------------------------------------
# AgentSandbox — path checks (unit, no patches needed)
# ---------------------------------------------------------------------------


class TestAgentSandboxPathChecks:
    def test_no_restrictions_allows_any(self):
        sb = AgentSandbox(allowed_paths=[])
        sb._check_path("/etc/passwd")  # no exception

    def test_path_within_glob_passes(self):
        sb = AgentSandbox(allowed_paths=["/tmp/*"])
        sb._check_path("/tmp/out.txt")

    def test_nested_path_within_glob_passes(self):
        # fnmatch '*' matches across '/' in Python
        sb = AgentSandbox(allowed_paths=["/tmp/*"])
        sb._check_path("/tmp/a/b/c.txt")

    def test_path_outside_glob_raises(self):
        sb = AgentSandbox(allowed_paths=["/tmp/*"])
        with pytest.raises(SandboxViolation) as ei:
            sb._check_path("/etc/passwd")
        assert ei.value.violation_type == "path_blocked"
        assert ei.value.detail["path"] == "/etc/passwd"

    def test_multiple_globs(self):
        sb = AgentSandbox(allowed_paths=["/tmp/*", "/output/*"])
        sb._check_path("/tmp/x")
        sb._check_path("/output/report.csv")
        with pytest.raises(SandboxViolation):
            sb._check_path("/etc/secret")

    def test_exact_path_in_glob(self):
        sb = AgentSandbox(allowed_paths=["/tmp/allowed.txt"])
        sb._check_path("/tmp/allowed.txt")
        with pytest.raises(SandboxViolation):
            sb._check_path("/tmp/other.txt")


# ---------------------------------------------------------------------------
# AgentSandbox — domain checks (unit, no patches needed)
# ---------------------------------------------------------------------------


class TestAgentSandboxDomainChecks:
    def test_no_restrictions_allows_any(self):
        sb = AgentSandbox(allowed_domains=[])
        sb._check_domain("https://anything.com")

    def test_exact_domain_passes(self):
        sb = AgentSandbox(allowed_domains=["api.openai.com"])
        sb._check_domain("https://api.openai.com/v1/chat")

    def test_bare_domain_passes(self):
        sb = AgentSandbox(allowed_domains=["safe.com"])
        sb._check_domain("safe.com")

    def test_domain_not_in_list_raises(self):
        sb = AgentSandbox(allowed_domains=["safe.com"])
        with pytest.raises(SandboxViolation) as ei:
            sb._check_domain("https://evil.com/steal")
        assert ei.value.violation_type == "domain_blocked"
        assert ei.value.detail["host"] == "evil.com"

    def test_subdomain_allowed(self):
        sb = AgentSandbox(allowed_domains=["openai.com"])
        sb._check_domain("https://api.openai.com/")

    def test_multiple_domains(self):
        sb = AgentSandbox(allowed_domains=["api.openai.com", "agentcop.live"])
        sb._check_domain("https://agentcop.live/report")
        with pytest.raises(SandboxViolation):
            sb._check_domain("https://phishing.com")


# ---------------------------------------------------------------------------
# AgentSandbox — path interception via syscall patches
# ---------------------------------------------------------------------------


class TestPathInterception:
    def test_open_blocked_path_raises(self, tmp_path):
        """builtins.open is patched to block paths outside allowed_paths."""
        import builtins

        with AgentSandbox(allowed_paths=[str(tmp_path) + "/*"]) as sb:
            # allowed path: open succeeds
            allowed = tmp_path / "allowed.txt"
            allowed.write_text("ok")
            fh = builtins.open(str(allowed), "r")
            fh.close()

            # blocked path: raises SandboxViolation with violation_type="path_blocked"
            with pytest.raises(SandboxViolation) as ei:
                builtins.open("/etc/hostname", "r")
            assert ei.value.violation_type == "path_blocked"

    def test_patches_installed_and_removed(self, tmp_path):
        """After __exit__, builtins.open is restored."""
        import builtins

        with AgentSandbox(allowed_paths=[str(tmp_path) + "/*"]):
            assert builtins.open is not _REAL_OPEN

        assert builtins.open is _REAL_OPEN

    def test_no_patch_when_intercept_disabled(self):
        import builtins

        with AgentSandbox(allowed_paths=["/tmp/*"], intercept_syscalls=False):
            assert builtins.open is _REAL_OPEN

    def test_open_integer_fd_not_checked(self, tmp_path):
        """open(fd, ...) passes through without path check."""
        import builtins

        f = tmp_path / "x.txt"
        f.write_text("hello")
        # Keep file_obj alive so the fd remains valid throughout the test.
        file_obj = _REAL_OPEN(str(f))
        fd = file_obj.fileno()
        try:
            with AgentSandbox(allowed_paths=["/nonexistent/*"]) as sb:
                # integer fd should not trigger path check
                fh = builtins.open(fd, closefd=False)
                fh.close()
        finally:
            file_obj.close()


# ---------------------------------------------------------------------------
# AgentSandbox — domain interception via syscall patches
# ---------------------------------------------------------------------------


class TestDomainInterception:
    def test_urlopen_blocked_domain_raises(self):
        """urllib.request.urlopen is patched to block non-allowed domains."""
        with AgentSandbox(allowed_domains=["safe.com"]):
            with pytest.raises(SandboxViolation) as ei:
                urllib.request.urlopen("https://evil.com/bad")
            assert ei.value.violation_type == "domain_blocked"

    def test_urlopen_allowed_domain_passes(self):
        """Allowed domain reaches the real urlopen (which may fail for other reasons)."""
        with AgentSandbox(allowed_domains=["localhost"]):
            with pytest.raises(Exception) as ei:
                urllib.request.urlopen("http://localhost:1/nonexistent", timeout=0.01)
            # SandboxViolation must NOT be among the exception types
            assert not isinstance(ei.value, SandboxViolation)

    def test_urlopen_restored_after_exit(self):
        with AgentSandbox(allowed_domains=["x.com"]):
            pass
        assert urllib.request.urlopen is _REAL_URLOPEN

    def test_no_patch_when_intercept_disabled(self):
        with AgentSandbox(allowed_domains=["x.com"], intercept_syscalls=False):
            assert urllib.request.urlopen is _REAL_URLOPEN


# ---------------------------------------------------------------------------
# AgentSandbox — subprocess interception
# ---------------------------------------------------------------------------


class TestSubprocessInterception:
    def test_absolute_path_outside_allowed_raises(self, tmp_path):
        with AgentSandbox(allowed_paths=[str(tmp_path) + "/*"]):
            with pytest.raises(SandboxViolation) as ei:
                subprocess.run(["/etc/evil_program", "--bad"])
            assert ei.value.violation_type == "path_blocked"

    def test_relative_command_not_checked(self, tmp_path):
        """Bare command names (not absolute paths) are not path-checked."""
        with AgentSandbox(allowed_paths=[str(tmp_path) + "/*"]):
            # "echo" is a bare command — no path check
            result = subprocess.run(
                ["echo", "hello"], capture_output=True, text=True
            )
            assert result.returncode == 0

    def test_subprocess_restored_after_exit(self):
        with AgentSandbox(allowed_paths=["/tmp/*"]):
            pass
        assert subprocess.run is _REAL_SUBPROCESS_RUN


# ---------------------------------------------------------------------------
# AgentSandbox — max_execution_time / timeout
# ---------------------------------------------------------------------------


class TestSandboxTimeout:
    def test_timeout_raises_sandbox_timeout_error(self):
        with pytest.raises(SandboxTimeoutError):
            with AgentSandbox(max_execution_time=0.1, intercept_syscalls=False):
                time.sleep(5)  # interrupted after ~0.1 s

    def test_timeout_is_subclass_of_sandbox_violation(self):
        with pytest.raises(SandboxViolation):
            with AgentSandbox(max_execution_time=0.1, intercept_syscalls=False):
                time.sleep(5)

    def test_no_timeout_when_finishes_in_time(self):
        """No exception when the block completes within the time limit."""
        with AgentSandbox(max_execution_time=5, intercept_syscalls=False):
            time.sleep(0.01)  # well within 5 s

    def test_timer_cancelled_on_clean_exit(self):
        sb = AgentSandbox(max_execution_time=30, intercept_syscalls=False)
        with sb:
            pass  # exits cleanly
        assert sb._timer is None

    def test_timer_cancelled_on_exception(self):
        sb = AgentSandbox(max_execution_time=30, intercept_syscalls=False)
        try:
            with sb:
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert sb._timer is None


# ---------------------------------------------------------------------------
# AgentSandbox — ToolPermissionLayer integration
# ---------------------------------------------------------------------------


class TestPermissionLayerIntegration:
    def test_inherits_paths_from_layer(self):
        from agentcop.permissions import ToolPermissionLayer, WritePermission

        layer = ToolPermissionLayer()
        layer.declare("agent-1", [WritePermission(paths=["/tmp/*", "/output/*"])])

        sb = AgentSandbox(permission_layer=layer, agent_id="agent-1", intercept_syscalls=False)
        assert "/tmp/*" in sb.allowed_paths
        assert "/output/*" in sb.allowed_paths

    def test_inherits_domains_from_layer(self):
        from agentcop.permissions import NetworkPermission, ToolPermissionLayer

        layer = ToolPermissionLayer()
        layer.declare("agent-1", [NetworkPermission(domains=["api.openai.com", "safe.io"])])

        sb = AgentSandbox(permission_layer=layer, agent_id="agent-1", intercept_syscalls=False)
        assert "api.openai.com" in sb.allowed_domains
        assert "safe.io" in sb.allowed_domains

    def test_merges_with_explicit_config(self):
        from agentcop.permissions import ToolPermissionLayer, WritePermission

        layer = ToolPermissionLayer()
        layer.declare("agent-1", [WritePermission(paths=["/data/*"])])

        sb = AgentSandbox(
            allowed_paths=["/tmp/*"],
            permission_layer=layer,
            agent_id="agent-1",
            intercept_syscalls=False,
        )
        assert "/tmp/*" in sb.allowed_paths
        assert "/data/*" in sb.allowed_paths

    def test_no_merge_without_agent_id(self):
        from agentcop.permissions import ToolPermissionLayer, WritePermission

        layer = ToolPermissionLayer()
        layer.declare("agent-1", [WritePermission(paths=["/data/*"])])

        sb = AgentSandbox(permission_layer=layer, intercept_syscalls=False)  # no agent_id
        assert sb.allowed_paths == []

    def test_unknown_agent_no_error(self):
        from agentcop.permissions import ToolPermissionLayer

        layer = ToolPermissionLayer()
        sb = AgentSandbox(
            permission_layer=layer, agent_id="unknown", intercept_syscalls=False
        )
        assert sb.allowed_paths == []

    def test_enforced_at_runtime(self, tmp_path):
        """Integration: sandbox inherited from layer blocks paths at open() time."""
        from agentcop.permissions import ToolPermissionLayer, WritePermission
        import builtins

        layer = ToolPermissionLayer()
        layer.declare("agent-1", [WritePermission(paths=[str(tmp_path) + "/*"])])

        with AgentSandbox(permission_layer=layer, agent_id="agent-1") as sb:
            # Path from layer — allowed
            allowed = tmp_path / "ok.txt"
            allowed.write_text("data")
            builtins.open(str(allowed), "r").close()

            # Path not in layer — blocked
            with pytest.raises(SandboxViolation):
                builtins.open("/etc/hostname", "r")


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestSandboxThreadSafety:
    def test_each_thread_independent(self, tmp_path):
        """The sandbox only affects the thread that entered it."""
        import builtins

        results: dict[str, bool] = {}
        barrier = threading.Barrier(2)

        def worker():
            # Worker thread: no sandbox active → open anywhere should work
            barrier.wait()  # sync: main thread sandbox is now active
            f = tmp_path / "worker.txt"
            f.write_text("hi")
            try:
                builtins.open(str(f), "r").close()
                results["worker_ok"] = True
            except SandboxViolation:
                results["worker_ok"] = False

        t = threading.Thread(target=worker)
        t.start()

        with AgentSandbox(allowed_paths=["/nonexistent/*"]):
            barrier.wait()  # signal worker to run
            t.join()

        assert results.get("worker_ok") is True

    def test_multiple_sandboxes_same_thread(self, tmp_path):
        """Stacked sandboxes: inner restrictions apply, outer restored on pop."""
        import builtins

        allowed = tmp_path / "x.txt"
        allowed.write_text("data")

        with AgentSandbox(allowed_paths=[str(tmp_path) + "/*"]):
            with AgentSandbox(allowed_paths=[str(tmp_path) + "/*"]):
                builtins.open(str(allowed), "r").close()
            builtins.open(str(allowed), "r").close()
        # After both exit, real open is restored
        assert builtins.open is _REAL_OPEN
