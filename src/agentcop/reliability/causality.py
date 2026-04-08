"""
Causal analysis for reliability metrics.

Identifies correlations between per-run metric values and candidate factors
(time of day, specific tool calls, input source) using Pearson correlation.

No external dependencies — uses only Python stdlib ``statistics`` module.
``statistics.correlation`` is available on Python 3.10+; this project requires
Python 3.11+, so it is always present.
"""

import statistics
from collections import Counter
from dataclasses import dataclass

from .models import AgentRun

_MIN_RUNS = 5  # minimum number of runs needed for correlation analysis


@dataclass
class CausalFinding:
    """A single correlation finding between a metric spike and a contributing factor."""

    metric: str
    factor_type: str  # "time_of_day" | "tool" | "input_source"
    factor_value: str  # "14:00 UTC" | "bash" | "abc12345"
    confidence: float  # |Pearson r|, 0-1
    direction: str  # "positive" | "negative"
    description: str


# ---------------------------------------------------------------------------
# Per-run metric extractors
# ---------------------------------------------------------------------------


def _per_run_tool_variance(run: AgentRun) -> float:
    """Simple intra-run tool variance: stdev of per-tool call counts."""
    if not run.tool_calls:
        return 0.0
    counts = list(Counter(tc.tool_name for tc in run.tool_calls).values())
    if len(counts) < 2:
        return 0.0
    try:
        return statistics.stdev(counts)
    except statistics.StatisticsError:
        return 0.0


_METRIC_FN: dict[str, object] = {
    "tool_variance": _per_run_tool_variance,
    "retry_count": lambda r: float(r.retry_count),
    "total_tokens": lambda r: float(r.total_tokens),
    "duration_ms": lambda r: float(r.duration_ms),
    "success": lambda r: float(r.success),
}


# ---------------------------------------------------------------------------
# Pearson helper
# ---------------------------------------------------------------------------


def _pearson(x: list[float], y: list[float]) -> float:
    """Pearson r, or 0.0 on degenerate input (too few points, zero variance)."""
    if len(x) < 3:
        return 0.0
    try:
        return statistics.correlation(x, y)
    except statistics.StatisticsError:
        return 0.0


# ---------------------------------------------------------------------------
# CausalAnalyzer
# ---------------------------------------------------------------------------


class CausalAnalyzer:
    """
    Correlates per-run metric values with time of day, tool presence,
    and input source using Pearson r.

    Only findings where ``|r| >= min_confidence`` are returned.
    Results are sorted by confidence descending.

    Example::

        analyzer = CausalAnalyzer()
        findings = analyzer.analyze(runs, metric="tool_variance")
        for f in findings:
            print(f.description)
        # → "tool_variance increase correlates with tool 'bash' (confidence: 87%)"
        # → "tool_variance spike at 14:00 UTC (confidence: 72%)"
    """

    def __init__(self, min_confidence: float = 0.4) -> None:
        self.min_confidence = min_confidence

    def analyze(
        self,
        runs: list[AgentRun],
        metric: str = "tool_variance",
    ) -> list[CausalFinding]:
        """Return causal findings for ``metric`` across all runs.

        ``metric`` must be one of: ``tool_variance``, ``retry_count``,
        ``total_tokens``, ``duration_ms``, ``success``.
        """
        if len(runs) < _MIN_RUNS:
            return []
        metric_fn = _METRIC_FN.get(metric)
        if metric_fn is None:
            raise ValueError(f"Unknown metric '{metric}'. Valid: {sorted(_METRIC_FN)}")
        metric_values = [metric_fn(r) for r in runs]  # type: ignore[operator]
        findings: list[CausalFinding] = []
        findings.extend(self._correlate_time_of_day(runs, metric_values, metric))
        findings.extend(self._correlate_tools(runs, metric_values, metric))
        findings.extend(self._correlate_input_sources(runs, metric_values, metric))
        findings.sort(key=lambda f: f.confidence, reverse=True)
        return findings

    # ── Factor correlators ─────────────────────────────────────────────────

    def _correlate_time_of_day(
        self,
        runs: list[AgentRun],
        metric_values: list[float],
        metric: str,
    ) -> list[CausalFinding]:
        hours = [float(r.timestamp.hour) for r in runs]
        r = _pearson(hours, metric_values)
        if abs(r) < self.min_confidence:
            return []
        peak_hour = self._peak_hour(runs, metric_values)
        direction = "positive" if r > 0 else "negative"
        return [
            CausalFinding(
                metric=metric,
                factor_type="time_of_day",
                factor_value=f"{peak_hour:02d}:00 UTC",
                confidence=abs(r),
                direction=direction,
                description=(
                    f"{metric} spike at {peak_hour:02d}:00 UTC (confidence: {abs(r):.0%})"
                ),
            )
        ]

    def _correlate_tools(
        self,
        runs: list[AgentRun],
        metric_values: list[float],
        metric: str,
    ) -> list[CausalFinding]:
        all_tools: set[str] = {tc.tool_name for run in runs for tc in run.tool_calls}
        findings: list[CausalFinding] = []
        for tool in sorted(all_tools):  # sorted for determinism
            presence = [float(any(tc.tool_name == tool for tc in run.tool_calls)) for run in runs]
            if sum(presence) < 2:
                continue
            r = _pearson(presence, metric_values)
            if abs(r) < self.min_confidence:
                continue
            direction = "positive" if r > 0 else "negative"
            verb = "increase" if direction == "positive" else "decrease"
            findings.append(
                CausalFinding(
                    metric=metric,
                    factor_type="tool",
                    factor_value=tool,
                    confidence=abs(r),
                    direction=direction,
                    description=(
                        f"{metric} {verb} correlates with tool '{tool}' (confidence: {abs(r):.0%})"
                    ),
                )
            )
        return findings

    def _correlate_input_sources(
        self,
        runs: list[AgentRun],
        metric_values: list[float],
        metric: str,
    ) -> list[CausalFinding]:
        # Use first 8 chars of input_hash as a routing-source proxy
        source_keys: set[str] = {r.input_hash[:8] for r in runs}
        findings: list[CausalFinding] = []
        for source in sorted(source_keys):
            presence = [float(r.input_hash[:8] == source) for r in runs]
            if sum(presence) < 2:
                continue
            r = _pearson(presence, metric_values)
            if abs(r) < self.min_confidence:
                continue
            direction = "positive" if r > 0 else "negative"
            verb = "increase" if direction == "positive" else "decrease"
            findings.append(
                CausalFinding(
                    metric=metric,
                    factor_type="input_source",
                    factor_value=source,
                    confidence=abs(r),
                    direction=direction,
                    description=(
                        f"{metric} {verb} correlates with inputs from {source} "
                        f"(confidence: {abs(r):.0%})"
                    ),
                )
            )
        return findings

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _peak_hour(runs: list[AgentRun], metric_values: list[float]) -> int:
        """Return the most common hour among above-mean metric runs."""
        if not metric_values:
            return 0
        mean_val = statistics.mean(metric_values)
        above_mean_hours = [
            runs[i].timestamp.hour for i, v in enumerate(metric_values) if v > mean_val
        ]
        if not above_mean_hours:
            return 0
        return Counter(above_mean_hours).most_common(1)[0][0]
