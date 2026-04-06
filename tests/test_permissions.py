"""Tests for agentcop.permissions — PermissionSet, PermissionChecker, ToolPermission."""

from __future__ import annotations

import pytest

from agentcop.permissions import (
    PermissionChecker,
    PermissionDenied,
    PermissionSet,
    ToolPermission,
)


# ---------------------------------------------------------------------------
# ToolPermission
# ---------------------------------------------------------------------------


class TestToolPermission:
    def test_matches_exact(self):
        perm = ToolPermission(tool="file_read")
        assert perm.matches("file_read") is True
        assert perm.matches("file_write") is False

    def test_matches_wildcard(self):
        perm = ToolPermission(tool="*")
        assert perm.matches("anything") is True
        assert perm.matches("file_read") is True

    def test_evaluate_allow(self):
        perm = ToolPermission(tool="read", allow=True)
        allowed, reason = perm.evaluate({})
        assert allowed is True
        assert reason == "tool allowed"

    def test_evaluate_deny(self):
        perm = ToolPermission(tool="write", allow=False, deny_reason="writes blocked")
        allowed, reason = perm.evaluate({})
        assert allowed is False
        assert reason == "writes blocked"

    def test_evaluate_condition_passes(self):
        perm = ToolPermission(
            tool="write",
            allow=True,
            conditions=[lambda args: args.get("path", "").startswith("/tmp/")],
        )
        allowed, reason = perm.evaluate({"path": "/tmp/x"})
        assert allowed is True

    def test_evaluate_condition_fails(self):
        perm = ToolPermission(
            tool="write",
            allow=True,
            conditions=[lambda args: args.get("path", "").startswith("/tmp/")],
        )
        allowed, reason = perm.evaluate({"path": "/etc/passwd"})
        assert allowed is False
        assert "condition" in reason.lower()

    def test_deny_skips_conditions(self):
        """Conditions are not evaluated when allow=False."""
        called = []
        perm = ToolPermission(
            tool="t",
            allow=False,
            deny_reason="flat deny",
            conditions=[lambda args: called.append(1) or True],
        )
        allowed, _ = perm.evaluate({})
        assert allowed is False
        assert called == []


# ---------------------------------------------------------------------------
# PermissionSet
# ---------------------------------------------------------------------------


class TestPermissionSet:
    def test_grant_and_get_rules(self):
        ps = PermissionSet()
        perm = ToolPermission(tool="read")
        ps.grant("dev", perm)
        rules = ps.get_rules("dev")
        assert len(rules) == 1
        assert rules[0].tool == "read"

    def test_unknown_role_returns_empty(self):
        ps = PermissionSet()
        assert ps.get_rules("ghost") == []

    def test_revoke_removes_exact_tool(self):
        ps = PermissionSet()
        ps.grant("dev", ToolPermission(tool="read"))
        ps.grant("dev", ToolPermission(tool="write"))
        ps.revoke("dev", "read")
        tools = [r.tool for r in ps.get_rules("dev")]
        assert "read" not in tools
        assert "write" in tools

    def test_revoke_nonexistent_is_noop(self):
        ps = PermissionSet()
        ps.grant("dev", ToolPermission(tool="read"))
        ps.revoke("dev", "nonexistent")
        assert len(ps.get_rules("dev")) == 1

    def test_list_roles(self):
        ps = PermissionSet()
        ps.grant("admin", ToolPermission(tool="*"))
        ps.grant("viewer", ToolPermission(tool="read"))
        roles = ps.list_roles()
        assert "admin" in roles
        assert "viewer" in roles

    def test_get_rules_returns_snapshot(self):
        """Mutating the returned list must not affect internal state."""
        ps = PermissionSet()
        ps.grant("dev", ToolPermission(tool="read"))
        rules = ps.get_rules("dev")
        rules.clear()
        assert len(ps.get_rules("dev")) == 1


# ---------------------------------------------------------------------------
# PermissionChecker
# ---------------------------------------------------------------------------


class TestPermissionChecker:
    def _make_checker(self, default_allow=False):
        ps = PermissionSet()
        ps.grant("admin", ToolPermission(tool="*", allow=True))
        ps.grant("readonly", ToolPermission(tool="file_read", allow=True))
        ps.grant("readonly", ToolPermission(tool="file_write", allow=False, deny_reason="read-only"))
        return PermissionChecker(ps, default_allow=default_allow)

    def test_admin_wildcard_allows_any(self):
        checker = self._make_checker()
        assert checker.is_allowed("admin", "file_read") is True
        assert checker.is_allowed("admin", "anything") is True

    def test_readonly_allows_file_read(self):
        checker = self._make_checker()
        assert checker.is_allowed("readonly", "file_read") is True

    def test_readonly_denies_file_write(self):
        checker = self._make_checker()
        allowed, reason = checker.check("readonly", "file_write")
        assert allowed is False
        assert reason == "read-only"

    def test_no_matching_rule_deny_by_default(self):
        checker = self._make_checker(default_allow=False)
        allowed, reason = checker.check("readonly", "delete")
        assert allowed is False
        assert "no rule" in reason.lower()

    def test_no_matching_rule_allow_when_default_allow(self):
        checker = self._make_checker(default_allow=True)
        allowed, _ = checker.check("readonly", "delete")
        assert allowed is True

    def test_unknown_role_deny_by_default(self):
        checker = self._make_checker()
        assert checker.is_allowed("unknown_role", "anything") is False

    def test_enforce_raises_on_denial(self):
        checker = self._make_checker()
        with pytest.raises(PermissionDenied, match="readonly"):
            checker.enforce("readonly", "file_write")

    def test_enforce_passes_on_allow(self):
        checker = self._make_checker()
        checker.enforce("admin", "any_tool")  # no exception

    def test_check_with_args_passed_to_condition(self):
        ps = PermissionSet()
        ps.grant(
            "dev",
            ToolPermission(
                tool="file_write",
                allow=True,
                conditions=[lambda args: args.get("path", "").startswith("/tmp/")],
            ),
        )
        checker = PermissionChecker(ps)
        assert checker.is_allowed("dev", "file_write", {"path": "/tmp/ok"}) is True
        assert checker.is_allowed("dev", "file_write", {"path": "/etc/evil"}) is False

    def test_first_matching_rule_wins(self):
        """Specific deny before wildcard allow — deny must win."""
        ps = PermissionSet()
        ps.grant("dev", ToolPermission(tool="danger", allow=False, deny_reason="blocked"))
        ps.grant("dev", ToolPermission(tool="*", allow=True))
        checker = PermissionChecker(ps)
        assert checker.is_allowed("dev", "danger") is False
        assert checker.is_allowed("dev", "safe_tool") is True

    def test_thread_safety(self):
        import threading

        ps = PermissionSet()
        ps.grant("user", ToolPermission(tool="*", allow=True))
        checker = PermissionChecker(ps)
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(50):
                    checker.is_allowed("user", "tool")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
