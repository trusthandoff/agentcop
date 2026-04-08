"""
SentinelEvent factory functions for reliability-system events.

Five event types are defined here:

- ``reliability_drift_detected``  — metric crossed the drift threshold
- ``retry_explosion``             — retry count spiked to dangerous levels
- ``branch_instability_critical`` — agent branch paths are highly unstable
- ``tool_variance_spike``         — tool usage variance exceeded threshold
- ``token_budget_spike``          — token consumption spiked above baseline

All factories return a :class:`~agentcop.event.SentinelEvent` ready to push
into a :class:`~agentcop.Sentinel`.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from agentcop.event import SentinelEvent

_SOURCE_SYSTEM = "agentcop.reliability"


def _make_event(
    event_type: str,
    severity: str,
    producer_id: str,
    body: str,
    attributes: dict[str, Any],
    trace_id: str = "",
) -> SentinelEvent:
    return SentinelEvent(
        event_id=f"reliability-{event_type}-{uuid.uuid4()}",
        event_type=event_type,
        timestamp=datetime.now(UTC),
        severity=severity,
        producer_id=producer_id,
        body=body,
        source_system=_SOURCE_SYSTEM,
        trace_id=trace_id,
        attributes=attributes,
    )


def reliability_drift_detected(
    agent_id: str,
    *,
    metric: str,
    before: float,
    after: float,
    window_hours: int = 24,
    trace_id: str = "",
) -> SentinelEvent:
    """Emit when a reliability metric crossed the drift threshold.

    Args:
        agent_id:     ID of the agent whose metric drifted.
        metric:       Name of the drifted metric (e.g. ``"path_entropy"``).
        before:       Metric value in the earlier half of the window.
        after:        Metric value in the later half of the window.
        window_hours: Width of the analysis window in hours.
        trace_id:     Optional trace correlation ID.

    Returns:
        A ``WARN``-severity :class:`~agentcop.event.SentinelEvent`.
    """
    change_pct = ((after - before) / before * 100.0) if before != 0.0 else float("inf")
    body = (
        f"Reliability drift detected for agent '{agent_id}': "
        f"{metric} changed from {before:.3f} to {after:.3f} "
        f"({change_pct:+.1f}%) over the last {window_hours}h window."
    )
    return _make_event(
        "reliability_drift_detected",
        "WARN",
        agent_id,
        body,
        {
            "agent_id": agent_id,
            "metric": metric,
            "before": before,
            "after": after,
            "change_pct": round(change_pct, 2),
            "window_hours": window_hours,
        },
        trace_id=trace_id,
    )


def retry_explosion(
    agent_id: str,
    *,
    run_id: str,
    retry_count: int,
    threshold: int = 10,
    tool_name: str = "",
    trace_id: str = "",
) -> SentinelEvent:
    """Emit when a single run's retry count exceeded the critical threshold.

    Args:
        agent_id:    ID of the offending agent.
        run_id:      ID of the specific run that exploded.
        retry_count: Actual retry count observed.
        threshold:   Threshold that was exceeded.
        tool_name:   Tool that caused the retries, if known.
        trace_id:    Optional trace correlation ID.

    Returns:
        An ``ERROR``-severity :class:`~agentcop.event.SentinelEvent`.
    """
    tool_clause = f" on tool '{tool_name}'" if tool_name else ""
    body = (
        f"Retry explosion for agent '{agent_id}'{tool_clause}: "
        f"{retry_count} retries in run '{run_id}' "
        f"(threshold: {threshold})."
    )
    return _make_event(
        "retry_explosion",
        "ERROR",
        agent_id,
        body,
        {
            "agent_id": agent_id,
            "run_id": run_id,
            "retry_count": retry_count,
            "threshold": threshold,
            "tool_name": tool_name,
        },
        trace_id=trace_id,
    )


def branch_instability_critical(
    agent_id: str,
    *,
    instability_score: float,
    threshold: float = 0.8,
    window_runs: int = 0,
    trace_id: str = "",
) -> SentinelEvent:
    """Emit when branch instability crossed the critical threshold.

    Args:
        agent_id:          ID of the agent.
        instability_score: Normalized branch instability (0-1).
        threshold:         Threshold that was exceeded (default 0.8).
        window_runs:       Number of runs in the analysis window.
        trace_id:          Optional trace correlation ID.

    Returns:
        An ``ERROR``-severity :class:`~agentcop.event.SentinelEvent`.
    """
    body = (
        f"Critical branch instability for agent '{agent_id}': "
        f"score {instability_score:.3f} exceeds threshold {threshold:.3f} "
        f"(measured over {window_runs} runs)."
    )
    return _make_event(
        "branch_instability_critical",
        "ERROR",
        agent_id,
        body,
        {
            "agent_id": agent_id,
            "instability_score": instability_score,
            "threshold": threshold,
            "window_runs": window_runs,
        },
        trace_id=trace_id,
    )


def tool_variance_spike(
    agent_id: str,
    *,
    tool_variance: float,
    baseline_variance: float,
    spike_factor: float,
    window_runs: int = 0,
    trace_id: str = "",
) -> SentinelEvent:
    """Emit when tool-usage variance spiked above the baseline.

    Args:
        agent_id:          ID of the agent.
        tool_variance:     Current normalized tool variance (0-1).
        baseline_variance: Variance in the baseline period.
        spike_factor:      How many times baseline the current variance is.
        window_runs:       Number of runs in the analysis window.
        trace_id:          Optional trace correlation ID.

    Returns:
        A ``WARN``-severity :class:`~agentcop.event.SentinelEvent`.
    """
    body = (
        f"Tool variance spike for agent '{agent_id}': "
        f"variance {tool_variance:.3f} is {spike_factor:.1f}× the baseline "
        f"{baseline_variance:.3f} (window: {window_runs} runs)."
    )
    return _make_event(
        "tool_variance_spike",
        "WARN",
        agent_id,
        body,
        {
            "agent_id": agent_id,
            "tool_variance": tool_variance,
            "baseline_variance": baseline_variance,
            "spike_factor": spike_factor,
            "window_runs": window_runs,
        },
        trace_id=trace_id,
    )


def token_budget_spike(
    agent_id: str,
    *,
    run_id: str,
    total_tokens: int,
    baseline_tokens: float,
    spike_factor: float,
    trace_id: str = "",
) -> SentinelEvent:
    """Emit when a run's token consumption spiked above the baseline.

    Args:
        agent_id:        ID of the agent.
        run_id:          ID of the specific run.
        total_tokens:    Tokens consumed in this run.
        baseline_tokens: Baseline average tokens per run.
        spike_factor:    How many times baseline the current usage is.
        trace_id:        Optional trace correlation ID.

    Returns:
        A ``WARN``-severity :class:`~agentcop.event.SentinelEvent`.
    """
    body = (
        f"Token budget spike for agent '{agent_id}' in run '{run_id}': "
        f"{total_tokens:,} tokens used ({spike_factor:.1f}× baseline of "
        f"{baseline_tokens:,.0f})."
    )
    return _make_event(
        "token_budget_spike",
        "WARN",
        agent_id,
        body,
        {
            "agent_id": agent_id,
            "run_id": run_id,
            "total_tokens": total_tokens,
            "baseline_tokens": baseline_tokens,
            "spike_factor": spike_factor,
        },
        trace_id=trace_id,
    )
