"""
Tests for the advanced reliability components:
  - ReliabilityTracer (instrumentation)
  - wrap_for_reliability
  - ReliabilityMixin + framework adapters (guards mocked)
  - @track_reliability decorator
  - CausalAnalyzer
  - ReliabilityPredictor
  - AgentClusterAnalyzer
"""

import hashlib
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from agentcop.reliability.models import AgentRun, ToolCall

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _run(
    agent_id: str = "agent-1",
    *,
    input_hash: str = "aaa",
    execution_path: list[str] | None = None,
    tool_calls: list[ToolCall] | None = None,
    retry_count: int = 0,
    total_tokens: int = 1000,
    estimated_cost_usd: float = 0.01,
    timestamp: datetime | None = None,
    offset_hours: float = 0.0,
    success: bool = True,
) -> AgentRun:
    return AgentRun(
        agent_id=agent_id,
        timestamp=(timestamp or _T0) + timedelta(hours=offset_hours),
        input_hash=input_hash,
        output_hash=_sha("out"),
        execution_path=execution_path or ["a", "b"],
        tool_calls=tool_calls or [],
        duration_ms=100,
        success=success,
        retry_count=retry_count,
        input_tokens=total_tokens // 2,
        output_tokens=total_tokens // 2,
        total_tokens=total_tokens,
        estimated_cost_usd=estimated_cost_usd,
    )


def _tool(name: str, retry_count: int = 0) -> ToolCall:
    return ToolCall(
        tool_name=name,
        args_hash=_sha(name),
        result_hash=_sha(name + "_r"),
        duration_ms=5,
        success=True,
        retry_count=retry_count,
    )


# ---------------------------------------------------------------------------
# ReliabilityTracer
# ---------------------------------------------------------------------------


class TestReliabilityTracer:
    def test_context_manager_returns_self(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        with ReliabilityTracer("a") as tracer:
            assert tracer is not None

    def test_run_built_on_exit(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        with ReliabilityTracer("my-agent") as tracer:
            tracer.record_branch("step_a")
        assert tracer.run is not None
        assert tracer.run.agent_id == "my-agent"
        assert "step_a" in tracer.run.execution_path

    def test_tool_calls_recorded(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        with ReliabilityTracer("a") as tracer:
            tracer.record_tool_call("bash", {"cmd": "ls"}, "file.txt")
            tracer.record_tool_call("read", "/etc/hosts", "127.0.0.1 localhost")
        assert len(tracer.run.tool_calls) == 2
        assert tracer.run.tool_calls[0].tool_name == "bash"
        assert tracer.run.tool_calls[1].tool_name == "read"

    def test_tool_args_are_hashed(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        with ReliabilityTracer("a") as tracer:
            tracer.record_tool_call("bash", {"secret": "password123"}, "ok")
        # Raw secret not stored — only the hash
        assert "password123" not in str(tracer.run.tool_calls[0].args_hash)
        assert len(tracer.run.tool_calls[0].args_hash) == 64  # SHA-256 hex

    def test_tokens_accumulated(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        with ReliabilityTracer("a") as tracer:
            tracer.record_tokens(input=100, output=200, model="gpt-4o")
            tracer.record_tokens(input=50, output=75)
        assert tracer.run.input_tokens == 150
        assert tracer.run.output_tokens == 275
        assert tracer.run.total_tokens == 425

    def test_cost_estimated_from_model(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        with ReliabilityTracer("a") as tracer:
            tracer.record_tokens(input=1_000_000, output=0, model="gpt-4o")
        # gpt-4o: $2.50 per 1M input tokens
        assert tracer.run.estimated_cost_usd == pytest.approx(2.50)

    def test_unknown_model_zero_cost(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        with ReliabilityTracer("a") as tracer:
            tracer.record_tokens(input=10_000, output=10_000, model="mystery-3")
        assert tracer.run.estimated_cost_usd == 0.0

    def test_exception_marks_failure(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        tracer = ReliabilityTracer("a")
        try:
            with tracer:
                raise ValueError("oops")
        except ValueError:
            pass
        assert tracer.run.success is False

    def test_exception_propagates(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        with pytest.raises(RuntimeError, match="boom"):
            with ReliabilityTracer("a"):
                raise RuntimeError("boom")

    def test_auto_store_on_exit(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        store = MagicMock()
        with ReliabilityTracer("a", store=store) as tracer:
            tracer.record_branch("x")
        store.record_run.assert_called_once_with("a", tracer.run)

    def test_input_hash_from_data(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer, _hash

        with ReliabilityTracer("a", input_data="task text") as tracer:
            pass
        assert tracer.run.input_hash == _hash("task text")

    def test_increment_retries(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        with ReliabilityTracer("a") as tracer:
            tracer.increment_retries(3)
        assert tracer.run.retry_count == 3

    def test_duration_ms_positive(self):
        from agentcop.reliability.instrumentation import ReliabilityTracer

        with ReliabilityTracer("a") as tracer:
            time.sleep(0.005)
        assert tracer.run.duration_ms >= 0


# ---------------------------------------------------------------------------
# wrap_for_reliability
# ---------------------------------------------------------------------------


class TestWrapForReliability:
    def _make_mock_adapter(self, events: list[dict]) -> MagicMock:
        """Adapter whose to_sentinel_event returns a SentinelEvent per dict."""
        from agentcop.event import SentinelEvent

        adapter = MagicMock()
        call_iter = iter(events)

        def _translate(raw):
            data = next(call_iter)
            return SentinelEvent(
                event_id=f"evt-{data['event_type']}",
                event_type=data["event_type"],
                timestamp=datetime.now(UTC),
                severity="INFO",
                body="",
                source_system="test",
                attributes=data.get("attributes", {}),
            )

        adapter.to_sentinel_event = _translate
        return adapter

    def test_run_recorded_on_node_end(self):
        from agentcop.reliability.instrumentation import wrap_for_reliability

        events = [
            {"event_type": "node_start", "attributes": {"node": "agent"}},
            {"event_type": "node_end", "attributes": {}},
        ]
        store = MagicMock()
        adapter = self._make_mock_adapter(events)
        wrap_for_reliability(adapter, "my-agent", store=store)

        for raw in events:
            adapter.to_sentinel_event(raw)

        store.record_run.assert_called_once()
        call_agent_id = store.record_run.call_args[0][0]
        assert call_agent_id == "my-agent"

    def test_branch_recorded_from_node_attribute(self):
        from agentcop.reliability.instrumentation import wrap_for_reliability

        events = [
            {"event_type": "node_start", "attributes": {"node": "planner"}},
            {"event_type": "some_step", "attributes": {"node": "executor"}},
            {"event_type": "node_end", "attributes": {}},
        ]
        store = MagicMock()
        adapter = self._make_mock_adapter(events)
        wrap_for_reliability(adapter, "a", store=store)
        for raw in events:
            adapter.to_sentinel_event(raw)

        run = store.record_run.call_args[0][1]
        assert "executor" in run.execution_path


# ---------------------------------------------------------------------------
# ReliabilityMixin
# ---------------------------------------------------------------------------


class TestReliabilityMixin:
    def test_start_and_end_run(self):
        from agentcop.reliability.adapters import ReliabilityMixin

        class Obj(ReliabilityMixin):
            pass

        obj = Obj()
        tracer = obj._start_run("agent-x")
        tracer.record_branch("step_1")
        run = obj._end_run(output="done")
        assert run is not None
        assert run.agent_id == "agent-x"
        assert "step_1" in run.execution_path

    def test_end_run_without_start_returns_none(self):
        from agentcop.reliability.adapters import ReliabilityMixin

        class Obj(ReliabilityMixin):
            pass

        assert Obj()._end_run() is None

    def test_end_run_with_exception_marks_failure(self):
        from agentcop.reliability.adapters import ReliabilityMixin

        class Obj(ReliabilityMixin):
            pass

        obj = Obj()
        obj._start_run("a")
        run = obj._end_run(exc=ValueError("err"))
        assert run.success is False


# ---------------------------------------------------------------------------
# LangChainReliabilityCallback (guard mocked)
# ---------------------------------------------------------------------------


class TestLangChainReliabilityCallback:
    def _make_callback(self, store=None):
        with patch("agentcop.reliability.adapters._require_langchain"):
            from agentcop.reliability.adapters import LangChainReliabilityCallback

            return LangChainReliabilityCallback("lc-agent", store=store)

    def test_on_chain_start_creates_tracer(self):
        cb = self._make_callback()
        cb.on_chain_start({}, {"input": "hello"})
        assert cb._get_tracer() is not None

    def test_on_chain_end_builds_run(self):
        cb = self._make_callback()
        cb.on_chain_start({}, {"input": "hi"})
        cb.on_chain_end({"output": "bye"})
        assert cb.last_run is not None
        assert cb.last_run.agent_id == "lc-agent"

    def test_on_chain_error_marks_failure(self):
        cb = self._make_callback()
        cb.on_chain_start({}, {})
        cb.on_chain_error(RuntimeError("boom"))
        assert cb.last_run is not None
        assert cb.last_run.success is False

    def test_tool_calls_captured(self):
        cb = self._make_callback()
        cb.on_chain_start({}, {})
        cb.on_tool_start({"name": "calculator"}, "2+2")
        cb.on_tool_end("4")
        cb.on_chain_end({})
        assert len(cb.last_run.tool_calls) == 1
        assert cb.last_run.tool_calls[0].tool_name == "calculator"

    def test_tool_error_marks_call_failed(self):
        cb = self._make_callback()
        cb.on_chain_start({}, {})
        cb.on_tool_start({"name": "search"}, "query")
        cb.on_tool_error(ConnectionError("timeout"))
        cb.on_chain_end({})
        assert cb.last_run.tool_calls[0].success is False

    def test_llm_tokens_captured(self):
        cb = self._make_callback()
        cb.on_chain_start({}, {})
        resp = MagicMock()
        resp.llm_output = {"token_usage": {"prompt_tokens": 50, "completion_tokens": 100}}
        cb.on_llm_end(resp)
        cb.on_chain_end({})
        assert cb.last_run.input_tokens == 50
        assert cb.last_run.output_tokens == 100


# ---------------------------------------------------------------------------
# AutoGenReliabilityWrapper (guard mocked)
# ---------------------------------------------------------------------------


class TestAutoGenReliabilityWrapper:
    def _make_wrapper(self, store=None):
        with patch("agentcop.reliability.adapters._require_autogen"):
            from agentcop.reliability.adapters import AutoGenReliabilityWrapper

            return AutoGenReliabilityWrapper("autogen-agent", store=store)

    def test_context_manager_builds_run(self):
        wrapper = self._make_wrapper()
        with wrapper.track_conversation("task"):
            pass
        assert wrapper.last_run is not None
        assert wrapper.last_run.agent_id == "autogen-agent"

    def test_wrap_function_map_records_calls(self):
        wrapper = self._make_wrapper()
        fn_map = {"search": lambda q: f"results for {q}"}
        wrapped = wrapper.wrap_function_map(fn_map)

        with wrapper.track_conversation():
            result = wrapped["search"]("AI")
        assert result == "results for AI"
        assert len(wrapper.last_run.tool_calls) == 1
        assert wrapper.last_run.tool_calls[0].tool_name == "search"

    def test_function_error_marks_call_failed(self):
        wrapper = self._make_wrapper()

        def fail_fn():
            raise RuntimeError("network error")

        fn_map = {"unstable": fail_fn}
        wrapped = wrapper.wrap_function_map(fn_map)

        with wrapper.track_conversation():
            try:
                wrapped["unstable"]()
            except RuntimeError:
                pass
        assert wrapper.last_run.tool_calls[0].success is False

    def test_context_exception_marks_failure(self):
        wrapper = self._make_wrapper()
        try:
            with wrapper.track_conversation():
                raise ValueError("crash")
        except ValueError:
            pass
        assert wrapper.last_run.success is False


# ---------------------------------------------------------------------------
# @track_reliability decorator
# ---------------------------------------------------------------------------


class TestTrackReliabilityDecorator:
    def test_basic_decoration(self):
        from agentcop.reliability.adapters import track_reliability

        @track_reliability("dec-agent")
        def my_fn(task: str) -> str:
            return f"done: {task}"

        result = my_fn("run task")
        assert result == "done: run task"

    def test_run_stored_when_store_provided(self):
        from agentcop.reliability.adapters import track_reliability

        store = MagicMock()

        @track_reliability("dec-agent", store=store)
        def my_fn(task: str) -> str:
            return "ok"

        my_fn("hello")
        store.record_run.assert_called_once()

    def test_named_input_arg(self):
        from agentcop.reliability.adapters import track_reliability
        from agentcop.reliability.instrumentation import _hash

        @track_reliability("a", input_arg="query")
        def search(limit: int, query: str) -> list:
            return []

        store = MagicMock()

        @track_reliability("a", input_arg="query", store=store)
        def search2(limit: int, query: str) -> list:
            return []

        search2(10, query="what is AI")
        run = store.record_run.call_args[0][1]
        assert run.input_hash == _hash("what is AI")

    def test_agent_id_metadata(self):
        from agentcop.reliability.adapters import track_reliability

        @track_reliability("tagged-agent")
        def fn() -> str:
            return "x"

        assert fn._reliability_agent_id == "tagged-agent"

    def test_exception_marks_failure_and_propagates(self):
        from agentcop.reliability.adapters import track_reliability

        store = MagicMock()

        @track_reliability("a", store=store)
        def bad_fn():
            raise RuntimeError("oops")

        with pytest.raises(RuntimeError):
            bad_fn()
        run = store.record_run.call_args[0][1]
        assert run.success is False


# ---------------------------------------------------------------------------
# CausalAnalyzer
# ---------------------------------------------------------------------------


class TestCausalAnalyzer:
    def test_too_few_runs_returns_empty(self):
        from agentcop.reliability.causality import CausalAnalyzer

        runs = [_run() for _ in range(4)]
        assert CausalAnalyzer().analyze(runs) == []

    def test_invalid_metric_raises(self):
        from agentcop.reliability.causality import CausalAnalyzer

        runs = [_run() for _ in range(10)]
        with pytest.raises(ValueError, match="Unknown metric"):
            CausalAnalyzer().analyze(runs, metric="nonexistent")

    def test_tool_correlation_detected(self):
        from agentcop.reliability.causality import CausalAnalyzer

        # When bash is called, retry_count is always high; otherwise 0
        runs = []
        for i in range(10):
            has_bash = i % 2 == 0
            tc = [_tool("bash")] if has_bash else []
            runs.append(_run(retry_count=5 if has_bash else 0, tool_calls=tc))

        findings = CausalAnalyzer(min_confidence=0.5).analyze(runs, metric="retry_count")
        tool_findings = [f for f in findings if f.factor_type == "tool"]
        assert any(f.factor_value == "bash" for f in tool_findings)

    def test_findings_sorted_by_confidence_desc(self):
        from agentcop.reliability.causality import CausalAnalyzer

        runs = []
        for i in range(20):
            tc = [_tool("bash")] if i % 2 == 0 else []
            runs.append(_run(retry_count=5 if i % 2 == 0 else 0, tool_calls=tc))

        findings = CausalAnalyzer(min_confidence=0.3).analyze(runs, metric="retry_count")
        if len(findings) > 1:
            for a, b in zip(findings, findings[1:]):
                assert a.confidence >= b.confidence

    def test_finding_description_format(self):
        from agentcop.reliability.causality import CausalAnalyzer

        runs = []
        for i in range(10):
            tc = [_tool("risky")] if i >= 5 else []
            runs.append(_run(total_tokens=5000 if i >= 5 else 500, tool_calls=tc))

        findings = CausalAnalyzer(min_confidence=0.4).analyze(runs, metric="total_tokens")
        for f in findings:
            assert "%" in f.description
            assert f.metric == "total_tokens"
            assert 0.0 <= f.confidence <= 1.0

    def test_input_source_correlation(self):
        from agentcop.reliability.causality import CausalAnalyzer
        import hashlib

        source_a = hashlib.sha256(b"source_A").hexdigest()
        source_b = hashlib.sha256(b"source_B").hexdigest()
        runs = []
        for i in range(12):
            ih = source_a if i % 2 == 0 else source_b
            runs.append(_run(input_hash=ih, retry_count=4 if i % 2 == 0 else 0))

        findings = CausalAnalyzer(min_confidence=0.5).analyze(runs, metric="retry_count")
        source_findings = [f for f in findings if f.factor_type == "input_source"]
        assert len(source_findings) > 0


# ---------------------------------------------------------------------------
# ReliabilityPredictor
# ---------------------------------------------------------------------------


class TestReliabilityPredictor:
    def test_too_few_runs_returns_empty(self):
        from agentcop.reliability.prediction import ReliabilityPredictor

        runs = [_run() for _ in range(4)]
        assert ReliabilityPredictor().predict(runs) == []

    def test_stable_series_no_warning(self):
        from agentcop.reliability.prediction import ReliabilityPredictor

        # Constant retry_count=0 → slope=0, no threshold breach
        runs = [
            _run(retry_count=0, offset_hours=float(i)) for i in range(20)
        ]
        predictions = ReliabilityPredictor(min_confidence=0.0).predict(runs)
        retry_pred = next((p for p in predictions if p.metric == "retry_count"), None)
        if retry_pred:
            assert not retry_pred.will_exceed_threshold

    def test_rising_retries_triggers_warning(self):
        from agentcop.reliability.prediction import ReliabilityPredictor

        # Retries linearly increasing 0 → 10 over 20 runs
        runs = [
            _run(retry_count=i, offset_hours=float(i)) for i in range(20)
        ]
        predictions = ReliabilityPredictor(min_confidence=0.5).predict(runs, horizon_hours=2.0)
        retry_pred = next((p for p in predictions if p.metric == "retry_count"), None)
        assert retry_pred is not None
        assert retry_pred.will_exceed_threshold
        assert retry_pred.sentinel_event is not None

    def test_sentinel_event_type(self):
        from agentcop.reliability.prediction import ReliabilityPredictor
        from agentcop.event import SentinelEvent

        runs = [_run(retry_count=i, offset_hours=float(i)) for i in range(20)]
        predictions = ReliabilityPredictor(min_confidence=0.5).predict(runs)
        for pred in predictions:
            if pred.sentinel_event:
                assert isinstance(pred.sentinel_event, SentinelEvent)
                assert pred.sentinel_event.event_type == "reliability_prediction"
                assert pred.sentinel_event.severity == "WARN"
                assert pred.sentinel_event.source_system == "agentcop.reliability"

    def test_prediction_confidence_bounded(self):
        from agentcop.reliability.prediction import ReliabilityPredictor

        runs = [_run(total_tokens=i * 10, offset_hours=float(i)) for i in range(20)]
        predictions = ReliabilityPredictor(min_confidence=0.0).predict(runs)
        for pred in predictions:
            assert 0.0 <= pred.confidence <= 1.0

    def test_ols_zero_slope_no_warning(self):
        from agentcop.reliability.prediction import ReliabilityPredictor

        runs = [_run(retry_count=0, offset_hours=float(i)) for i in range(20)]
        predictions = ReliabilityPredictor(min_confidence=0.0).predict(runs)
        for pred in predictions:
            if pred.metric == "retry_count":
                assert pred.slope_per_hour == pytest.approx(0.0, abs=0.01)

    def test_predictions_sorted_by_confidence(self):
        from agentcop.reliability.prediction import ReliabilityPredictor

        runs = [_run(retry_count=i, total_tokens=i * 100, offset_hours=float(i)) for i in range(20)]
        predictions = ReliabilityPredictor(min_confidence=0.0).predict(runs)
        if len(predictions) > 1:
            for a, b in zip(predictions, predictions[1:]):
                assert a.confidence >= b.confidence


# ---------------------------------------------------------------------------
# AgentClusterAnalyzer
# ---------------------------------------------------------------------------


class TestAgentClusterAnalyzer:
    def test_empty_input_returns_empty(self):
        from agentcop.reliability.clustering import AgentClusterAnalyzer

        assert AgentClusterAnalyzer().cluster_reports([]) == []
        assert AgentClusterAnalyzer().cluster_runs({}) == []

    def test_cluster_count_bounded_by_agents(self):
        from agentcop.reliability.clustering import AgentClusterAnalyzer
        from agentcop.reliability.models import ReliabilityReport

        def _report(agent_id: str, score: float) -> ReliabilityReport:
            return ReliabilityReport(
                agent_id=agent_id,
                window_runs=10,
                window_hours=24,
                path_entropy=score,
                tool_variance=score,
                retry_explosion_score=score,
                branch_instability=score,
                reliability_score=max(0, min(100, int(100 - score * 100))),
                reliability_tier="STABLE" if score < 0.2 else "VARIABLE",
                drift_detected=False,
                trend="STABLE",
                tokens_per_run_avg=500.0,
                cost_per_run_avg=0.01,
                token_spike_detected=False,
            )

        reports = [_report(f"agent-{i}", i * 0.1) for i in range(2)]
        clusters = AgentClusterAnalyzer(k=5).cluster_reports(reports)
        assert len(clusters) <= 2

    def test_cluster_from_runs(self):
        from agentcop.reliability.clustering import AgentClusterAnalyzer

        stable_runs = [_run(agent_id="stable", execution_path=["a", "b"]) for _ in range(8)]
        chaotic_runs = [
            _run(
                agent_id="chaotic",
                execution_path=[f"step_{i}"],
                retry_count=5,
            )
            for i in range(8)
        ]
        clusters = AgentClusterAnalyzer(k=2).cluster_runs({
            "stable": stable_runs,
            "chaotic": chaotic_runs,
        })
        assert len(clusters) == 2
        all_agents = {a for c in clusters for a in c.agent_ids}
        assert all_agents == {"stable", "chaotic"}

    def test_cluster_tier_assigned(self):
        from agentcop.reliability.clustering import AgentClusterAnalyzer

        stable_runs = [_run(execution_path=["a", "b"], retry_count=0) for _ in range(10)]
        clusters = AgentClusterAnalyzer(k=1).cluster_runs({"a": stable_runs})
        assert clusters[0].tier in ("STABLE", "VARIABLE", "UNSTABLE", "CRITICAL")

    def test_recommended_action_present(self):
        from agentcop.reliability.clustering import AgentClusterAnalyzer

        runs = [_run(execution_path=[f"p{i}"], retry_count=5) for i in range(8)]
        clusters = AgentClusterAnalyzer(k=1).cluster_runs({"a": runs})
        assert clusters[0].recommended_action

    def test_shared_pattern_stable(self):
        from agentcop.reliability.clustering import AgentClusterAnalyzer

        stable_runs = [_run(execution_path=["a", "b"], retry_count=0) for _ in range(10)]
        clusters = AgentClusterAnalyzer(k=1).cluster_runs({"a": stable_runs})
        assert "stable" in clusters[0].shared_pattern.lower()

    def test_agent_ids_sorted(self):
        from agentcop.reliability.clustering import AgentClusterAnalyzer

        runs = [_run() for _ in range(5)]
        clusters = AgentClusterAnalyzer(k=1).cluster_runs({
            "zeta": runs, "alpha": runs, "mango": runs
        })
        for cluster in clusters:
            assert cluster.agent_ids == sorted(cluster.agent_ids)

    def test_centroid_dimension(self):
        from agentcop.reliability.clustering import AgentClusterAnalyzer

        runs = [_run() for _ in range(5)]
        clusters = AgentClusterAnalyzer(k=1).cluster_runs({"a": runs})
        assert len(clusters[0].centroid) == 4  # pe, tv, retry, bi
        for v in clusters[0].centroid:
            assert 0.0 <= v <= 1.0

    def test_deterministic_across_calls(self):
        from agentcop.reliability.clustering import AgentClusterAnalyzer

        paths = [["a", "b"], ["c", "d"], ["e"]]
        runs = {
            f"agent-{i}": [_run(execution_path=paths[i % 3]) for _ in range(5)]
            for i in range(6)
        }
        a = AgentClusterAnalyzer(k=2).cluster_runs(runs)
        b = AgentClusterAnalyzer(k=2).cluster_runs(runs)
        assert [c.agent_ids for c in a] == [c.agent_ids for c in b]
