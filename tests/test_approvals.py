"""Tests for agentcop.approvals — ApprovalGate and policy types."""

from __future__ import annotations

import threading

import pytest

from agentcop.approvals import (
    ApprovalDenied,
    ApprovalGate,
    ApprovalRequest,
    AutoApprovePolicy,
    AutoDenyPolicy,
    CallbackApprovalPolicy,
)


# ---------------------------------------------------------------------------
# ApprovalRequest
# ---------------------------------------------------------------------------


class TestApprovalRequest:
    def test_default_status_is_pending(self):
        req = ApprovalRequest(tool="t", args={})
        assert req.pending is True
        assert req.approved is False
        assert req.denied is False

    def test_approved_status(self):
        req = ApprovalRequest(tool="t", args={}, status="approved")
        assert req.approved is True
        assert req.pending is False

    def test_denied_status(self):
        req = ApprovalRequest(tool="t", args={}, status="denied")
        assert req.denied is True
        assert req.approved is False

    def test_unique_request_ids(self):
        ids = {ApprovalRequest(tool="t", args={}).request_id for _ in range(100)}
        assert len(ids) == 100

    def test_created_at_is_set(self):
        req = ApprovalRequest(tool="t", args={})
        assert req.created_at is not None

    def test_resolved_at_defaults_to_none(self):
        req = ApprovalRequest(tool="t", args={})
        assert req.resolved_at is None


# ---------------------------------------------------------------------------
# AutoApprovePolicy
# ---------------------------------------------------------------------------


class TestAutoApprovePolicy:
    def test_approves_at_threshold(self):
        policy = AutoApprovePolicy(risk_threshold=50)
        req = ApprovalRequest(tool="t", args={}, risk_score=50)
        status, _ = policy.evaluate(req)
        assert status == "approved"

    def test_approves_below_threshold(self):
        policy = AutoApprovePolicy(risk_threshold=50)
        req = ApprovalRequest(tool="t", args={}, risk_score=30)
        status, _ = policy.evaluate(req)
        assert status == "approved"

    def test_denies_above_threshold(self):
        policy = AutoApprovePolicy(risk_threshold=50)
        req = ApprovalRequest(tool="t", args={}, risk_score=51)
        status, _ = policy.evaluate(req)
        assert status == "denied"

    def test_reason_mentions_threshold(self):
        policy = AutoApprovePolicy(risk_threshold=40)
        req = ApprovalRequest(tool="t", args={}, risk_score=10)
        _, reason = policy.evaluate(req)
        assert "40" in reason


# ---------------------------------------------------------------------------
# AutoDenyPolicy
# ---------------------------------------------------------------------------


class TestAutoDenyPolicy:
    def test_denies_at_threshold(self):
        policy = AutoDenyPolicy(risk_threshold=80)
        req = ApprovalRequest(tool="t", args={}, risk_score=80)
        status, _ = policy.evaluate(req)
        assert status == "denied"

    def test_denies_above_threshold(self):
        policy = AutoDenyPolicy(risk_threshold=80)
        req = ApprovalRequest(tool="t", args={}, risk_score=99)
        status, _ = policy.evaluate(req)
        assert status == "denied"

    def test_approves_below_threshold(self):
        policy = AutoDenyPolicy(risk_threshold=80)
        req = ApprovalRequest(tool="t", args={}, risk_score=79)
        status, _ = policy.evaluate(req)
        assert status == "approved"


# ---------------------------------------------------------------------------
# CallbackApprovalPolicy
# ---------------------------------------------------------------------------


class TestCallbackApprovalPolicy:
    def test_callback_returning_true_approves(self):
        policy = CallbackApprovalPolicy(callback=lambda req: True)
        req = ApprovalRequest(tool="t", args={})
        status, _ = policy.evaluate(req)
        assert status == "approved"

    def test_callback_returning_false_denies(self):
        policy = CallbackApprovalPolicy(callback=lambda req: False)
        req = ApprovalRequest(tool="t", args={})
        status, _ = policy.evaluate(req)
        assert status == "denied"

    def test_callback_receives_request(self):
        received: list[ApprovalRequest] = []

        def cb(req: ApprovalRequest) -> bool:
            received.append(req)
            return True

        policy = CallbackApprovalPolicy(callback=cb)
        req = ApprovalRequest(tool="my_tool", args={"x": 1})
        policy.evaluate(req)
        assert len(received) == 1
        assert received[0].tool == "my_tool"

    def test_custom_reasons(self):
        policy = CallbackApprovalPolicy(
            callback=lambda req: True,
            approve_reason="human said yes",
            deny_reason="human said no",
        )
        req = ApprovalRequest(tool="t", args={})
        _, reason = policy.evaluate(req)
        assert reason == "human said yes"


# ---------------------------------------------------------------------------
# ApprovalGate
# ---------------------------------------------------------------------------


@pytest.fixture
def gate_approve():
    return ApprovalGate(policy=AutoApprovePolicy(risk_threshold=100))


@pytest.fixture
def gate_deny():
    return ApprovalGate(policy=AutoDenyPolicy(risk_threshold=0))


class TestApprovalGate:
    def test_request_returns_approval_request(self, gate_approve):
        req = gate_approve.request("my_tool", {"a": 1}, risk_score=10)
        assert isinstance(req, ApprovalRequest)
        assert req.tool == "my_tool"
        assert req.args == {"a": 1}
        assert req.risk_score == 10

    def test_request_approved(self, gate_approve):
        req = gate_approve.request("tool", {}, risk_score=0)
        assert req.approved is True
        assert req.resolved_at is not None

    def test_request_denied(self, gate_deny):
        req = gate_deny.request("tool", {}, risk_score=100)
        assert req.denied is True

    def test_default_policy_approves_everything(self):
        gate = ApprovalGate()
        req = gate.request("anything", {}, risk_score=100)
        assert req.approved is True

    def test_request_stored(self, gate_approve):
        req = gate_approve.request("t", {})
        assert gate_approve.get(req.request_id) is req

    def test_get_unknown_returns_none(self, gate_approve):
        assert gate_approve.get("nonexistent-id") is None

    def test_all_requests(self, gate_approve):
        gate_approve.request("a", {})
        gate_approve.request("b", {})
        all_reqs = gate_approve.all_requests()
        assert len(all_reqs) == 2

    def test_enforce_passes_when_approved(self, gate_approve):
        req = gate_approve.enforce("safe_tool", {}, risk_score=0)
        assert req.approved is True

    def test_enforce_raises_when_denied(self, gate_deny):
        with pytest.raises(ApprovalDenied, match="my_tool"):
            gate_deny.enforce("my_tool", {}, risk_score=100)

    def test_manual_approve(self):
        """Test manually approving a pending request via callback policy."""
        event = threading.Event()
        gate = ApprovalGate(
            policy=CallbackApprovalPolicy(callback=lambda req: event.wait(timeout=0.1))
        )
        # callback returns False (timeout) → denied in this call
        req = gate.request("t", {}, risk_score=50)
        # Force back to pending for manual approval test
        req.status = "pending"
        req.resolved_at = None
        gate._requests[req.request_id] = req

        approved = gate.approve(req.request_id, reason="looks good")
        assert approved.approved is True
        assert approved.reason == "looks good"

    def test_manual_deny(self):
        gate = ApprovalGate(
            policy=CallbackApprovalPolicy(callback=lambda req: True)
        )
        req = gate.request("t", {})
        req.status = "pending"
        req.resolved_at = None
        gate._requests[req.request_id] = req

        denied = gate.deny(req.request_id, reason="actually no")
        assert denied.denied is True
        assert denied.reason == "actually no"

    def test_approve_non_pending_raises(self, gate_approve):
        req = gate_approve.request("t", {})  # already approved
        with pytest.raises(ValueError, match="status is"):
            gate_approve.approve(req.request_id)

    def test_deny_non_pending_raises(self, gate_approve):
        req = gate_approve.request("t", {})  # already approved
        with pytest.raises(ValueError, match="status is"):
            gate_approve.deny(req.request_id)

    def test_approve_unknown_id_raises(self, gate_approve):
        with pytest.raises(KeyError):
            gate_approve.approve("ghost-id")

    def test_pending_returns_only_pending(self, gate_approve):
        gate_approve.request("auto", {})  # approved immediately
        assert gate_approve.pending() == []

    def test_thread_safety(self, gate_approve):
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(20):
                    gate_approve.request("tool", {})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(gate_approve.all_requests()) == 100
