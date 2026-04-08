"""Tests for agentcop.sandbox — ExecutionSandbox, SandboxPolicy, SandboxViolation."""

from __future__ import annotations

import threading

import pytest

from agentcop.sandbox import ExecutionSandbox, SandboxPolicy, SandboxViolation

# ---------------------------------------------------------------------------
# SandboxPolicy
# ---------------------------------------------------------------------------


class TestSandboxPolicy:
    def test_defaults_are_permissive(self):
        p = SandboxPolicy()
        assert p.allowed_paths == []
        assert p.denied_paths == []
        assert p.allowed_env_vars == []
        assert p.denied_env_vars == []
        assert p.max_output_bytes is None


# ---------------------------------------------------------------------------
# SandboxViolation
# ---------------------------------------------------------------------------


class TestSandboxViolation:
    def test_fields(self):
        exc = SandboxViolation(
            "bad path",
            violation_type="path_denied",
            detail={"path": "/etc/passwd"},
        )
        assert exc.violation_type == "path_denied"
        assert exc.detail["path"] == "/etc/passwd"
        assert str(exc) == "bad path"

    def test_default_detail_is_empty(self):
        exc = SandboxViolation("x", violation_type="path_denied")
        assert exc.detail == {}


# ---------------------------------------------------------------------------
# ExecutionSandbox — context manager
# ---------------------------------------------------------------------------


class TestSandboxContextManager:
    def test_active_inside_block(self):
        sb = ExecutionSandbox()
        assert sb.active is False
        with sb:
            assert sb.active is True
        assert sb.active is False

    def test_reentrant(self):
        sb = ExecutionSandbox()
        with sb:
            with sb:
                assert sb.active is True
            assert sb.active is True
        assert sb.active is False

    def test_active_is_per_thread(self):
        sb = ExecutionSandbox()
        results: dict[str, bool] = {}

        def worker():
            results["before"] = sb.active
            with sb:
                results["inside"] = sb.active
            results["after"] = sb.active

        main_active_inside: list[bool] = []
        with sb:
            main_active_inside.append(sb.active)
            t = threading.Thread(target=worker)
            t.start()
            t.join()

        # main thread was active inside the block
        assert main_active_inside[0] is True
        # main thread is no longer active after the block
        assert sb.active is False
        assert results["before"] is False
        assert results["inside"] is True
        assert results["after"] is False


# ---------------------------------------------------------------------------
# ExecutionSandbox — path checks
# ---------------------------------------------------------------------------


class TestPathChecks:
    def test_no_policy_allows_any_path(self):
        sb = ExecutionSandbox()
        sb.assert_path_allowed("/etc/passwd")  # no exception

    def test_allowed_path_passes(self):
        sb = ExecutionSandbox(SandboxPolicy(allowed_paths=["/tmp/", "/var/data/"]))
        sb.assert_path_allowed("/tmp/output.txt")
        sb.assert_path_allowed("/var/data/file.csv")

    def test_path_not_in_allowlist_raises(self):
        sb = ExecutionSandbox(SandboxPolicy(allowed_paths=["/tmp/"]))
        with pytest.raises(SandboxViolation) as exc_info:
            sb.assert_path_allowed("/etc/passwd")
        assert exc_info.value.violation_type == "path_not_allowed"

    def test_denied_path_raises(self):
        sb = ExecutionSandbox(SandboxPolicy(denied_paths=["/etc/"]))
        with pytest.raises(SandboxViolation) as exc_info:
            sb.assert_path_allowed("/etc/hosts")
        assert exc_info.value.violation_type == "path_denied"

    def test_denied_takes_precedence_over_allowed(self):
        """A path matching both allowed and denied should be denied."""
        sb = ExecutionSandbox(
            SandboxPolicy(
                allowed_paths=["/etc/"],
                denied_paths=["/etc/shadow"],
            )
        )
        sb.assert_path_allowed("/etc/hosts")  # allowed, not denied
        with pytest.raises(SandboxViolation):
            sb.assert_path_allowed("/etc/shadow")  # explicitly denied

    def test_empty_allowed_paths_means_all_permitted(self):
        sb = ExecutionSandbox(SandboxPolicy(allowed_paths=[]))
        sb.assert_path_allowed("/anything/goes")  # no exception

    def test_violation_detail_contains_path(self):
        sb = ExecutionSandbox(SandboxPolicy(denied_paths=["/secret/"]))
        with pytest.raises(SandboxViolation) as exc_info:
            sb.assert_path_allowed("/secret/key")
        assert exc_info.value.detail["path"] == "/secret/key"


# ---------------------------------------------------------------------------
# ExecutionSandbox — env var checks
# ---------------------------------------------------------------------------


class TestEnvChecks:
    def test_no_policy_allows_any_env(self):
        sb = ExecutionSandbox()
        sb.assert_env_allowed("SECRET_KEY")  # no exception

    def test_allowed_env_var_passes(self):
        sb = ExecutionSandbox(SandboxPolicy(allowed_env_vars=["HOME", "PATH"]))
        sb.assert_env_allowed("HOME")
        sb.assert_env_allowed("PATH")

    def test_env_not_in_allowlist_raises(self):
        sb = ExecutionSandbox(SandboxPolicy(allowed_env_vars=["HOME"]))
        with pytest.raises(SandboxViolation) as exc_info:
            sb.assert_env_allowed("AWS_SECRET_ACCESS_KEY")
        assert exc_info.value.violation_type == "env_not_allowed"

    def test_denied_env_var_raises(self):
        sb = ExecutionSandbox(SandboxPolicy(denied_env_vars=["AWS_SECRET_ACCESS_KEY"]))
        with pytest.raises(SandboxViolation) as exc_info:
            sb.assert_env_allowed("AWS_SECRET_ACCESS_KEY")
        assert exc_info.value.violation_type == "env_denied"

    def test_denied_env_precedence_over_allowed(self):
        sb = ExecutionSandbox(
            SandboxPolicy(
                allowed_env_vars=["HOME", "SECRET"],
                denied_env_vars=["SECRET"],
            )
        )
        sb.assert_env_allowed("HOME")
        with pytest.raises(SandboxViolation):
            sb.assert_env_allowed("SECRET")


# ---------------------------------------------------------------------------
# ExecutionSandbox — output size checks
# ---------------------------------------------------------------------------


class TestOutputSizeChecks:
    def test_no_limit_always_passes(self):
        sb = ExecutionSandbox(SandboxPolicy(max_output_bytes=None))
        sb.check_output_size(999_999_999)  # no exception

    def test_within_limit_passes(self):
        sb = ExecutionSandbox(SandboxPolicy(max_output_bytes=1024))
        sb.check_output_size(1024)  # equal to limit — OK

    def test_exceeds_limit_raises(self):
        sb = ExecutionSandbox(SandboxPolicy(max_output_bytes=100))
        with pytest.raises(SandboxViolation) as exc_info:
            sb.check_output_size(101)
        assert exc_info.value.violation_type == "output_too_large"
        assert exc_info.value.detail["size_bytes"] == 101
        assert exc_info.value.detail["max_output_bytes"] == 100
