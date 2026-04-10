"""
TrustObserver — export trust data to external observability platforms.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .models import TrustChain, TrustClaim

_log = logging.getLogger(__name__)


class TrustObserver:
    """
    Exports TrustClaims and TrustChains to external observability systems.

    Supported outputs:
    - OpenTelemetry span attributes
    - LangSmith run format
    - Datadog trace format
    - Prometheus text metrics
    - Webhook (any HTTP endpoint)

    Thread-safe counters for Prometheus metrics.
    """

    def __init__(self, webhook_url: str | None = None) -> None:
        self._webhook_url = webhook_url
        self._lock = threading.Lock()
        self._verified_count: int = 0
        self._delegation_violations: int = 0
        self._boundary_violations: int = 0

    # ------------------------------------------------------------------
    # Per-claim / per-chain export
    # ------------------------------------------------------------------

    def to_otel_span(self, claim: TrustClaim) -> dict[str, Any]:
        """Return OpenTelemetry span attributes for a TrustClaim."""
        return {
            "trust.claim_id": claim.claim_id,
            "trust.agent_id": claim.agent_id,
            "trust.claim_type": claim.claim_type,
            "trust.payload_hash": claim.payload_hash,
            "trust.issuer_id": claim.issuer_id,
            "trust.timestamp": claim.timestamp,
            "trust.signed": claim.signature is not None,
        }

    def to_langsmith_run(self, chain: TrustChain) -> dict[str, Any]:
        """Return a LangSmith run-format dict for a TrustChain."""
        start = chain.claims[0].timestamp if chain.claims else time.time()
        end = chain.claims[-1].timestamp if chain.claims else time.time()
        return {
            "id": chain.chain_id,
            "name": f"trust_chain_{chain.chain_id[:8]}",
            "run_type": "chain",
            "start_time": start,
            "end_time": end,
            "inputs": {"root_claim_id": chain.root_claim_id},
            "outputs": {
                "verified": chain.verified,
                "broken_at": chain.broken_at,
                "claim_count": len(chain.claims),
            },
            "extra": {"trust_chain": True},
        }

    def to_datadog_trace(self, chain: TrustChain) -> dict[str, Any]:
        """Return a Datadog trace-format dict for a TrustChain."""
        trace_id = chain.chain_id.replace("-", "")[:16]
        spans = []
        for claim in chain.claims:
            spans.append({
                "trace_id": trace_id,
                "span_id": claim.claim_id.replace("-", "")[:16],
                "name": f"trust.{claim.claim_type}",
                "service": "agentcop.trust",
                "resource": claim.agent_id,
                "start": int(claim.timestamp * 1e9),  # nanoseconds
                "duration": 0,
                "meta": {
                    "trust.verified": str(chain.verified),
                    "trust.claim_type": claim.claim_type,
                    "trust.payload_hash": claim.payload_hash[:16],
                },
                "error": 0 if chain.verified else 1,
            })
        return {"traces": [spans]}

    # ------------------------------------------------------------------
    # Prometheus
    # ------------------------------------------------------------------

    def to_prometheus_metrics(self) -> str:
        """Return Prometheus text-format metrics string."""
        with self._lock:
            verified = self._verified_count
            deleg = self._delegation_violations
            boundary = self._boundary_violations

        lines = [
            "# HELP trust_chain_verified_total Total number of verified trust chains",
            "# TYPE trust_chain_verified_total counter",
            f"trust_chain_verified_total {verified}",
            "# HELP delegation_violations_total Total number of delegation violations",
            "# TYPE delegation_violations_total counter",
            f"delegation_violations_total {deleg}",
            "# HELP boundary_violations_total Total number of boundary violations",
            "# TYPE boundary_violations_total counter",
            f"boundary_violations_total {boundary}",
        ]
        return "\n".join(lines)

    def record_verified_chain(self) -> None:
        """Increment the verified-chain counter."""
        with self._lock:
            self._verified_count += 1

    def record_delegation_violation(self) -> None:
        """Increment the delegation-violation counter."""
        with self._lock:
            self._delegation_violations += 1

    def record_boundary_violation(self) -> None:
        """Increment the boundary-violation counter."""
        with self._lock:
            self._boundary_violations += 1

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------

    def send_webhook(self, payload: dict[str, Any]) -> bool:
        """
        POST a JSON payload to the configured webhook URL.

        Returns True on success (HTTP status < 400), False on any failure.
        Failures are logged at DEBUG level and never raise.
        """
        if not self._webhook_url:
            return False
        try:
            body = json.dumps(payload).encode()
            req = Request(
                self._webhook_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=5) as resp:
                return resp.status < 400
        except (URLError, OSError) as exc:
            _log.debug("Webhook delivery failed: %s", exc)
            return False
