"""Tests for agentcop.trust.observability — TrustObserver."""
from __future__ import annotations

import time

from agentcop.trust.models import TrustChain, TrustClaim
from agentcop.trust.observability import TrustObserver


def _claim(agent_id: str = "agent-x", signed: bool = False) -> TrustClaim:
    return TrustClaim(
        claim_id="claim-001",
        agent_id=agent_id,
        claim_type="execution",
        payload_hash="a" * 64,
        issuer_id="issuer-1",
        timestamp=time.time(),
        signature="sig" if signed else None,
    )


def _chain(verified: bool = True, n_claims: int = 2) -> TrustChain:
    claims = [_claim(f"agent-{i}") for i in range(n_claims)]
    return TrustChain(
        chain_id="chain-001",
        root_claim_id=claims[0].claim_id if claims else "",
        claims=claims,
        verified=verified,
        broken_at=None if verified else claims[-1].claim_id,
    )


class TestToOtelSpan:
    def test_contains_claim_id(self):
        obs = TrustObserver()
        c = _claim()
        attrs = obs.to_otel_span(c)
        assert attrs["trust.claim_id"] == c.claim_id

    def test_contains_agent_id(self):
        obs = TrustObserver()
        attrs = obs.to_otel_span(_claim("special"))
        assert attrs["trust.agent_id"] == "special"

    def test_contains_claim_type(self):
        obs = TrustObserver()
        attrs = obs.to_otel_span(_claim())
        assert attrs["trust.claim_type"] == "execution"

    def test_signed_flag_false_when_unsigned(self):
        obs = TrustObserver()
        attrs = obs.to_otel_span(_claim(signed=False))
        assert attrs["trust.signed"] is False

    def test_signed_flag_true_when_signed(self):
        obs = TrustObserver()
        attrs = obs.to_otel_span(_claim(signed=True))
        assert attrs["trust.signed"] is True

    def test_contains_payload_hash(self):
        obs = TrustObserver()
        attrs = obs.to_otel_span(_claim())
        assert "trust.payload_hash" in attrs


class TestToLangSmithRun:
    def test_contains_chain_id(self):
        obs = TrustObserver()
        run = obs.to_langsmith_run(_chain())
        assert run["id"] == "chain-001"

    def test_contains_verified_status(self):
        obs = TrustObserver()
        run = obs.to_langsmith_run(_chain(verified=True))
        assert run["outputs"]["verified"] is True

    def test_contains_claim_count(self):
        obs = TrustObserver()
        run = obs.to_langsmith_run(_chain(n_claims=3))
        assert run["outputs"]["claim_count"] == 3

    def test_run_type_is_chain(self):
        obs = TrustObserver()
        run = obs.to_langsmith_run(_chain())
        assert run["run_type"] == "chain"

    def test_empty_chain_has_times(self):
        obs = TrustObserver()
        chain = TrustChain(chain_id="c", root_claim_id="", claims=[], verified=True)
        run = obs.to_langsmith_run(chain)
        assert "start_time" in run
        assert "end_time" in run


class TestToDatadogTrace:
    def test_contains_traces_key(self):
        obs = TrustObserver()
        result = obs.to_datadog_trace(_chain())
        assert "traces" in result

    def test_verified_chain_error_zero(self):
        obs = TrustObserver()
        result = obs.to_datadog_trace(_chain(verified=True))
        spans = result["traces"][0]
        assert all(s["error"] == 0 for s in spans)

    def test_unverified_chain_error_one(self):
        obs = TrustObserver()
        result = obs.to_datadog_trace(_chain(verified=False))
        spans = result["traces"][0]
        assert all(s["error"] == 1 for s in spans)

    def test_span_has_service(self):
        obs = TrustObserver()
        result = obs.to_datadog_trace(_chain())
        span = result["traces"][0][0]
        assert span["service"] == "agentcop.trust"


class TestPrometheusMetrics:
    def test_prometheus_format_returns_string(self):
        obs = TrustObserver()
        metrics = obs.to_prometheus_metrics()
        assert isinstance(metrics, str)

    def test_contains_verified_metric(self):
        obs = TrustObserver()
        metrics = obs.to_prometheus_metrics()
        assert "trust_chain_verified_total" in metrics

    def test_contains_delegation_violations_metric(self):
        obs = TrustObserver()
        metrics = obs.to_prometheus_metrics()
        assert "delegation_violations_total" in metrics

    def test_contains_boundary_violations_metric(self):
        obs = TrustObserver()
        metrics = obs.to_prometheus_metrics()
        assert "boundary_violations_total" in metrics

    def test_record_verified_chain_increments(self):
        obs = TrustObserver()
        obs.record_verified_chain()
        obs.record_verified_chain()
        metrics = obs.to_prometheus_metrics()
        assert "trust_chain_verified_total 2" in metrics

    def test_record_delegation_violation_increments(self):
        obs = TrustObserver()
        obs.record_delegation_violation()
        metrics = obs.to_prometheus_metrics()
        assert "delegation_violations_total 1" in metrics

    def test_record_boundary_violation_increments(self):
        obs = TrustObserver()
        obs.record_boundary_violation()
        metrics = obs.to_prometheus_metrics()
        assert "boundary_violations_total 1" in metrics


class TestWebhook:
    def test_no_url_returns_false(self):
        obs = TrustObserver(webhook_url=None)
        result = obs.send_webhook({"event": "test"})
        assert result is False

    def test_empty_url_returns_false(self):
        obs = TrustObserver(webhook_url="")
        result = obs.send_webhook({"event": "test"})
        assert result is False
