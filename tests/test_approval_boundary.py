"""Tests for agentcop.approvals — ApprovalBoundary."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from agentcop.approvals import ApprovalBoundary, ApprovalRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _boundary(**kw) -> ApprovalBoundary:
    """Create an in-memory boundary with sensible test defaults."""
    return ApprovalBoundary(requires_approval_above=70, timeout=300, **kw)


# ---------------------------------------------------------------------------
# Threshold logic — auto-approve vs pending
# ---------------------------------------------------------------------------


class TestThresholdLogic:
    def test_auto_approve_at_threshold(self):
        b = _boundary()
        req = b.submit("tool", {}, risk_score=70)
        assert req.approved is True
        assert req.resolved_at is not None

    def test_auto_approve_below_threshold(self):
        b = _boundary()
        req = b.submit("tool", {}, risk_score=0)
        assert req.approved is True

    def test_pending_above_threshold(self):
        b = _boundary()
        req = b.submit("tool", {}, risk_score=71)
        assert req.pending is True
        assert req.resolved_at is None
        b.close()

    def test_pending_at_max_risk(self):
        b = _boundary()
        req = b.submit("tool", {}, risk_score=100)
        assert req.pending is True
        b.close()

    def test_auto_approve_reason_mentions_threshold(self):
        b = _boundary()
        req = b.submit("tool", {}, risk_score=50)
        assert "70" in req.reason  # threshold referenced

    def test_zero_threshold_requires_approval_for_any_positive_score(self):
        b = ApprovalBoundary(requires_approval_above=0, timeout=300)
        req = b.submit("tool", {}, risk_score=1)
        assert req.pending is True
        b.close()

    def test_100_threshold_approves_everything(self):
        b = ApprovalBoundary(requires_approval_above=100, timeout=300)
        req = b.submit("tool", {}, risk_score=100)
        assert req.approved is True


# ---------------------------------------------------------------------------
# Manual approve / deny
# ---------------------------------------------------------------------------


class TestManualDecisions:
    def test_approve_pending_request(self):
        b = _boundary()
        req = b.submit("t", {}, risk_score=80)
        approved = b.approve(req.request_id)
        assert approved.approved is True
        assert approved.resolved_at is not None
        b.close()

    def test_deny_pending_request(self):
        b = _boundary()
        req = b.submit("t", {}, risk_score=80)
        denied = b.deny(req.request_id)
        assert denied.denied is True
        b.close()

    def test_approve_with_actor_and_reason(self):
        b = _boundary()
        req = b.submit("t", {}, risk_score=80)
        b.approve(req.request_id, actor="alice", reason="looks fine")
        assert req.reason == "looks fine"
        b.close()

    def test_approve_unknown_id_raises(self):
        b = _boundary()
        with pytest.raises(KeyError):
            b.approve("nonexistent-id")

    def test_deny_unknown_id_raises(self):
        b = _boundary()
        with pytest.raises(KeyError):
            b.deny("nonexistent-id")

    def test_approve_already_resolved_raises(self):
        b = _boundary()
        req = b.submit("t", {}, risk_score=80)
        b.approve(req.request_id)
        with pytest.raises(ValueError, match="status is"):
            b.approve(req.request_id)
        b.close()

    def test_deny_already_resolved_raises(self):
        b = _boundary()
        req = b.submit("t", {}, risk_score=80)
        b.approve(req.request_id)
        with pytest.raises(ValueError, match="status is"):
            b.deny(req.request_id)
        b.close()

    def test_timer_cancelled_on_approve(self):
        b = _boundary()
        req = b.submit("t", {}, risk_score=80)
        timer = b._timers.get(req.request_id)
        assert timer is not None
        b.approve(req.request_id)
        assert req.request_id not in b._timers
        b.close()


# ---------------------------------------------------------------------------
# Timeout → auto-deny
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_timeout_auto_denies(self):
        b = ApprovalBoundary(requires_approval_above=0, timeout=0.05)
        req = b.submit("risky", {}, risk_score=50)
        assert req.pending is True
        time.sleep(0.2)
        assert req.denied is True
        assert "auto-denied" in req.reason

    def test_timeout_sets_resolved_at(self):
        b = ApprovalBoundary(requires_approval_above=0, timeout=0.05)
        req = b.submit("t", {}, risk_score=1)
        time.sleep(0.2)
        assert req.resolved_at is not None

    def test_no_timeout_when_approved_first(self):
        b = ApprovalBoundary(requires_approval_above=0, timeout=2)
        req = b.submit("t", {}, risk_score=1)
        b.approve(req.request_id)
        time.sleep(0.05)  # well before 2s timeout
        assert req.approved is True
        b.close()

    def test_wait_for_decision_blocks_until_approved(self):
        b = _boundary()
        req = b.submit("t", {}, risk_score=90)

        def approver():
            time.sleep(0.05)
            b.approve(req.request_id)

        t = threading.Thread(target=approver)
        t.start()
        resolved = b.wait_for_decision(req.request_id, timeout=2)
        t.join()
        assert resolved.approved is True
        b.close()

    def test_wait_for_decision_returns_after_timeout(self):
        b = ApprovalBoundary(requires_approval_above=0, timeout=0.1)
        req = b.submit("t", {}, risk_score=1)
        # wait_for_decision with short wall-clock timeout
        resolved = b.wait_for_decision(req.request_id, timeout=0.5)
        # Either timed out by boundary or by wait — status should be decided
        assert not resolved.pending

    def test_wait_for_already_resolved_returns_immediately(self):
        b = _boundary()
        req = b.submit("t", {}, risk_score=20)  # auto-approved
        resolved = b.wait_for_decision(req.request_id)
        assert resolved.approved is True

    def test_wait_unknown_id_raises(self):
        b = _boundary()
        with pytest.raises(KeyError):
            b.wait_for_decision("ghost")


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


class TestInspection:
    def test_pending_requests_filters_correctly(self):
        b = _boundary()
        r1 = b.submit("a", {}, risk_score=80)
        r2 = b.submit("b", {}, risk_score=20)  # auto-approved
        pending = b.pending_requests()
        assert r1 in pending
        assert r2 not in pending
        b.close()

    def test_get_returns_request(self):
        b = _boundary()
        req = b.submit("t", {}, risk_score=10)
        assert b.get(req.request_id) is req

    def test_get_unknown_returns_none(self):
        b = _boundary()
        assert b.get("ghost") is None

    def test_all_requests(self):
        b = _boundary()
        r1 = b.submit("a", {}, risk_score=10)
        r2 = b.submit("b", {}, risk_score=10)
        all_reqs = b.all_requests()
        assert r1 in all_reqs
        assert r2 in all_reqs


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    def test_submitted_event_logged(self):
        b = _boundary()
        req = b.submit("my_tool", {"x": 1}, risk_score=50)
        trail = b.audit_trail(req.request_id)
        events = [e["event"] for e in trail]
        assert "submitted" in events

    def test_auto_approve_event_logged(self):
        b = _boundary()
        req = b.submit("tool", {}, risk_score=30)
        trail = b.audit_trail(req.request_id)
        events = [e["event"] for e in trail]
        assert "approved" in events

    def test_manual_approve_event_logged(self):
        b = _boundary()
        req = b.submit("tool", {}, risk_score=80)
        b.approve(req.request_id, actor="alice")
        trail = b.audit_trail(req.request_id)
        events = [e["event"] for e in trail]
        assert "approved" in events
        approve_entry = next(e for e in trail if e["event"] == "approved")
        assert approve_entry["actor"] == "alice"
        b.close()

    def test_deny_event_logged(self):
        b = _boundary()
        req = b.submit("tool", {}, risk_score=80)
        b.deny(req.request_id, reason="too risky")
        trail = b.audit_trail(req.request_id)
        deny_entry = next(e for e in trail if e["event"] == "denied")
        assert deny_entry["reason"] == "too risky"
        b.close()

    def test_timeout_event_logged(self):
        b = ApprovalBoundary(requires_approval_above=0, timeout=0.05)
        req = b.submit("risky", {}, risk_score=1)
        time.sleep(0.2)
        trail = b.audit_trail(req.request_id)
        events = [e["event"] for e in trail]
        assert "timeout" in events

    def test_audit_trail_all_requests(self):
        b = _boundary()
        b.submit("a", {}, risk_score=10)
        b.submit("b", {}, risk_score=10)
        trail = b.audit_trail()  # all requests
        assert len(trail) >= 4  # 2 submitted + 2 approved

    def test_audit_trail_contains_tool_and_risk_score(self):
        b = _boundary()
        req = b.submit("delete_file", {"path": "/data/x"}, risk_score=50)
        trail = b.audit_trail(req.request_id)
        submitted = next(e for e in trail if e["event"] == "submitted")
        assert submitted["tool"] == "delete_file"
        assert submitted["risk_score"] == 50

    def test_audit_trail_newest_first(self):
        b = _boundary()
        req = b.submit("t", {}, risk_score=80)
        b.approve(req.request_id)
        trail = b.audit_trail(req.request_id)
        # newest (approved) should come before oldest (submitted)
        assert trail[0]["event"] == "approved"
        b.close()

    def test_audit_trail_limit(self):
        b = _boundary()
        for _ in range(10):
            b.submit("t", {}, risk_score=10)
        trail = b.audit_trail(limit=3)
        assert len(trail) == 3


# ---------------------------------------------------------------------------
# Channel dispatch
# ---------------------------------------------------------------------------


class TestChannelDispatch:
    def test_cli_channel_prints_to_stderr(self, capsys):
        b = ApprovalBoundary(
            requires_approval_above=0, channels=["cli"], timeout=300
        )
        req = b.submit("dangerous_tool", {}, risk_score=50)
        captured = capsys.readouterr()
        assert "dangerous_tool" in captured.err
        assert req.request_id in captured.err
        b.close()

    def test_webhook_dispatch_posts_json(self):
        b = ApprovalBoundary(
            requires_approval_above=0,
            channels=["webhook"],
            timeout=300,
            webhook_url="https://approval.example.com/notify",
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            req = b.submit("delete_file", {"path": "/data/x"}, risk_score=50)
        mock_urlopen.assert_called_once()
        request_obj = mock_urlopen.call_args[0][0]
        import json, urllib.request as ur
        assert request_obj.full_url == "https://approval.example.com/notify"
        assert request_obj.get_header("Content-type") == "application/json"
        payload = json.loads(request_obj.data)
        assert payload["request_id"] == req.request_id
        assert payload["tool"] == "delete_file"
        assert payload["risk_score"] == 50
        b.close()

    def test_webhook_dict_channel_uses_own_url(self):
        b = ApprovalBoundary(
            requires_approval_above=0,
            channels=[{"type": "webhook", "url": "https://my-hook.example.com"}],
            timeout=300,
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            b.submit("t", {}, risk_score=50)
        request_obj = mock_urlopen.call_args[0][0]
        assert "my-hook.example.com" in request_obj.full_url
        b.close()

    def test_slack_channel_uses_slack_payload_format(self):
        b = ApprovalBoundary(
            requires_approval_above=0,
            channels=[{"type": "slack", "url": "https://hooks.slack.com/xxx"}],
            timeout=300,
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
            b.submit("my_tool", {}, risk_score=99)
        import json
        request_obj = mock_urlopen.call_args[0][0]
        payload = json.loads(request_obj.data)
        assert "text" in payload
        assert "my_tool" in payload["text"]
        b.close()

    def test_webhook_without_url_logs_dispatch_failed(self):
        b = ApprovalBoundary(
            requires_approval_above=0,
            channels=["webhook"],  # no URL!
            timeout=300,
        )
        req = b.submit("t", {}, risk_score=50)
        trail = b.audit_trail(req.request_id)
        events = [e["event"] for e in trail]
        assert "dispatch_failed" in events
        b.close()

    def test_dispatch_logged_in_audit_trail(self):
        b = ApprovalBoundary(
            requires_approval_above=0,
            channels=["cli"],
            timeout=300,
        )
        req = b.submit("t", {}, risk_score=50)
        trail = b.audit_trail(req.request_id)
        events = [e["event"] for e in trail]
        assert "dispatch_sent" in events
        b.close()

    def test_no_dispatch_for_auto_approved_requests(self):
        b = ApprovalBoundary(
            requires_approval_above=100,  # everything auto-approved
            channels=["cli"],
            timeout=300,
        )
        req = b.submit("t", {}, risk_score=50)
        assert req.approved is True
        trail = b.audit_trail(req.request_id)
        events = [e["event"] for e in trail]
        assert "dispatch_sent" not in events

    def test_multiple_channels(self, capsys):
        b = ApprovalBoundary(
            requires_approval_above=0,
            channels=["cli", "email"],
            timeout=300,
        )
        b.submit("t", {}, risk_score=50)
        captured = capsys.readouterr()
        # both cli and email write to stderr
        assert "APPROVAL REQUIRED" in captured.err
        assert "EMAIL" in captured.err
        b.close()


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestBoundaryThreadSafety:
    def test_concurrent_submits(self):
        b = _boundary()
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(10):
                    req = b.submit("t", {}, risk_score=30)  # auto-approved
                    assert req.approved is True
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(b.all_requests()) == 50

    def test_concurrent_approve_deny(self):
        b = _boundary()
        reqs = [b.submit("t", {}, risk_score=80) for _ in range(10)]
        errors: list[Exception] = []

        def approver(req):
            try:
                b.approve(req.request_id)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=approver, args=(r,)) for r in reqs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        b.close()
