"""Tests for agentcop.reliability — models, metrics, and the full engine."""

import hashlib
import math
from datetime import UTC, datetime, timedelta

import pytest

from agentcop.reliability import (
    AgentRun,
    BranchInstabilityAnalyzer,
    DriftDetector,
    PathEntropyCalculator,
    ReliabilityEngine,
    ReliabilityReport,
    ReliabilityScorer,
    RetryExplosionDetector,
    ToolCall,
    ToolVarianceCalculator,
    TokenBudgetAnalyzer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _run(
    agent_id: str = "agent-1",
    *,
    input_hash: str = "aaa",
    output_hash: str = "bbb",
    execution_path: list[str] | None = None,
    tool_calls: list[ToolCall] | None = None,
    retry_count: int = 0,
    total_tokens: int = 1000,
    input_tokens: int = 500,
    output_tokens: int = 500,
    estimated_cost_usd: float = 0.01,
    duration_ms: int = 100,
    success: bool = True,
    timestamp: datetime | None = None,
    offset_hours: float = 0.0,
) -> AgentRun:
    return AgentRun(
        agent_id=agent_id,
        timestamp=(timestamp or _T0) + timedelta(hours=offset_hours),
        input_hash=input_hash,
        output_hash=output_hash,
        execution_path=execution_path or ["step_a", "step_b"],
        tool_calls=tool_calls or [],
        retry_count=retry_count,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost_usd,
        duration_ms=duration_ms,
        success=success,
    )


def _tool(name: str, retry_count: int = 0, success: bool = True) -> ToolCall:
    return ToolCall(
        tool_name=name,
        args_hash=_sha(name),
        result_hash=_sha(name + "_result"),
        duration_ms=10,
        success=success,
        retry_count=retry_count,
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_agent_run_defaults(self):
        run = _run()
        assert run.run_id  # auto-generated UUID
        assert run.metadata == {}
        assert run.tool_calls == []

    def test_tool_call_fields(self):
        tc = _tool("bash")
        assert tc.tool_name == "bash"
        assert tc.retry_count == 0

    def test_reliability_report_tier_literal(self):
        # Pydantic enforces the Literal constraint
        with pytest.raises(Exception):
            ReliabilityReport(
                agent_id="x",
                window_runs=1,
                window_hours=24,
                path_entropy=0.0,
                tool_variance=0.0,
                retry_explosion_score=0.0,
                branch_instability=0.0,
                reliability_score=90,
                reliability_tier="PERFECT",  # not a valid literal
                drift_detected=False,
                trend="STABLE",
                tokens_per_run_avg=100.0,
                cost_per_run_avg=0.01,
                token_spike_detected=False,
            )

    def test_reliability_report_computed_at_default(self):
        report = ReliabilityReport(
            agent_id="a",
            window_runs=5,
            window_hours=24,
            path_entropy=0.1,
            tool_variance=0.1,
            retry_explosion_score=0.0,
            branch_instability=0.0,
            reliability_score=90,
            reliability_tier="STABLE",
            drift_detected=False,
            trend="STABLE",
            tokens_per_run_avg=500.0,
            cost_per_run_avg=0.005,
            token_spike_detected=False,
        )
        assert report.computed_at is not None


# ---------------------------------------------------------------------------
# PathEntropyCalculator
# ---------------------------------------------------------------------------


class TestPathEntropyCalculator:
    def setup_method(self):
        self.calc = PathEntropyCalculator()

    def test_empty_returns_zero(self):
        assert self.calc.calculate([]) == 0.0

    def test_single_run_returns_zero(self):
        assert self.calc.calculate([_run()]) == 0.0

    def test_identical_paths_return_zero(self):
        runs = [_run(execution_path=["a", "b"]) for _ in range(10)]
        assert self.calc.calculate(runs) == 0.0

    def test_all_unique_paths_near_one(self):
        runs = [_run(execution_path=[f"path_{i}"]) for i in range(8)]
        result = self.calc.calculate(runs)
        assert result > 0.9

    def test_output_bounded_zero_one(self):
        import random
        random.seed(42)
        paths = [["a", "b"], ["a", "c"], ["d"], ["a", "b", "c", "d"]]
        runs = [_run(execution_path=paths[i % len(paths)]) for i in range(20)]
        result = self.calc.calculate(runs)
        assert 0.0 <= result <= 1.0

    def test_two_equal_paths_returns_zero(self):
        runs = [_run(execution_path=["x", "y"]), _run(execution_path=["x", "y"])]
        assert self.calc.calculate(runs) == 0.0

    def test_two_different_paths_returns_one(self):
        runs = [_run(execution_path=["x"]), _run(execution_path=["y"])]
        # log2(2) / log2(2) = 1
        assert self.calc.calculate(runs) == 1.0


# ---------------------------------------------------------------------------
# ToolVarianceCalculator
# ---------------------------------------------------------------------------


class TestToolVarianceCalculator:
    def setup_method(self):
        self.calc = ToolVarianceCalculator()

    def test_empty_returns_zero(self):
        assert self.calc.calculate([]) == 0.0

    def test_no_tool_calls_returns_zero(self):
        runs = [_run(tool_calls=[]) for _ in range(5)]
        assert self.calc.calculate(runs) == 0.0

    def test_identical_tool_usage_returns_zero(self):
        tc = [_tool("bash"), _tool("read")]
        runs = [_run(tool_calls=tc) for _ in range(5)]
        assert self.calc.calculate(runs) == 0.0

    def test_variable_tool_usage_returns_nonzero(self):
        runs = [
            _run(tool_calls=[_tool("bash"), _tool("bash")]),
            _run(tool_calls=[_tool("read")]),
            _run(tool_calls=[_tool("write"), _tool("write"), _tool("write")]),
            _run(tool_calls=[_tool("bash")]),
        ]
        result = self.calc.calculate(runs)
        assert result > 0.0

    def test_output_bounded_zero_one(self):
        runs = [
            _run(tool_calls=[_tool("a")] * i) for i in range(1, 10)
        ]
        result = self.calc.calculate(runs)
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# RetryExplosionDetector
# ---------------------------------------------------------------------------


class TestRetryExplosionDetector:
    def setup_method(self):
        self.det = RetryExplosionDetector(warning_threshold=3, critical_threshold=10)

    def test_empty_returns_zero(self):
        score, events = self.det.calculate([])
        assert score == 0.0
        assert events == []

    def test_no_retries_returns_zero(self):
        runs = [_run(retry_count=0) for _ in range(5)]
        score, events = self.det.calculate(runs)
        assert score == 0.0
        assert events == []

    def test_warning_threshold(self):
        runs = [_run(retry_count=4)]
        score, events = self.det.calculate(runs)
        assert score > 0.0
        assert events[0].severity == "WARNING"

    def test_critical_threshold(self):
        runs = [_run(retry_count=11)]
        score, events = self.det.calculate(runs)
        assert score > 0.0
        assert events[0].severity == "CRITICAL"

    def test_tool_level_retries_detected(self):
        tc = [_tool("bash", retry_count=5)]
        runs = [_run(retry_count=0, tool_calls=tc)]
        _, events = self.det.calculate(runs)
        assert any(e.source.startswith("tool:") for e in events)

    def test_score_bounded_zero_one(self):
        runs = [_run(retry_count=20) for _ in range(10)]
        score, _ = self.det.calculate(runs)
        assert 0.0 <= score <= 1.0

    def test_velocity_inflation_on_accelerating_retries(self):
        # early runs: no retries; recent runs: high retries
        early = [_run(retry_count=0, offset_hours=float(i)) for i in range(4)]
        recent = [_run(retry_count=5, offset_hours=float(4 + i)) for i in range(4)]
        score, _ = self.det.calculate(early + recent)
        # Without velocity inflation the score is 0.5 * 4 / 8 = 0.25
        # With inflation it should be higher
        assert score > 0.25


# ---------------------------------------------------------------------------
# BranchInstabilityAnalyzer
# ---------------------------------------------------------------------------


class TestBranchInstabilityAnalyzer:
    def setup_method(self):
        self.calc = BranchInstabilityAnalyzer()

    def test_empty_returns_zero(self):
        assert self.calc.calculate([]) == 0.0

    def test_single_run_returns_zero(self):
        assert self.calc.calculate([_run()]) == 0.0

    def test_unique_inputs_no_comparison(self):
        # All different input hashes → no pairs to compare
        runs = [_run(input_hash=f"h{i}") for i in range(5)]
        assert self.calc.calculate(runs) == 0.0

    def test_identical_paths_same_input_returns_zero(self):
        runs = [
            _run(input_hash="same", execution_path=["a", "b"]),
            _run(input_hash="same", execution_path=["a", "b"]),
        ]
        assert self.calc.calculate(runs) == 0.0

    def test_different_paths_same_input_returns_nonzero(self):
        runs = [
            _run(input_hash="same", execution_path=["a", "b"]),
            _run(input_hash="same", execution_path=["c", "d"]),
        ]
        result = self.calc.calculate(runs)
        assert result > 0.0

    def test_completely_different_paths_returns_one(self):
        runs = [
            _run(input_hash="x", execution_path=["a", "b"]),
            _run(input_hash="x", execution_path=["c", "d"]),
        ]
        assert self.calc.calculate(runs) == 1.0

    def test_output_bounded_zero_one(self):
        runs = [
            _run(input_hash="x", execution_path=["a", "b", "c"]),
            _run(input_hash="x", execution_path=["a", "b"]),
            _run(input_hash="x", execution_path=["a", "d", "e"]),
        ]
        result = self.calc.calculate(runs)
        assert 0.0 <= result <= 1.0

    def test_different_length_paths_handled(self):
        runs = [
            _run(input_hash="x", execution_path=["a"]),
            _run(input_hash="x", execution_path=["a", "b", "c"]),
        ]
        # Should not raise; extra positions are treated as empty-string mismatch
        result = self.calc.calculate(runs)
        assert 0.0 < result <= 1.0


# ---------------------------------------------------------------------------
# TokenBudgetAnalyzer
# ---------------------------------------------------------------------------


class TestTokenBudgetAnalyzer:
    def setup_method(self):
        self.analyzer = TokenBudgetAnalyzer(spike_multiplier=3.0)

    def test_empty_returns_defaults(self):
        result = self.analyzer.analyze([])
        assert result["baseline_tokens"] == 0.0
        assert result["spike_detected"] is False
        assert result["spike_events"] == []

    def test_no_spike_for_normal_usage(self):
        runs = [_run(total_tokens=1000) for _ in range(5)]
        result = self.analyzer.analyze(runs)
        assert result["spike_detected"] is False
        assert result["spike_events"] == []

    def test_spike_detected(self):
        runs = [_run(total_tokens=100) for _ in range(4)]
        runs.append(_run(total_tokens=10000))  # 10000 > 3 × mean(~2080)
        result = self.analyzer.analyze(runs)
        assert result["spike_detected"] is True
        assert len(result["spike_events"]) >= 1

    def test_spike_event_is_sentinel_event(self):
        from agentcop.event import SentinelEvent
        runs = [_run(total_tokens=100) for _ in range(4)]
        runs.append(_run(total_tokens=1000))
        result = self.analyzer.analyze(runs)
        for e in result["spike_events"]:
            assert isinstance(e, SentinelEvent)
            assert e.event_type == "token_budget_spike"
            assert e.severity == "WARN"

    def test_baseline_is_mean(self):
        runs = [_run(total_tokens=t) for t in [100, 200, 300]]
        result = self.analyzer.analyze(runs)
        assert result["baseline_tokens"] == pytest.approx(200.0)

    def test_cost_per_run_avg(self):
        runs = [_run(estimated_cost_usd=0.02) for _ in range(5)]
        result = self.analyzer.analyze(runs)
        assert result["cost_per_run"] == pytest.approx(0.02)

    def test_burn_rate_computed(self):
        # 3 runs each consuming 1000 tokens spread over 2 hours
        runs = [
            _run(total_tokens=1000, offset_hours=0.0),
            _run(total_tokens=1000, offset_hours=1.0),
            _run(total_tokens=1000, offset_hours=2.0),
        ]
        result = self.analyzer.analyze(runs)
        # Total = 3000 tokens over 120 minutes → 25 tokens/min
        assert result["budget_burn_rate"] == pytest.approx(25.0, rel=0.01)


# ---------------------------------------------------------------------------
# ReliabilityScorer
# ---------------------------------------------------------------------------


class TestReliabilityScorer:
    def setup_method(self):
        self.scorer = ReliabilityScorer()

    def test_perfect_score(self):
        score, tier = self.scorer.score(0.0, 0.0, 0.0, 0.0)
        assert score == 100
        assert tier == "STABLE"

    def test_zero_score(self):
        score, tier = self.scorer.score(1.0, 1.0, 1.0, 1.0)
        assert score == 0
        assert tier == "CRITICAL"

    def test_tier_stable(self):
        _, tier = self.scorer.score(0.1, 0.1, 0.0, 0.0)
        assert tier == "STABLE"

    def test_tier_variable(self):
        # penalty ≈ 0.35 → score ≈ 65
        _, tier = self.scorer.score(0.5, 0.5, 0.2, 0.2)
        assert tier in ("VARIABLE", "UNSTABLE")

    def test_tier_critical(self):
        _, tier = self.scorer.score(0.9, 0.9, 0.9, 0.9)
        assert tier == "CRITICAL"

    def test_score_bounded(self):
        score, _ = self.scorer.score(0.5, 0.5, 0.5, 0.5)
        assert 0 <= score <= 100

    @pytest.mark.parametrize(
        "recent,prior,expected",
        [
            (90, 80, "IMPROVING"),
            (80, 90, "DEGRADING"),
            (80, 82, "STABLE"),
        ],
    )
    def test_compute_trend(self, recent, prior, expected):
        assert ReliabilityScorer.compute_trend(recent, prior) == expected


# ---------------------------------------------------------------------------
# DriftDetector
# ---------------------------------------------------------------------------


class TestDriftDetector:
    def setup_method(self):
        self.det = DriftDetector(window_hours=2, significance_factor=2.0)

    def test_too_few_runs_returns_no_drift(self):
        runs = [_run() for _ in range(3)]
        detected, desc, events = self.det.detect(runs)
        assert detected is False
        assert desc is None
        assert events == []

    def test_stable_runs_no_drift(self):
        runs = [_run(execution_path=["a", "b"], retry_count=0) for _ in range(8)]
        detected, _, _ = self.det.detect(runs)
        assert detected is False

    def test_retry_spike_triggers_drift(self):
        prior = [_run(retry_count=0, offset_hours=float(i)) for i in range(4)]
        recent = [_run(retry_count=5, offset_hours=float(4 + i)) for i in range(4)]
        detected, desc, events = self.det.detect(prior + recent)
        assert detected is True
        assert desc is not None
        assert any(e.event_type == "reliability_drift" for e in events)

    def test_path_entropy_spike_triggers_drift(self):
        prior = [_run(execution_path=["a", "b"]) for _ in range(4)]
        # Recent: all unique paths → high entropy
        recent = [_run(execution_path=[f"path_{i}"]) for i in range(4)]
        detected, desc, events = self.det.detect(prior + recent)
        assert detected is True
        assert events  # sentinel events fired

    def test_drift_events_have_correct_type(self):
        from agentcop.event import SentinelEvent
        prior = [_run(retry_count=0) for _ in range(4)]
        recent = [_run(retry_count=8) for _ in range(4)]
        _, _, events = self.det.detect(prior + recent)
        for e in events:
            assert isinstance(e, SentinelEvent)
            assert e.severity == "WARN"
            assert e.source_system == "agentcop.reliability"


# ---------------------------------------------------------------------------
# ReliabilityEngine (integration)
# ---------------------------------------------------------------------------


class TestReliabilityEngine:
    def setup_method(self):
        self.engine = ReliabilityEngine()

    def test_empty_runs(self):
        report, events = self.engine.compute_report("agent-1", [])
        assert isinstance(report, ReliabilityReport)
        assert report.window_runs == 0
        assert report.reliability_score == 100
        assert report.reliability_tier == "STABLE"
        assert events == []

    def test_stable_agent(self):
        runs = [
            _run(
                agent_id="agent-1",
                execution_path=["a", "b"],
                tool_calls=[_tool("bash"), _tool("read")],
                retry_count=0,
                total_tokens=1000,
                estimated_cost_usd=0.01,
            )
            for _ in range(10)
        ]
        report, events = self.engine.compute_report("agent-1", runs)
        assert report.reliability_tier == "STABLE"
        assert report.reliability_score >= 80
        assert not report.drift_detected
        assert not report.token_spike_detected
        assert events == []

    def test_unstable_agent(self):
        paths = [["a", "b"], ["c", "d"], ["e"], ["f", "g", "h"]]
        runs = [
            _run(
                agent_id="agent-2",
                execution_path=paths[i % len(paths)],
                retry_count=4,
                total_tokens=1000,
            )
            for i in range(10)
        ]
        report, _ = self.engine.compute_report("agent-2", runs)
        assert report.reliability_tier in ("VARIABLE", "UNSTABLE", "CRITICAL")
        assert report.reliability_score < 80

    def test_token_spike_produces_sentinel_events(self):
        runs = [_run(total_tokens=500) for _ in range(9)]
        runs.append(_run(total_tokens=5000))  # spike
        report, events = self.engine.compute_report("agent-3", runs)
        assert report.token_spike_detected is True
        assert any(e.event_type == "token_budget_spike" for e in events)

    def test_report_agent_id_matches(self):
        runs = [_run(agent_id="my-agent")]
        report, _ = self.engine.compute_report("my-agent", runs)
        assert report.agent_id == "my-agent"

    def test_report_window_runs(self):
        runs = [_run() for _ in range(7)]
        report, _ = self.engine.compute_report("a", runs)
        assert report.window_runs == 7

    def test_trend_degrading(self):
        # Prior window: stable; recent window: chaotic paths + retries
        prior = [
            _run(execution_path=["a", "b"], retry_count=0, offset_hours=float(i))
            for i in range(10)
        ]
        recent = [
            _run(
                execution_path=[f"unique_{i}"],
                retry_count=5,
                offset_hours=float(10 + i),
            )
            for i in range(10)
        ]
        report, _ = self.engine.compute_report("a", prior + recent)
        assert report.trend == "DEGRADING"

    def test_top_issues_populated_for_bad_agent(self):
        paths = [["a"], ["b"], ["c"], ["d"], ["e"], ["f"], ["g"], ["h"]]
        runs = [
            _run(execution_path=paths[i % len(paths)], retry_count=5)
            for i in range(8)
        ]
        report, _ = self.engine.compute_report("bad-agent", runs)
        assert len(report.top_issues) > 0

    def test_drift_detected_fires_sentinel_events(self):
        prior = [_run(retry_count=0, offset_hours=float(i)) for i in range(4)]
        recent = [_run(retry_count=7, offset_hours=float(4 + i)) for i in range(4)]
        report, events = self.engine.compute_report("a", prior + recent)
        assert report.drift_detected is True
        assert any(e.event_type == "reliability_drift" for e in events)

    def test_reliability_score_is_int_in_range(self):
        import random
        random.seed(7)
        paths = [["a", "b"], ["c"], ["d", "e", "f"]]
        runs = [
            _run(
                execution_path=paths[i % len(paths)],
                retry_count=random.randint(0, 6),
                total_tokens=random.randint(500, 3000),
            )
            for i in range(15)
        ]
        report, _ = self.engine.compute_report("x", runs)
        assert isinstance(report.reliability_score, int)
        assert 0 <= report.reliability_score <= 100

    def test_branch_instability_same_input_different_paths(self):
        runs = [
            _run(input_hash="same", execution_path=["a", "b"]),
            _run(input_hash="same", execution_path=["c", "d"]),
            _run(input_hash="same", execution_path=["e", "f"]),
        ]
        report, _ = self.engine.compute_report("a", runs)
        assert report.branch_instability > 0.0

    def test_window_hours_propagated(self):
        engine = ReliabilityEngine(window_hours=48)
        report, _ = engine.compute_report("a", [_run()])
        assert report.window_hours == 48
