"""
Reliability metrics engine for agentcop.

Each calculator is a stateless class whose primary method accepts a list of
AgentRun records and returns a normalised float (0-1) unless noted otherwise.

None of the calculators perform I/O.  TokenBudgetAnalyzer and DriftDetector
return SentinelEvent objects for the caller to push into a Sentinel instance.
"""

import math
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from agentcop.event import SentinelEvent

from .models import AgentRun, ReliabilityReport

_SOURCE_SYSTEM = "agentcop.reliability"


# ---------------------------------------------------------------------------
# PathEntropyCalculator
# ---------------------------------------------------------------------------


class PathEntropyCalculator:
    """
    Shannon entropy over execution_path sequences.

    Each unique path tuple is treated as a symbol.  The entropy is normalised
    by log2(n) so the output is in [0, 1]:
      0 → every run follows the same path
      1 → every run follows a unique path
    """

    def calculate(self, runs: list[AgentRun]) -> float:
        if len(runs) < 2:
            return 0.0
        path_tuples = [tuple(r.execution_path) for r in runs]
        counts = Counter(path_tuples)
        if len(counts) == 1:
            return 0.0
        n = len(path_tuples)
        entropy = -sum((c / n) * math.log2(c / n) for c in counts.values())
        max_entropy = math.log2(n)
        if max_entropy == 0.0:
            return 0.0
        return min(entropy / max_entropy, 1.0)


# ---------------------------------------------------------------------------
# ToolVarianceCalculator
# ---------------------------------------------------------------------------


class ToolVarianceCalculator:
    """
    Coefficient of variation (std/mean) on tool call frequency per tool_name,
    averaged across all tools seen in the window.

    Output: float 0-1 (0=perfectly consistent, 1=highly variable).
    CV values are capped at 2.0 before normalising so that extreme outliers
    do not push the score above 1.
    """

    def calculate(self, runs: list[AgentRun]) -> float:
        if not runs:
            return 0.0
        all_tools: set[str] = {tc.tool_name for run in runs for tc in run.tool_calls}
        if not all_tools:
            return 0.0
        n = len(runs)
        cvs: list[float] = []
        for tool in all_tools:
            counts = [sum(1 for tc in run.tool_calls if tc.tool_name == tool) for run in runs]
            mean = sum(counts) / n
            if mean == 0.0:
                continue
            variance = sum((c - mean) ** 2 for c in counts) / n
            std = math.sqrt(variance)
            # Normalise: cap raw CV at 2.0, then scale to [0, 1]
            cvs.append(min(std / mean, 2.0) / 2.0)
        if not cvs:
            return 0.0
        return sum(cvs) / len(cvs)


# ---------------------------------------------------------------------------
# RetryExplosionDetector
# ---------------------------------------------------------------------------


@dataclass
class ExplosionEvent:
    """A single retry-explosion observation within a run or tool call."""

    run_id: str
    agent_id: str
    timestamp: datetime
    retry_count: int
    severity: str  # "WARNING" | "CRITICAL"
    source: str  # "run" | "tool:<name>"


class RetryExplosionDetector:
    """
    Detects excessive retries at the run level and per tool call.

    The explosion score is a weighted fraction of runs that breached thresholds:
      - warning_threshold (default 3)  → weight 0.5
      - critical_threshold (default 10) → weight 1.0

    Retry velocity is also tracked: if the second half of the window has a
    substantially higher retry average than the first half, the score is
    inflated by up to 30 % to signal an accelerating pattern.

    Output: (float 0-1, list[ExplosionEvent])
    """

    def __init__(self, warning_threshold: int = 3, critical_threshold: int = 10) -> None:
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold

    def calculate(self, runs: list[AgentRun]) -> tuple[float, list[ExplosionEvent]]:
        if not runs:
            return 0.0, []

        explosion_events: list[ExplosionEvent] = []
        weighted_score = 0.0

        for run in runs:
            if run.retry_count >= self.warning_threshold:
                sev = "CRITICAL" if run.retry_count >= self.critical_threshold else "WARNING"
                explosion_events.append(
                    ExplosionEvent(
                        run_id=run.run_id,
                        agent_id=run.agent_id,
                        timestamp=run.timestamp,
                        retry_count=run.retry_count,
                        severity=sev,
                        source="run",
                    )
                )
                weighted_score += 1.0 if run.retry_count >= self.critical_threshold else 0.5

            for tc in run.tool_calls:
                if tc.retry_count >= self.warning_threshold:
                    sev = "CRITICAL" if tc.retry_count >= self.critical_threshold else "WARNING"
                    explosion_events.append(
                        ExplosionEvent(
                            run_id=run.run_id,
                            agent_id=run.agent_id,
                            timestamp=run.timestamp,
                            retry_count=tc.retry_count,
                            severity=sev,
                            source=f"tool:{tc.tool_name}",
                        )
                    )

        score = min(weighted_score / len(runs), 1.0)

        # Velocity check: if retries are accelerating, inflate score by up to 30 %
        sorted_runs = sorted(runs, key=lambda r: r.timestamp)
        if len(sorted_runs) >= 4:
            mid = len(sorted_runs) // 2
            early_avg = sum(r.retry_count for r in sorted_runs[:mid]) / mid
            recent_avg = sum(r.retry_count for r in sorted_runs[mid:]) / (len(sorted_runs) - mid)
            if early_avg < 1.0 and recent_avg >= self.warning_threshold:
                score = min(score + 0.3, 1.0)
            elif early_avg > 0 and recent_avg > 2 * early_avg:
                score = min(score * 1.3, 1.0)

        return score, explosion_events


# ---------------------------------------------------------------------------
# BranchInstabilityAnalyzer
# ---------------------------------------------------------------------------


class BranchInstabilityAnalyzer:
    """
    Normalised Hamming distance between execution paths of identical inputs.

    Runs are grouped by input_hash.  Within each group all pairs are compared.
    Shorter paths are zero-padded to the length of the longer one before the
    Hamming distance is computed.  The result is the mean distance across all
    pairs, already in [0, 1].

    Output: 0 → same decisions every time, 1 → completely different paths.
    """

    def calculate(self, runs: list[AgentRun]) -> float:
        if len(runs) < 2:
            return 0.0

        groups: dict[str, list[AgentRun]] = defaultdict(list)
        for run in runs:
            groups[run.input_hash].append(run)

        distances: list[float] = []
        for group_runs in groups.values():
            if len(group_runs) < 2:
                continue
            for i in range(len(group_runs)):
                for j in range(i + 1, len(group_runs)):
                    distances.append(
                        self._normalized_hamming(
                            group_runs[i].execution_path,
                            group_runs[j].execution_path,
                        )
                    )

        if not distances:
            return 0.0
        return sum(distances) / len(distances)

    @staticmethod
    def _normalized_hamming(a: list[str], b: list[str]) -> float:
        max_len = max(len(a), len(b), 1)
        a_pad = a + [""] * (max_len - len(a))
        b_pad = b + [""] * (max_len - len(b))
        diffs = sum(1 for x, y in zip(a_pad, b_pad, strict=False) if x != y)
        return diffs / max_len


# ---------------------------------------------------------------------------
# TokenBudgetAnalyzer
# ---------------------------------------------------------------------------


class TokenBudgetAnalyzer:
    """
    Tracks token usage and cost across runs.

    ``spike_multiplier`` (default 3.0): fires a SentinelEvent for every run
    whose total_tokens exceeds ``spike_multiplier × rolling_baseline``.

    Returns a dict with keys:
      baseline_tokens   — rolling mean of total_tokens
      token_variance    — coefficient of variation (std/mean), capped at 1.0
      spike_detected    — True if any run triggered a spike event
      spike_events      — list[SentinelEvent] to push into a Sentinel
      cost_per_run      — mean estimated_cost_usd
      budget_burn_rate  — tokens / minute over the window
    """

    def __init__(self, spike_multiplier: float = 3.0) -> None:
        self.spike_multiplier = spike_multiplier

    def analyze(self, runs: list[AgentRun]) -> dict:
        if not runs:
            return {
                "baseline_tokens": 0.0,
                "token_variance": 0.0,
                "spike_detected": False,
                "spike_events": [],
                "cost_per_run": 0.0,
                "budget_burn_rate": 0.0,
            }

        token_counts = [r.total_tokens for r in runs]
        n = len(token_counts)
        baseline = sum(token_counts) / n

        variance = sum((t - baseline) ** 2 for t in token_counts) / n
        std = math.sqrt(variance)
        token_variance = min(std / baseline, 1.0) if baseline > 0.0 else 0.0

        spike_events: list[SentinelEvent] = []
        for run in runs:
            if baseline > 0.0 and run.total_tokens > self.spike_multiplier * baseline:
                spike_events.append(self._make_spike_event(run, baseline))

        cost_per_run = sum(r.estimated_cost_usd for r in runs) / n

        sorted_runs = sorted(runs, key=lambda r: r.timestamp)
        if len(sorted_runs) >= 2:
            span_seconds = (sorted_runs[-1].timestamp - sorted_runs[0].timestamp).total_seconds()
            burn_rate = (sum(token_counts) / span_seconds * 60.0) if span_seconds > 0.0 else 0.0
        else:
            burn_rate = 0.0

        return {
            "baseline_tokens": baseline,
            "token_variance": token_variance,
            "spike_detected": bool(spike_events),
            "spike_events": spike_events,
            "cost_per_run": cost_per_run,
            "budget_burn_rate": burn_rate,
        }

    def _make_spike_event(self, run: AgentRun, baseline: float) -> SentinelEvent:
        multiplier = run.total_tokens / baseline if baseline > 0.0 else 0.0
        return SentinelEvent(
            event_id=f"reliability-token-spike-{run.run_id}",
            event_type="token_budget_spike",
            timestamp=run.timestamp,
            severity="WARN",
            producer_id=run.agent_id,
            trace_id=run.run_id,
            body=(
                f"Token spike for agent '{run.agent_id}': "
                f"{run.total_tokens} tokens = {multiplier:.1f}x baseline ({baseline:.0f})"
            ),
            source_system=_SOURCE_SYSTEM,
            attributes={
                "run_id": run.run_id,
                "agent_id": run.agent_id,
                "total_tokens": run.total_tokens,
                "baseline_tokens": baseline,
                "spike_multiplier": multiplier,
            },
        )


# ---------------------------------------------------------------------------
# ReliabilityScorer
# ---------------------------------------------------------------------------


class ReliabilityScorer:
    """
    Composite scorer that converts four metric floats into a 0-100 reliability
    score and a tier label.

    Weights:
      path_entropy      25 %
      tool_variance     25 %
      retry_explosion   30 %
      branch_instability 20 %

    Tiers:
      STABLE   ≥ 80
      VARIABLE  60-79
      UNSTABLE  40-59
      CRITICAL  < 40
    """

    _WEIGHTS = {
        "path_entropy": 0.25,
        "tool_variance": 0.25,
        "retry_explosion": 0.30,
        "branch_instability": 0.20,
    }

    def score(
        self,
        path_entropy: float,
        tool_variance: float,
        retry_explosion: float,
        branch_instability: float,
    ) -> tuple[int, str]:
        """Return (reliability_score, tier)."""
        penalty = (
            path_entropy * self._WEIGHTS["path_entropy"]
            + tool_variance * self._WEIGHTS["tool_variance"]
            + retry_explosion * self._WEIGHTS["retry_explosion"]
            + branch_instability * self._WEIGHTS["branch_instability"]
        )
        raw = 100.0 - penalty * 100.0
        reliability_score = max(0, min(100, int(round(raw))))

        if reliability_score >= 80:
            tier = "STABLE"
        elif reliability_score >= 60:
            tier = "VARIABLE"
        elif reliability_score >= 40:
            tier = "UNSTABLE"
        else:
            tier = "CRITICAL"

        return reliability_score, tier

    @staticmethod
    def compute_trend(recent_score: int, prior_score: int) -> str:
        """Return IMPROVING / STABLE / DEGRADING based on score delta."""
        delta = recent_score - prior_score
        if delta > 5:
            return "IMPROVING"
        if delta < -5:
            return "DEGRADING"
        return "STABLE"


# ---------------------------------------------------------------------------
# DriftDetector
# ---------------------------------------------------------------------------


class DriftDetector:
    """
    Compares an early and a recent window of runs to detect behavioural drift.

    A drift event is fired as a SentinelEvent whenever a metric has shifted by
    more than ``significance_factor`` (default 2.0) relative to the early window.

    ``window_hours`` is embedded in the human-readable drift description only;
    it does not filter runs by timestamp — callers are responsible for that.

    Returns (drift_detected, description | None, list[SentinelEvent]).
    """

    def __init__(self, window_hours: int = 2, significance_factor: float = 2.0) -> None:
        self.window_hours = window_hours
        self.significance_factor = significance_factor

    def detect(self, runs: list[AgentRun]) -> tuple[bool, str | None, list[SentinelEvent]]:
        if len(runs) < 4:
            return False, None, []

        mid = len(runs) // 2
        prior = runs[:mid]
        recent = runs[mid:]

        descriptions: list[str] = []
        sentinel_events: list[SentinelEvent] = []
        latest_run = runs[-1]

        tool_calc = ToolVarianceCalculator()
        prior_tv = tool_calc.calculate(prior)
        recent_tv = tool_calc.calculate(recent)
        if prior_tv > 0.0 and recent_tv > self.significance_factor * prior_tv:
            factor = recent_tv / prior_tv
            desc = f"tool_variance increased {factor:.1f}x in last {self.window_hours}h"
            descriptions.append(desc)
            sentinel_events.append(self._make_drift_event(latest_run, "tool_variance_drift", desc))

        retry_det = RetryExplosionDetector()
        prior_retry, _ = retry_det.calculate(prior)
        recent_retry, _ = retry_det.calculate(recent)
        if prior_retry < 0.1 and recent_retry >= 0.3:
            desc = f"retry rate spiked to {recent_retry:.0%} in last {self.window_hours}h"
            descriptions.append(desc)
            sentinel_events.append(self._make_drift_event(latest_run, "retry_drift", desc))
        elif prior_retry > 0.0 and recent_retry > self.significance_factor * prior_retry:
            factor = recent_retry / prior_retry
            desc = f"retry_rate increased {factor:.1f}x in last {self.window_hours}h"
            descriptions.append(desc)
            sentinel_events.append(self._make_drift_event(latest_run, "retry_drift", desc))

        path_calc = PathEntropyCalculator()
        prior_pe = path_calc.calculate(prior)
        recent_pe = path_calc.calculate(recent)
        if prior_pe < 0.1 and recent_pe >= 0.3:
            desc = f"execution path became chaotic in last {self.window_hours}h"
            descriptions.append(desc)
            sentinel_events.append(self._make_drift_event(latest_run, "path_entropy_drift", desc))
        elif prior_pe > 0.0 and recent_pe > self.significance_factor * prior_pe:
            factor = recent_pe / prior_pe
            desc = f"path_entropy increased {factor:.1f}x in last {self.window_hours}h"
            descriptions.append(desc)
            sentinel_events.append(self._make_drift_event(latest_run, "path_entropy_drift", desc))

        if not descriptions:
            return False, None, []
        return True, "; ".join(descriptions), sentinel_events

    @staticmethod
    def _make_drift_event(run: AgentRun, drift_type: str, description: str) -> SentinelEvent:
        return SentinelEvent(
            event_id=f"reliability-drift-{uuid.uuid4()}",
            event_type="reliability_drift",
            timestamp=datetime.now(UTC),
            severity="WARN",
            producer_id=run.agent_id,
            body=description,
            source_system=_SOURCE_SYSTEM,
            attributes={
                "agent_id": run.agent_id,
                "drift_type": drift_type,
                "description": description,
            },
        )


# ---------------------------------------------------------------------------
# ReliabilityEngine
# ---------------------------------------------------------------------------


class ReliabilityEngine:
    """
    Orchestrates all metrics calculators and produces a ReliabilityReport.

    Also collects any SentinelEvents fired by TokenBudgetAnalyzer (token spikes)
    and DriftDetector (behavioural drift).  The caller is responsible for pushing
    those events into a :class:`~agentcop.Sentinel` instance if desired::

        engine = ReliabilityEngine()
        report, events = engine.compute_report("my-agent", runs)
        for e in events:
            sentinel.push(e)

    Parameters
    ----------
    retry_warning_threshold:
        Run-level retry_count that triggers a WARNING explosion event (default 3).
    retry_critical_threshold:
        Run-level retry_count that triggers a CRITICAL explosion event (default 10).
    token_spike_multiplier:
        Multiplier above baseline that fires a token-spike SentinelEvent (default 3.0).
    drift_significance_factor:
        Ratio by which a metric must increase to be flagged as drift (default 2.0).
    window_hours:
        Embedded in the report as metadata; does not filter runs (default 24).
    """

    def __init__(
        self,
        retry_warning_threshold: int = 3,
        retry_critical_threshold: int = 10,
        token_spike_multiplier: float = 3.0,
        drift_significance_factor: float = 2.0,
        window_hours: int = 24,
    ) -> None:
        self._path_calc = PathEntropyCalculator()
        self._tool_calc = ToolVarianceCalculator()
        self._retry_det = RetryExplosionDetector(retry_warning_threshold, retry_critical_threshold)
        self._branch_calc = BranchInstabilityAnalyzer()
        self._token_analyzer = TokenBudgetAnalyzer(token_spike_multiplier)
        self._drift_detector = DriftDetector(window_hours, drift_significance_factor)
        self._scorer = ReliabilityScorer()
        self._window_hours = window_hours

    def compute_report(
        self,
        agent_id: str,
        runs: list[AgentRun],
    ) -> tuple[ReliabilityReport, list[SentinelEvent]]:
        """
        Compute a ReliabilityReport from a list of AgentRun records.

        Returns ``(report, sentinel_events)`` where ``sentinel_events`` are any
        token-spike or drift SentinelEvents produced during analysis.
        """
        sentinel_events: list[SentinelEvent] = []

        path_entropy = self._path_calc.calculate(runs)
        tool_variance = self._tool_calc.calculate(runs)
        retry_score, _ = self._retry_det.calculate(runs)
        branch_instability = self._branch_calc.calculate(runs)

        token_analysis = self._token_analyzer.analyze(runs)
        sentinel_events.extend(token_analysis["spike_events"])

        drift_detected, drift_description, drift_events = self._drift_detector.detect(runs)
        sentinel_events.extend(drift_events)

        reliability_score, tier = self._scorer.score(
            path_entropy, tool_variance, retry_score, branch_instability
        )

        trend = self._compute_trend(runs)
        top_issues = self._build_top_issues(
            path_entropy,
            tool_variance,
            retry_score,
            branch_instability,
            token_analysis["spike_detected"],
            drift_detected,
            drift_description,
        )

        report = ReliabilityReport(
            agent_id=agent_id,
            window_runs=len(runs),
            window_hours=self._window_hours,
            path_entropy=path_entropy,
            tool_variance=tool_variance,
            retry_explosion_score=retry_score,
            branch_instability=branch_instability,
            reliability_score=reliability_score,
            reliability_tier=tier,
            drift_detected=drift_detected,
            drift_description=drift_description,
            top_issues=top_issues,
            trend=trend,
            tokens_per_run_avg=token_analysis["baseline_tokens"],
            cost_per_run_avg=token_analysis["cost_per_run"],
            token_spike_detected=token_analysis["spike_detected"],
        )
        return report, sentinel_events

    def _compute_score_for_runs(self, runs: list[AgentRun]) -> int:
        pe = self._path_calc.calculate(runs)
        tv = self._tool_calc.calculate(runs)
        retry, _ = self._retry_det.calculate(runs)
        bi = self._branch_calc.calculate(runs)
        score, _ = self._scorer.score(pe, tv, retry, bi)
        return score

    def _compute_trend(self, runs: list[AgentRun]) -> str:
        if len(runs) >= 20:
            prior = runs[-20:-10]
            recent = runs[-10:]
        elif len(runs) >= 4:
            mid = len(runs) // 2
            prior = runs[:mid]
            recent = runs[mid:]
        else:
            return "STABLE"
        recent_score = self._compute_score_for_runs(recent)
        prior_score = self._compute_score_for_runs(prior)
        return ReliabilityScorer.compute_trend(recent_score, prior_score)

    @staticmethod
    def _build_top_issues(
        path_entropy: float,
        tool_variance: float,
        retry_score: float,
        branch_instability: float,
        token_spike: bool,
        drift_detected: bool,
        drift_description: str | None,
    ) -> list[str]:
        issues: list[str] = []
        if path_entropy > 0.7:
            issues.append(f"High path entropy ({path_entropy:.2f}): execution paths vary widely")
        if tool_variance > 0.7:
            issues.append(f"High tool variance ({tool_variance:.2f}): tool usage is inconsistent")
        if retry_score > 0.5:
            issues.append(f"Retry explosion ({retry_score:.2f}): excessive retries detected")
        if branch_instability > 0.7:
            issues.append(
                f"High branch instability ({branch_instability:.2f}): "
                "same inputs produce different paths"
            )
        if token_spike:
            issues.append("Token spike detected: total_tokens exceeded 3× baseline")
        if drift_detected and drift_description:
            issues.append(f"Drift detected: {drift_description}")
        return issues
