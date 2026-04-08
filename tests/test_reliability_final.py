"""Tests for Phase 3 reliability components:
events, badge_integration, leaderboard, prometheus, cli, and identity.record_run.
"""

import hashlib
from datetime import UTC, datetime

import pytest

from agentcop.reliability.models import AgentRun, ReliabilityReport, ToolCall

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _run(
    agent_id: str = "agent-1",
    *,
    success: bool = True,
    retry_count: int = 0,
    total_tokens: int = 1000,
    execution_path: list[str] | None = None,
    tool_calls: list[ToolCall] | None = None,
    timestamp: datetime | None = None,
) -> AgentRun:
    return AgentRun(
        agent_id=agent_id,
        timestamp=timestamp or datetime.now(UTC),
        input_hash=_sha("in"),
        output_hash=_sha("out"),
        execution_path=execution_path or ["step_a", "step_b"],
        tool_calls=tool_calls or [],
        duration_ms=100,
        success=success,
        retry_count=retry_count,
        input_tokens=total_tokens // 2,
        output_tokens=total_tokens // 2,
        total_tokens=total_tokens,
        estimated_cost_usd=0.001,
    )


def _report(
    agent_id: str = "a",
    *,
    reliability_score: int = 90,
    reliability_tier: str = "STABLE",
    window_runs: int = 5,
) -> ReliabilityReport:
    return ReliabilityReport(
        agent_id=agent_id,
        window_runs=window_runs,
        window_hours=24,
        path_entropy=0.1,
        tool_variance=0.05,
        retry_explosion_score=0.0,
        branch_instability=0.0,
        reliability_score=reliability_score,
        reliability_tier=reliability_tier,
        drift_detected=False,
        trend="STABLE",
        tokens_per_run_avg=1000.0,
        cost_per_run_avg=0.001,
        token_spike_detected=False,
    )


# ===========================================================================
# events.py
# ===========================================================================


class TestReliabilityDriftDetected:
    def test_returns_sentinel_event(self):
        from agentcop.reliability.events import reliability_drift_detected

        ev = reliability_drift_detected(
            "my-agent", metric="path_entropy", before=0.2, after=0.7, window_hours=24
        )
        assert ev.event_type == "reliability_drift_detected"
        assert ev.severity == "WARN"
        assert ev.producer_id == "my-agent"
        assert ev.source_system == "agentcop.reliability"

    def test_attributes_populated(self):
        from agentcop.reliability.events import reliability_drift_detected

        ev = reliability_drift_detected(
            "a", metric="tool_variance", before=0.1, after=0.5, window_hours=48
        )
        assert ev.attributes["metric"] == "tool_variance"
        assert ev.attributes["before"] == pytest.approx(0.1)
        assert ev.attributes["after"] == pytest.approx(0.5)
        assert ev.attributes["window_hours"] == 48

    def test_change_pct_computed(self):
        from agentcop.reliability.events import reliability_drift_detected

        ev = reliability_drift_detected("a", metric="m", before=0.2, after=0.6)
        assert ev.attributes["change_pct"] == pytest.approx(200.0)

    def test_before_zero_no_exception(self):
        from agentcop.reliability.events import reliability_drift_detected

        ev = reliability_drift_detected("a", metric="m", before=0.0, after=0.5)
        # change_pct should be inf but event should still be produced
        assert ev.event_type == "reliability_drift_detected"

    def test_trace_id_propagated(self):
        from agentcop.reliability.events import reliability_drift_detected

        ev = reliability_drift_detected("a", metric="m", before=0.1, after=0.3, trace_id="tid-1")
        assert ev.trace_id == "tid-1"


class TestRetryExplosion:
    def test_returns_error_severity(self):
        from agentcop.reliability.events import retry_explosion

        ev = retry_explosion("a", run_id="r1", retry_count=15, threshold=10)
        assert ev.severity == "ERROR"
        assert ev.event_type == "retry_explosion"

    def test_attributes(self):
        from agentcop.reliability.events import retry_explosion

        ev = retry_explosion("a", run_id="r1", retry_count=12, threshold=10, tool_name="bash")
        assert ev.attributes["retry_count"] == 12
        assert ev.attributes["tool_name"] == "bash"
        assert ev.attributes["run_id"] == "r1"

    def test_no_tool_name_defaults_empty(self):
        from agentcop.reliability.events import retry_explosion

        ev = retry_explosion("a", run_id="r", retry_count=5)
        assert ev.attributes["tool_name"] == ""


class TestBranchInstabilityCritical:
    def test_returns_error_severity(self):
        from agentcop.reliability.events import branch_instability_critical

        ev = branch_instability_critical("a", instability_score=0.9)
        assert ev.severity == "ERROR"
        assert ev.event_type == "branch_instability_critical"

    def test_threshold_in_attributes(self):
        from agentcop.reliability.events import branch_instability_critical

        ev = branch_instability_critical("a", instability_score=0.85, threshold=0.75)
        assert ev.attributes["threshold"] == pytest.approx(0.75)
        assert ev.attributes["instability_score"] == pytest.approx(0.85)


class TestToolVarianceSpike:
    def test_returns_warn_severity(self):
        from agentcop.reliability.events import tool_variance_spike

        ev = tool_variance_spike(
            "a",
            tool_variance=0.8,
            baseline_variance=0.2,
            spike_factor=4.0,
        )
        assert ev.severity == "WARN"
        assert ev.event_type == "tool_variance_spike"

    def test_spike_factor_in_attributes(self):
        from agentcop.reliability.events import tool_variance_spike

        ev = tool_variance_spike(
            "a", tool_variance=0.8, baseline_variance=0.2, spike_factor=4.0, window_runs=10
        )
        assert ev.attributes["spike_factor"] == pytest.approx(4.0)
        assert ev.attributes["window_runs"] == 10


class TestTokenBudgetSpike:
    def test_returns_warn_severity(self):
        from agentcop.reliability.events import token_budget_spike

        ev = token_budget_spike(
            "a",
            run_id="r1",
            total_tokens=10000,
            baseline_tokens=2000.0,
            spike_factor=5.0,
        )
        assert ev.severity == "WARN"
        assert ev.event_type == "token_budget_spike"

    def test_attributes(self):
        from agentcop.reliability.events import token_budget_spike

        ev = token_budget_spike(
            "a", run_id="r1", total_tokens=9000, baseline_tokens=3000.0, spike_factor=3.0
        )
        assert ev.attributes["total_tokens"] == 9000
        assert ev.attributes["baseline_tokens"] == pytest.approx(3000.0)

    def test_trace_id_propagated(self):
        from agentcop.reliability.events import token_budget_spike

        ev = token_budget_spike(
            "a",
            run_id="r",
            total_tokens=1,
            baseline_tokens=1.0,
            spike_factor=1.0,
            trace_id="trace-xyz",
        )
        assert ev.trace_id == "trace-xyz"


# ===========================================================================
# badge_integration.py
# ===========================================================================


class TestReliabilityEmoji:
    def test_stable_green(self):
        from agentcop.reliability.badge_integration import reliability_emoji

        assert reliability_emoji("STABLE") == "🟢"

    def test_variable_yellow(self):
        from agentcop.reliability.badge_integration import reliability_emoji

        assert reliability_emoji("VARIABLE") == "🟡"

    def test_unstable_orange(self):
        from agentcop.reliability.badge_integration import reliability_emoji

        assert reliability_emoji("UNSTABLE") == "🟠"

    def test_critical_red(self):
        from agentcop.reliability.badge_integration import reliability_emoji

        assert reliability_emoji("CRITICAL") == "🔴"

    def test_unknown_fallback(self):
        from agentcop.reliability.badge_integration import reliability_emoji

        assert reliability_emoji("BOGUS") == "❓"


class TestCombinedBadgeText:
    def test_secured_stable(self):
        from agentcop.reliability.badge_integration import combined_badge_text

        text = combined_badge_text(trust_score=94, reliability_score=87, reliability_tier="STABLE")
        assert text == "✅ SECURED 94/100 | 🟢 STABLE 87/100"

    def test_monitored_variable(self):
        from agentcop.reliability.badge_integration import combined_badge_text

        text = combined_badge_text(
            trust_score=60, reliability_score=72, reliability_tier="VARIABLE"
        )
        assert "MONITORED" in text
        assert "VARIABLE" in text

    def test_at_risk_critical(self):
        from agentcop.reliability.badge_integration import combined_badge_text

        text = combined_badge_text(
            trust_score=25, reliability_score=30, reliability_tier="CRITICAL"
        )
        assert "AT RISK" in text
        assert "CRITICAL" in text

    def test_explicit_security_tier_override(self):
        from agentcop.reliability.badge_integration import combined_badge_text

        text = combined_badge_text(
            trust_score=94,
            reliability_score=80,
            reliability_tier="STABLE",
            security_tier="MONITORED",
        )
        assert "MONITORED" in text

    def test_score_rounded(self):
        from agentcop.reliability.badge_integration import combined_badge_text

        text = combined_badge_text(
            trust_score=94.7, reliability_score=87, reliability_tier="STABLE"
        )
        assert "95/100" in text


class TestShieldUrl:
    def test_returns_shields_url(self):
        from agentcop.reliability.badge_integration import reliability_shield_url

        url = reliability_shield_url("my-agent", "STABLE", 87)
        assert url.startswith("https://img.shields.io/badge/")
        assert "87" in url

    def test_markdown_badge_wraps_url(self):
        from agentcop.reliability.badge_integration import reliability_markdown_badge

        md = reliability_markdown_badge("my-agent", "STABLE", 87)
        assert md.startswith("![Reliability](")
        assert md.endswith(")")


# ===========================================================================
# leaderboard.py
# ===========================================================================


class TestLeaderboardEntry:
    def test_percentile_description_100(self):
        from agentcop.reliability.leaderboard import LeaderboardEntry

        entry = LeaderboardEntry(
            rank=1,
            agent_id="a",
            reliability_score=95,
            reliability_tier="STABLE",
            window_runs=10,
            percentile=100.0,
        )
        assert entry.percentile_description == "more reliable than 100% of tracked agents"

    def test_percentile_description_73(self):
        from agentcop.reliability.leaderboard import LeaderboardEntry

        entry = LeaderboardEntry(
            rank=2,
            agent_id="b",
            reliability_score=80,
            reliability_tier="STABLE",
            window_runs=5,
            percentile=73.0,
        )
        assert "73%" in entry.percentile_description


class TestReliabilityLeaderboard:
    def test_rank_reports_sorted_by_score(self):
        from agentcop.reliability.leaderboard import ReliabilityLeaderboard

        reports = [
            _report("a", reliability_score=60),
            _report("b", reliability_score=90),
            _report("c", reliability_score=75),
        ]
        board = ReliabilityLeaderboard()
        rankings = board.rank_reports(reports)
        assert [e.agent_id for e in rankings] == ["b", "c", "a"]
        assert rankings[0].rank == 1
        assert rankings[2].rank == 3

    def test_rank_1_has_100_percentile(self):
        from agentcop.reliability.leaderboard import ReliabilityLeaderboard

        reports = [_report("a", reliability_score=90), _report("b", reliability_score=70)]
        board = ReliabilityLeaderboard()
        rankings = board.rank_reports(reports)
        assert rankings[0].percentile == pytest.approx(100.0)

    def test_last_has_0_percentile(self):
        from agentcop.reliability.leaderboard import ReliabilityLeaderboard

        reports = [_report("a", reliability_score=90), _report("b", reliability_score=70)]
        board = ReliabilityLeaderboard()
        rankings = board.rank_reports(reports)
        assert rankings[-1].percentile == pytest.approx(0.0)

    def test_single_agent_100_percentile(self):
        from agentcop.reliability.leaderboard import ReliabilityLeaderboard

        board = ReliabilityLeaderboard()
        rankings = board.rank_reports([_report("solo", reliability_score=85)])
        assert len(rankings) == 1
        assert rankings[0].percentile == pytest.approx(100.0)

    def test_empty_returns_empty(self):
        from agentcop.reliability.leaderboard import ReliabilityLeaderboard

        board = ReliabilityLeaderboard()
        assert board.rank_reports([]) == []

    def test_summary_contains_agent_ids(self):
        from agentcop.reliability.leaderboard import ReliabilityLeaderboard

        reports = [_report("alpha", reliability_score=90), _report("beta", reliability_score=75)]
        board = ReliabilityLeaderboard()
        rankings = board.rank_reports(reports)
        summary = board.summary(rankings)
        assert "alpha" in summary
        assert "beta" in summary

    def test_rank_requires_store(self):
        from agentcop.reliability.leaderboard import ReliabilityLeaderboard

        board = ReliabilityLeaderboard(store=None)
        with pytest.raises(RuntimeError, match="requires a store"):
            board.rank(["agent-a"])

    def test_rank_with_store(self, tmp_path):
        from agentcop.reliability.leaderboard import ReliabilityLeaderboard
        from agentcop.reliability.store import ReliabilityStore

        store = ReliabilityStore(str(tmp_path / "test.db"))
        store.record_run("a", _run("a"))
        store.record_run("b", _run("b"))
        board = ReliabilityLeaderboard(store=store)
        rankings = board.rank(["a", "b"])
        assert len(rankings) == 2
        store.close()

    def test_ties_handled_consistently(self):
        from agentcop.reliability.leaderboard import ReliabilityLeaderboard

        reports = [
            _report("a", reliability_score=80),
            _report("b", reliability_score=80),
            _report("c", reliability_score=60),
        ]
        board = ReliabilityLeaderboard()
        rankings = board.rank_reports(reports)
        # All three should appear exactly once
        agent_ids = {e.agent_id for e in rankings}
        assert agent_ids == {"a", "b", "c"}


# ===========================================================================
# prometheus.py
# ===========================================================================


class TestReportsToPrometheus:
    def test_returns_string(self):
        from agentcop.reliability.prometheus import reports_to_prometheus

        output = reports_to_prometheus([_report("a")])
        assert isinstance(output, str)

    def test_empty_returns_empty_string(self):
        from agentcop.reliability.prometheus import reports_to_prometheus

        assert reports_to_prometheus([]) == ""

    def test_all_8_metrics_present(self):
        from agentcop.reliability.prometheus import reports_to_prometheus

        output = reports_to_prometheus([_report("a")])
        expected_metrics = [
            "agentcop_reliability_score",
            "agentcop_path_entropy",
            "agentcop_tool_variance",
            "agentcop_retry_explosion_score",
            "agentcop_branch_instability",
            "agentcop_tokens_per_run_avg",
            "agentcop_cost_per_run_avg",
            "agentcop_window_runs_total",
        ]
        for metric in expected_metrics:
            assert metric in output, f"Missing metric: {metric}"

    def test_agent_id_label_present(self):
        from agentcop.reliability.prometheus import reports_to_prometheus

        output = reports_to_prometheus([_report("my-agent")])
        assert 'agent_id="my-agent"' in output

    def test_score_value_present(self):
        from agentcop.reliability.prometheus import reports_to_prometheus

        output = reports_to_prometheus([_report("a", reliability_score=87)])
        assert "agentcop_reliability_score" in output
        assert "87.0" in output

    def test_multiple_agents(self):
        from agentcop.reliability.prometheus import reports_to_prometheus

        output = reports_to_prometheus([_report("alpha"), _report("beta")])
        assert 'agent_id="alpha"' in output
        assert 'agent_id="beta"' in output

    def test_has_help_and_type_lines(self):
        from agentcop.reliability.prometheus import reports_to_prometheus

        output = reports_to_prometheus([_report("a")])
        assert "# HELP" in output
        assert "# TYPE" in output

    def test_timestamp_appended_when_provided(self):
        from agentcop.reliability.prometheus import reports_to_prometheus

        output = reports_to_prometheus([_report("a")], timestamp_ms=1700000000000)
        assert "1700000000000" in output

    def test_trailing_newline(self):
        from agentcop.reliability.prometheus import reports_to_prometheus

        output = reports_to_prometheus([_report("a")])
        assert output.endswith("\n")

    def test_label_escaping(self):
        from agentcop.reliability.prometheus import reports_to_prometheus

        output = reports_to_prometheus([_report('agent"special')])
        # Quotes inside labels must be escaped
        assert '\\"' in output


class TestPrometheusExporter:
    def test_export_with_store(self, tmp_path):
        from agentcop.reliability.prometheus import PrometheusExporter
        from agentcop.reliability.store import ReliabilityStore

        store = ReliabilityStore(str(tmp_path / "test.db"))
        store.record_run("a", _run("a"))
        exporter = PrometheusExporter(store)
        output = exporter.export(["a"])
        assert "agentcop_reliability_score" in output
        store.close()

    def test_export_reports(self):
        from agentcop.reliability.prometheus import PrometheusExporter

        exporter = PrometheusExporter(None)
        output = exporter.export_reports([_report("x")])
        assert "agentcop_reliability_score" in output


# ===========================================================================
# CLI argument parsing
# ===========================================================================


class TestCLIParser:
    def _parse(self, argv: list[str]) -> object:
        from agentcop.reliability.cli import _build_parser

        return _build_parser().parse_args(argv)

    def test_report_single_agent(self):
        args = self._parse(["report", "--agent", "my-agent"])
        assert args.command == "report"
        assert args.agent == "my-agent"

    def test_report_verbose_flag(self):
        args = self._parse(["report", "--agent", "x", "--verbose"])
        assert args.verbose is True

    def test_compare_requires_agents(self):
        args = self._parse(["compare", "--agents", "a", "b"])
        assert args.command == "compare"
        assert args.agents == ["a", "b"]

    def test_watch_defaults(self):
        args = self._parse(["watch", "--agent", "x"])
        assert args.interval == pytest.approx(5.0)
        assert args.window_hours == 24

    def test_export_json_default(self):
        args = self._parse(["export", "--agent", "x"])
        assert args.format == "json"

    def test_export_prometheus_format(self):
        args = self._parse(["export", "--agents", "a", "b", "--format", "prometheus"])
        assert args.format == "prometheus"
        assert args.agents == ["a", "b"]

    def test_custom_db_path(self):
        args = self._parse(["--db", "custom.db", "report", "--agent", "x"])
        assert args.db == "custom.db"

    def test_window_hours_override(self):
        args = self._parse(["report", "--agent", "x", "--window-hours", "48"])
        assert args.window_hours == 48


# ===========================================================================
# identity.py — record_run integration
# ===========================================================================


class TestIdentityRecordRun:
    def test_record_run_sets_reliability_score(self):
        from agentcop.identity import AgentIdentity

        identity = AgentIdentity.register("test-agent")
        run = _run("test-agent")
        identity.record_run(run)
        assert identity.reliability_score is not None
        assert 0 <= identity.reliability_score <= 100

    def test_record_run_sets_reliability_tier(self):
        from agentcop.identity import AgentIdentity

        identity = AgentIdentity.register("test-agent")
        run = _run("test-agent")
        identity.record_run(run)
        assert identity.reliability_tier in ("STABLE", "VARIABLE", "UNSTABLE", "CRITICAL")

    def test_record_run_sets_last_check(self):
        from agentcop.identity import AgentIdentity

        identity = AgentIdentity.register("test-agent")
        run = _run("test-agent")
        identity.record_run(run)
        assert identity.last_reliability_check is not None

    def test_stable_run_no_trust_penalty(self):
        from agentcop.identity import AgentIdentity

        identity = AgentIdentity.register("test-agent", trust_score=70.0)
        run = _run("test-agent")  # clean run → likely STABLE
        before = identity.trust_score
        identity.record_run(run)
        # STABLE → delta 0, trust unchanged
        if identity.reliability_tier == "STABLE":
            assert identity.trust_score == pytest.approx(before)

    def test_critical_run_reduces_trust(self):
        from unittest.mock import MagicMock, patch

        from agentcop.identity import AgentIdentity

        identity = AgentIdentity.register("test-agent", trust_score=70.0)
        run = _run("test-agent")

        mock_report = MagicMock()
        mock_report.reliability_score = 20
        mock_report.reliability_tier = "CRITICAL"

        with patch("agentcop.reliability.metrics.ReliabilityEngine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.compute_report.return_value = (mock_report, [])
            mock_engine_cls.return_value = mock_engine

            identity.record_run(run)

        assert identity.trust_score == pytest.approx(40.0)  # 70 - 30

    def test_unstable_run_reduces_trust_15(self):
        from unittest.mock import MagicMock, patch

        from agentcop.identity import AgentIdentity

        identity = AgentIdentity.register("test-agent", trust_score=60.0)
        run = _run("test-agent")

        mock_report = MagicMock()
        mock_report.reliability_score = 35
        mock_report.reliability_tier = "UNSTABLE"

        with patch("agentcop.reliability.metrics.ReliabilityEngine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.compute_report.return_value = (mock_report, [])
            mock_engine_cls.return_value = mock_engine

            identity.record_run(run)

        assert identity.trust_score == pytest.approx(45.0)  # 60 - 15

    def test_variable_run_reduces_trust_5(self):
        from unittest.mock import MagicMock, patch

        from agentcop.identity import AgentIdentity

        identity = AgentIdentity.register("test-agent", trust_score=80.0)
        run = _run("test-agent")

        mock_report = MagicMock()
        mock_report.reliability_score = 65
        mock_report.reliability_tier = "VARIABLE"

        with patch("agentcop.reliability.metrics.ReliabilityEngine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.compute_report.return_value = (mock_report, [])
            mock_engine_cls.return_value = mock_engine

            identity.record_run(run)

        assert identity.trust_score == pytest.approx(75.0)  # 80 - 5

    def test_trust_score_clamped_at_zero(self):
        from unittest.mock import MagicMock, patch

        from agentcop.identity import AgentIdentity

        identity = AgentIdentity.register("test-agent", trust_score=10.0)
        run = _run("test-agent")

        mock_report = MagicMock()
        mock_report.reliability_score = 10
        mock_report.reliability_tier = "CRITICAL"

        with patch("agentcop.reliability.metrics.ReliabilityEngine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.compute_report.return_value = (mock_report, [])
            mock_engine_cls.return_value = mock_engine

            identity.record_run(run)

        assert identity.trust_score == pytest.approx(0.0)

    def test_reliability_fields_none_before_first_run(self):
        from agentcop.identity import AgentIdentity

        identity = AgentIdentity.register("fresh-agent")
        assert identity.reliability_score is None
        assert identity.reliability_tier is None
        assert identity.last_reliability_check is None


# ===========================================================================
# Public API exports from agentcop.__init__
# ===========================================================================


class TestPublicAPIExports:
    def test_reliability_tracer_importable_from_agentcop(self):
        from agentcop import ReliabilityTracer  # type: ignore[attr-defined]

        assert ReliabilityTracer is not None

    def test_reliability_store_importable_from_agentcop(self):
        from agentcop import ReliabilityStore  # type: ignore[attr-defined]

        assert ReliabilityStore is not None

    def test_reliability_report_importable_from_agentcop(self):
        from agentcop import ReliabilityReport  # type: ignore[attr-defined]

        assert ReliabilityReport is not None

    def test_wrap_for_reliability_importable_from_agentcop(self):
        from agentcop import wrap_for_reliability  # type: ignore[attr-defined]

        assert wrap_for_reliability is not None

    def test_reliability_submodule_exports(self):
        from agentcop.reliability import (
            PrometheusExporter,
            ReliabilityLeaderboard,
            branch_instability_critical,
            combined_badge_text,
            reliability_drift_detected,
            reports_to_prometheus,
            retry_explosion,
            token_budget_spike,
            tool_variance_spike,
        )

        assert ReliabilityLeaderboard is not None
        assert PrometheusExporter is not None
        assert combined_badge_text is not None
        assert reliability_drift_detected is not None
        assert retry_explosion is not None
        assert branch_instability_critical is not None
        assert tool_variance_spike is not None
        assert token_budget_spike is not None
        assert reports_to_prometheus is not None
