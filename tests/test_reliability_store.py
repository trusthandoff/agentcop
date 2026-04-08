"""Tests for ReliabilityStore — schema init, CRUD, time window filtering."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from agentcop.reliability.models import AgentRun, ReliabilityReport, ToolCall
from agentcop.reliability.store import ReliabilityStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _run(
    agent_id: str = "agent-1",
    *,
    input_hash: str = "aaa",
    execution_path: list[str] | None = None,
    tool_calls: list[ToolCall] | None = None,
    retry_count: int = 0,
    total_tokens: int = 1000,
    timestamp: datetime | None = None,
) -> AgentRun:
    return AgentRun(
        agent_id=agent_id,
        timestamp=timestamp or datetime.now(UTC),
        input_hash=input_hash,
        output_hash=_sha("out"),
        execution_path=execution_path or ["a", "b"],
        tool_calls=tool_calls or [],
        duration_ms=100,
        success=True,
        retry_count=retry_count,
        input_tokens=total_tokens // 2,
        output_tokens=total_tokens // 2,
        total_tokens=total_tokens,
        estimated_cost_usd=0.01,
    )


def _tool(name: str) -> ToolCall:
    return ToolCall(
        tool_name=name,
        args_hash=_sha(name),
        result_hash=_sha(name + "_r"),
        duration_ms=5,
        success=True,
        retry_count=0,
    )


@pytest.fixture()
def store(tmp_path):
    """Fresh in-memory store per test."""
    s = ReliabilityStore(":memory:")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------


class TestSchemaInit:
    def test_tables_exist(self, store):
        cursor = store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "rel_agent_runs" in tables
        assert "rel_tool_calls" in tables
        assert "rel_snapshots" in tables
        assert "rel_schema_version" in tables

    def test_schema_version_set(self, store):
        cursor = store._conn.execute("SELECT version FROM rel_schema_version")
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == 1

    def test_double_init_is_idempotent(self, tmp_path):
        path = str(tmp_path / "test.db")
        s1 = ReliabilityStore(path)
        s1.close()
        s2 = ReliabilityStore(path)  # should not raise
        s2.close()


# ---------------------------------------------------------------------------
# record_run / get_runs roundtrip
# ---------------------------------------------------------------------------


class TestRecordAndGetRuns:
    def test_basic_roundtrip(self, store):
        run = _run()
        store.record_run("agent-1", run)
        runs = store.get_runs("agent-1", hours=1)
        assert len(runs) == 1
        assert runs[0].run_id == run.run_id
        assert runs[0].agent_id == "agent-1"
        assert runs[0].input_hash == run.input_hash
        assert runs[0].retry_count == run.retry_count
        assert runs[0].execution_path == run.execution_path

    def test_tool_calls_persisted(self, store):
        tc = [_tool("bash"), _tool("read")]
        run = _run(tool_calls=tc)
        store.record_run("agent-1", run)
        runs = store.get_runs("agent-1", hours=1)
        assert len(runs[0].tool_calls) == 2
        assert {t.tool_name for t in runs[0].tool_calls} == {"bash", "read"}

    def test_empty_tool_calls(self, store):
        run = _run(tool_calls=[])
        store.record_run("agent-1", run)
        runs = store.get_runs("agent-1", hours=1)
        assert runs[0].tool_calls == []

    def test_metadata_roundtrip(self, store):
        run = AgentRun(
            agent_id="a",
            timestamp=datetime.now(UTC),
            input_hash="x",
            output_hash="y",
            execution_path=[],
            duration_ms=1,
            success=True,
            retry_count=0,
            input_tokens=10,
            output_tokens=10,
            total_tokens=20,
            estimated_cost_usd=0.0,
            metadata={"model": "gpt-4o", "env": "prod"},
        )
        store.record_run("a", run)
        loaded = store.get_runs("a", hours=1)[0]
        assert loaded.metadata["model"] == "gpt-4o"
        assert loaded.metadata["env"] == "prod"

    def test_record_is_idempotent(self, store):
        run = _run()
        store.record_run("agent-1", run)
        store.record_run("agent-1", run)  # same run_id → INSERT OR REPLACE
        assert len(store.get_runs("agent-1", hours=1)) == 1

    def test_multiple_agents_isolated(self, store):
        store.record_run("agent-a", _run(agent_id="agent-a"))
        store.record_run("agent-b", _run(agent_id="agent-b"))
        assert len(store.get_runs("agent-a", hours=1)) == 1
        assert len(store.get_runs("agent-b", hours=1)) == 1

    def test_success_field_roundtrip(self, store):
        run = AgentRun(
            agent_id="a",
            timestamp=datetime.now(UTC),
            input_hash="x",
            output_hash="y",
            execution_path=[],
            duration_ms=1,
            success=False,
            retry_count=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost_usd=0.0,
        )
        store.record_run("a", run)
        loaded = store.get_runs("a", hours=1)[0]
        assert loaded.success is False


# ---------------------------------------------------------------------------
# Time-window filtering
# ---------------------------------------------------------------------------


class TestTimeWindowFiltering:
    def test_run_outside_window_excluded(self, store):
        old_run = _run(timestamp=datetime.now(UTC) - timedelta(hours=48))
        store.record_run("agent-1", old_run)
        runs = store.get_runs("agent-1", hours=24)
        assert len(runs) == 0

    def test_run_inside_window_included(self, store):
        recent = _run(timestamp=datetime.now(UTC) - timedelta(hours=1))
        store.record_run("agent-1", recent)
        runs = store.get_runs("agent-1", hours=24)
        assert len(runs) == 1

    def test_multiple_runs_time_ordered(self, store):
        t0 = datetime.now(UTC) - timedelta(hours=5)
        for i in range(3):
            store.record_run("a", _run(timestamp=t0 + timedelta(hours=i)))
        runs = store.get_runs("a", hours=10)
        assert len(runs) == 3
        assert runs[0].timestamp < runs[1].timestamp < runs[2].timestamp


# ---------------------------------------------------------------------------
# input_hash filtering
# ---------------------------------------------------------------------------


class TestInputHashFiltering:
    def test_input_hash_filter(self, store):
        store.record_run("a", _run(input_hash="aaa"))
        store.record_run("a", _run(input_hash="bbb"))
        store.record_run("a", _run(input_hash="aaa"))
        runs = store.get_runs("a", hours=1, input_hash="aaa")
        assert len(runs) == 2
        assert all(r.input_hash == "aaa" for r in runs)

    def test_input_hash_filter_no_match(self, store):
        store.record_run("a", _run(input_hash="aaa"))
        runs = store.get_runs("a", hours=1, input_hash="zzz")
        assert runs == []


# ---------------------------------------------------------------------------
# snapshot_report
# ---------------------------------------------------------------------------


class TestSnapshotReport:
    def _make_report(self, agent_id: str = "a") -> ReliabilityReport:
        return ReliabilityReport(
            agent_id=agent_id,
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

    def test_snapshot_stored(self, store):
        report = self._make_report()
        store.snapshot_report(report)
        cursor = store._conn.execute(
            "SELECT agent_id, window_hours FROM rel_snapshots WHERE agent_id = 'a'"
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[1] == 24

    def test_multiple_snapshots(self, store):
        store.snapshot_report(self._make_report("x"))
        store.snapshot_report(self._make_report("x"))
        cursor = store._conn.execute("SELECT COUNT(*) FROM rel_snapshots WHERE agent_id = 'x'")
        assert cursor.fetchone()[0] == 2

    def test_snapshot_json_roundtrip(self, store):
        import json

        report = self._make_report("z")
        store.snapshot_report(report)
        cursor = store._conn.execute("SELECT report_json FROM rel_snapshots WHERE agent_id = 'z'")
        raw = cursor.fetchone()[0]
        data = json.loads(raw)
        assert data["agent_id"] == "z"
        assert data["reliability_tier"] == "STABLE"


# ---------------------------------------------------------------------------
# get_report
# ---------------------------------------------------------------------------


class TestGetReport:
    def test_get_report_empty_returns_stable(self, store):
        report = store.get_report("no-runs-agent", window_hours=24)
        assert isinstance(report, ReliabilityReport)
        assert report.window_runs == 0
        assert report.reliability_tier == "STABLE"

    def test_get_report_uses_window_hours(self, store):
        for _ in range(3):
            store.record_run("a", _run())
        report = store.get_report("a", window_hours=48)
        assert report.window_hours == 48
        assert report.window_runs == 3

    def test_get_report_excludes_old_runs(self, store):
        old = _run(timestamp=datetime.now(UTC) - timedelta(hours=50))
        recent = _run(timestamp=datetime.now(UTC) - timedelta(minutes=30))
        store.record_run("a", old)
        store.record_run("a", recent)
        report = store.get_report("a", window_hours=24)
        assert report.window_runs == 1
