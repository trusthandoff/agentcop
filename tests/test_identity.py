"""
Tests for agentcop.identity:
  - AgentIdentity.register + fingerprinting
  - Trust score evolution (clean executions, violations, repeat, streak bonus)
  - Behavioral baseline building (after 10 executions)
  - Drift detection (new tool, slow execution, new agent contact)
  - Auto-flagging when trust < 30
  - Storage backends (InMemoryIdentityStore, SQLiteIdentityStore)
  - Sentinel integration (attach_identity, push enrichment, detect_violations, watch)
  - Thread safety
"""

import threading
import time
from datetime import UTC, datetime

import pytest

from agentcop import (
    AgentIdentity,
    BehavioralBaseline,
    InMemoryIdentityStore,
    Sentinel,
    SentinelEvent,
    SQLiteIdentityStore,
    ViolationRecord,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(
    event_id: str = "e-1",
    event_type: str = "node_end",
    attributes: dict | None = None,
) -> SentinelEvent:
    return SentinelEvent(
        event_id=event_id,
        event_type=event_type,
        timestamp=datetime.now(UTC),
        severity="INFO",
        body="test event",
        source_system="test",
        attributes=attributes or {},
    )


def _make_baseline_identity(
    agent_id: str = "test-agent",
    tools: list[str] | None = None,
    avg_time: float = 1.0,
) -> AgentIdentity:
    """Return an identity with baseline already established."""
    identity = AgentIdentity.register(agent_id=agent_id, code="def fn(): pass")
    tools = tools or ["tool_a", "tool_b"]
    for _ in range(10):
        identity.record_execution(
            {
                "tools_called": tools,
                "execution_time": avg_time,
                "output_size": 100,
                "agents_contacted": ["agent-peer"],
            }
        )
    assert identity.baseline is not None
    return identity


# ---------------------------------------------------------------------------
# Registration + fingerprinting
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_returns_agent_identity(self):
        identity = AgentIdentity.register(agent_id="my-agent")
        assert isinstance(identity, AgentIdentity)
        assert identity.agent_id == "my-agent"

    def test_default_trust_score_is_50(self):
        identity = AgentIdentity.register(agent_id="a")
        assert identity.trust_score == 50.0

    def test_default_status_is_active(self):
        identity = AgentIdentity.register(agent_id="a")
        assert identity.status == "active"

    def test_fingerprint_is_sha256_hex(self):
        identity = AgentIdentity.register(agent_id="a")
        assert len(identity.fingerprint) == 64
        assert all(c in "0123456789abcdef" for c in identity.fingerprint)

    def test_callable_code_produces_fingerprint(self):
        def my_agent():
            return "hello"

        identity = AgentIdentity.register(agent_id="a", code=my_agent)
        assert identity.fingerprint != "0" * 64

    def test_string_code_produces_fingerprint(self):
        identity = AgentIdentity.register(agent_id="a", code="def agent(): pass")
        assert len(identity.fingerprint) == 64

    def test_file_path_code_produces_fingerprint(self, tmp_path):
        script = tmp_path / "agent.py"
        script.write_text("def agent(): pass")
        identity = AgentIdentity.register(agent_id="a", code=script)
        assert len(identity.fingerprint) == 64

    def test_config_affects_fingerprint(self):
        fp1 = AgentIdentity.register(agent_id="a", code="x", config={"v": 1}).fingerprint
        fp2 = AgentIdentity.register(agent_id="a", code="x", config={"v": 2}).fingerprint
        assert fp1 != fp2

    def test_same_inputs_same_fingerprint(self):
        fp1 = AgentIdentity.register(agent_id="a", code="def fn(): pass").fingerprint
        fp2 = AgentIdentity.register(agent_id="a", code="def fn(): pass").fingerprint
        assert fp1 == fp2

    def test_different_code_different_fingerprint(self):
        fp1 = AgentIdentity.register(agent_id="a", code="def fn(): return 1").fingerprint
        fp2 = AgentIdentity.register(agent_id="a", code="def fn(): return 2").fingerprint
        assert fp1 != fp2

    def test_register_persists_to_store(self):
        store = InMemoryIdentityStore()
        identity = AgentIdentity.register(agent_id="my-agent", store=store)
        loaded = store.load("my-agent")
        assert loaded is identity

    def test_metadata_stored(self):
        identity = AgentIdentity.register(
            agent_id="a", metadata={"framework": "langgraph"}
        )
        assert identity.metadata["framework"] == "langgraph"

    def test_created_at_is_utc(self):
        identity = AgentIdentity.register(agent_id="a")
        assert identity.created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Trust score evolution
# ---------------------------------------------------------------------------


class TestTrustScore:
    def test_clean_execution_increments_by_1(self):
        identity = AgentIdentity.register(agent_id="a")
        identity.record_execution()
        assert identity.trust_score == 51.0

    def test_multiple_clean_executions_accumulate(self):
        identity = AgentIdentity.register(agent_id="a")
        for _ in range(5):
            identity.record_execution()
        assert identity.trust_score == 55.0

    def test_trust_clamped_at_100(self):
        identity = AgentIdentity.register(agent_id="a", trust_score=99.0)
        identity.record_execution()
        assert identity.trust_score == 100.0
        identity.record_execution()
        assert identity.trust_score == 100.0

    def test_warn_violation_decrements_5(self):
        identity = AgentIdentity.register(agent_id="a")
        v = ViolationRecord(
            violation_type="x", severity="WARN", source_event_id="e1"
        )
        identity.observe_violation(v)
        assert identity.trust_score == 45.0

    def test_error_violation_decrements_10(self):
        identity = AgentIdentity.register(agent_id="a")
        v = ViolationRecord(
            violation_type="x", severity="ERROR", source_event_id="e1"
        )
        identity.observe_violation(v)
        assert identity.trust_score == 40.0

    def test_critical_violation_decrements_20(self):
        identity = AgentIdentity.register(agent_id="a")
        v = ViolationRecord(
            violation_type="x", severity="CRITICAL", source_event_id="e1"
        )
        identity.observe_violation(v)
        assert identity.trust_score == 30.0

    def test_repeat_violation_adds_10_extra_penalty(self):
        identity = AgentIdentity.register(agent_id="a")
        v = ViolationRecord(
            violation_type="same_type", severity="WARN", source_event_id="e1"
        )
        identity.observe_violation(v)  # first: -5 → 45
        assert identity.trust_score == 45.0
        identity.observe_violation(v)  # second: -5 -10 = -15 → 30
        assert identity.trust_score == 30.0

    def test_streak_bonus_at_50_clean(self):
        identity = AgentIdentity.register(agent_id="a", trust_score=0.0)
        # 50 clean executions → +50 base + +10 bonus = 60
        for _ in range(50):
            identity.record_execution()
        assert identity.trust_score == 60.0

    def test_streak_bonus_applies_at_every_50_executions(self):
        identity = AgentIdentity.register(agent_id="a", trust_score=0.0)
        for _ in range(100):
            identity.record_execution()
        # +100 (base) + +10 (at 50) + +10 (at 100) = 120 → clamped 100
        assert identity.trust_score == 100.0

    def test_trust_clamped_at_0(self):
        identity = AgentIdentity.register(agent_id="a", trust_score=5.0)
        v = ViolationRecord(
            violation_type="x", severity="CRITICAL", source_event_id="e1"
        )
        identity.observe_violation(v)
        assert identity.trust_score == 0.0

    def test_observe_violation_resets_consecutive_clean(self):
        identity = AgentIdentity.register(agent_id="a")
        identity.record_execution()
        assert identity._consecutive_clean == 1
        v = ViolationRecord(
            violation_type="x", severity="WARN", source_event_id="e1"
        )
        identity.observe_violation(v)
        assert identity._consecutive_clean == 0


# ---------------------------------------------------------------------------
# Auto-flagging
# ---------------------------------------------------------------------------


class TestAutoFlagging:
    def test_auto_flag_when_trust_below_30_from_violations(self):
        identity = AgentIdentity.register(agent_id="a", trust_score=31.0)
        v = ViolationRecord(
            violation_type="x", severity="CRITICAL", source_event_id="e1"
        )
        extras = identity.observe_violation(v)
        assert identity.status == "flagged"
        assert any(e.violation_type == "agent_flagged" for e in extras)

    def test_agent_flagged_severity_is_critical(self):
        identity = AgentIdentity.register(agent_id="a", trust_score=31.0)
        v = ViolationRecord(
            violation_type="x", severity="CRITICAL", source_event_id="e1"
        )
        extras = identity.observe_violation(v)
        flagged = next(e for e in extras if e.violation_type == "agent_flagged")
        assert flagged.severity == "CRITICAL"

    def test_auto_flag_from_record_execution_drift(self):
        identity = AgentIdentity.register(agent_id="a", trust_score=5.0)
        # build baseline
        for _ in range(10):
            identity.record_execution({"tools_called": ["tool_a"]})
        # 11th execution with new tool → drift penalty → trust may drop below 30
        identity.record_execution({"tools_called": ["brand_new_tool"]})
        # trust score will drop due to drift penalty
        assert identity.trust_score <= 50.0

    def test_already_flagged_does_not_emit_second_time(self):
        identity = AgentIdentity.register(agent_id="a", trust_score=25.0, status="flagged")
        v = ViolationRecord(
            violation_type="x", severity="WARN", source_event_id="e1"
        )
        extras = identity.observe_violation(v)
        # already flagged — no new agent_flagged record
        assert not any(e.violation_type == "agent_flagged" for e in extras)


# ---------------------------------------------------------------------------
# Behavioral baseline
# ---------------------------------------------------------------------------


class TestBaseline:
    def test_no_baseline_before_10_executions(self):
        identity = AgentIdentity.register(agent_id="a")
        for _ in range(9):
            identity.record_execution()
        assert identity.baseline is None

    def test_baseline_built_at_10_executions(self):
        identity = AgentIdentity.register(agent_id="a")
        for _ in range(10):
            identity.record_execution()
        assert isinstance(identity.baseline, BehavioralBaseline)

    def test_baseline_tools_frequency(self):
        identity = AgentIdentity.register(agent_id="a")
        for i in range(10):
            tools = ["tool_a", "tool_b"] if i < 5 else ["tool_a"]
            identity.record_execution({"tools_called": tools})
        assert identity.baseline is not None
        assert "tool_a" in identity.baseline.tools_called
        assert identity.baseline.tools_called["tool_a"] == 1.0  # used every run
        assert identity.baseline.tools_called["tool_b"] == 0.5  # used in 5/10 runs

    def test_baseline_avg_execution_time(self):
        identity = AgentIdentity.register(agent_id="a")
        for _ in range(10):
            identity.record_execution({"execution_time": 2.0})
        assert identity.baseline is not None
        assert identity.baseline.avg_execution_time == pytest.approx(2.0)

    def test_baseline_output_size_range(self):
        identity = AgentIdentity.register(agent_id="a")
        sizes = list(range(10, 20))  # 10..19
        for s in sizes:
            identity.record_execution({"output_size": s})
        assert identity.baseline is not None
        assert identity.baseline.typical_output_size_min == 10
        assert identity.baseline.typical_output_size_max == 19

    def test_baseline_known_agents(self):
        identity = AgentIdentity.register(agent_id="a")
        for _ in range(10):
            identity.record_execution({"agents_contacted": ["peer-1", "peer-2"]})
        assert identity.baseline is not None
        assert "peer-1" in identity.baseline.known_agents
        assert "peer-2" in identity.baseline.known_agents

    def test_baseline_execution_count(self):
        identity = AgentIdentity.register(agent_id="a")
        for _ in range(10):
            identity.record_execution()
        assert identity.baseline is not None
        assert identity.baseline.execution_count == 10

    def test_no_execution_time_when_not_provided(self):
        identity = AgentIdentity.register(agent_id="a")
        for _ in range(10):
            identity.record_execution()
        assert identity.baseline is not None
        assert identity.baseline.avg_execution_time is None


# ---------------------------------------------------------------------------
# Drift detection via record_execution
# ---------------------------------------------------------------------------


class TestDriftDetection:
    def test_no_drift_before_baseline(self):
        identity = AgentIdentity.register(agent_id="a")
        # First 9 executions: baseline not yet established, no drift violations
        for _ in range(9):
            violations = identity.record_execution({"tools_called": ["brand_new"]})
            assert identity.baseline is None
            assert violations == []
        # 10th execution: baseline is built, still no drift violations on build
        violations = identity.record_execution({"tools_called": ["brand_new"]})
        assert identity.baseline is not None
        assert violations == []

    def test_new_tool_triggers_drift_violation(self):
        identity = _make_baseline_identity(tools=["tool_a"])
        violations = identity.record_execution({"tools_called": ["never_seen"]})
        assert any(
            v.violation_type == "identity_drift" and v.detail["drift_type"] == "new_tool"
            for v in violations
        )

    def test_known_tool_no_drift(self):
        identity = _make_baseline_identity(tools=["tool_a"])
        violations = identity.record_execution({"tools_called": ["tool_a"]})
        assert not any(v.violation_type == "identity_drift" for v in violations)

    def test_slow_execution_triggers_drift(self):
        identity = _make_baseline_identity(avg_time=1.0)
        violations = identity.record_execution({"execution_time": 4.0})  # 4x > 3x
        assert any(
            v.violation_type == "identity_drift"
            and v.detail["drift_type"] == "slow_execution"
            for v in violations
        )

    def test_normal_execution_time_no_drift(self):
        identity = _make_baseline_identity(avg_time=1.0)
        violations = identity.record_execution({"execution_time": 2.5})  # 2.5x < 3x
        assert not any(
            v.violation_type == "identity_drift"
            and v.detail["drift_type"] == "slow_execution"
            for v in violations
        )

    def test_new_agent_contact_triggers_drift(self):
        identity = _make_baseline_identity()
        violations = identity.record_execution({"agents_contacted": ["completely-new"]})
        assert any(
            v.violation_type == "identity_drift"
            and v.detail["drift_type"] == "new_agent_contact"
            for v in violations
        )

    def test_known_agent_no_drift(self):
        identity = _make_baseline_identity()
        violations = identity.record_execution({"agents_contacted": ["agent-peer"]})
        assert not any(v.violation_type == "identity_drift" for v in violations)

    def test_drift_violation_severity_is_warn(self):
        identity = _make_baseline_identity(tools=["tool_a"])
        violations = identity.record_execution({"tools_called": ["unseen"]})
        drift_v = next(v for v in violations if v.violation_type == "identity_drift")
        assert drift_v.severity == "WARN"

    def test_drift_reduces_trust_score(self):
        identity = _make_baseline_identity(tools=["tool_a"])
        score_before = identity.trust_score
        identity.record_execution({"tools_called": ["unseen"]})
        assert identity.trust_score < score_before


# ---------------------------------------------------------------------------
# Drift detector (make_drift_detector)
# ---------------------------------------------------------------------------


class TestMakeDriftDetector:
    def test_detector_returns_none_before_baseline(self):
        identity = AgentIdentity.register(agent_id="a")
        detect = identity.make_drift_detector()
        event = _event(attributes={"agent_id": "a", "tools_called": ["new_tool"]})
        assert detect(event) is None

    def test_detector_returns_none_for_other_agent(self):
        identity = _make_baseline_identity()
        detect = identity.make_drift_detector()
        event = _event(attributes={"agent_id": "other-agent", "tools_called": ["new"]})
        assert detect(event) is None

    def test_detector_fires_on_new_tool(self):
        identity = _make_baseline_identity(tools=["tool_a"])
        detect = identity.make_drift_detector()
        event = _event(
            attributes={"agent_id": "test-agent", "tools_called": ["unseen_tool"]}
        )
        result = detect(event)
        assert result is not None
        assert result.violation_type == "identity_drift"
        assert result.detail["drift_type"] == "new_tool"

    def test_detector_returns_none_for_known_tool(self):
        identity = _make_baseline_identity(tools=["tool_a"])
        detect = identity.make_drift_detector()
        event = _event(attributes={"agent_id": "test-agent", "tools_called": ["tool_a"]})
        assert detect(event) is None

    def test_detector_fires_on_slow_execution(self):
        identity = _make_baseline_identity(avg_time=1.0)
        detect = identity.make_drift_detector()
        event = _event(
            attributes={"agent_id": "test-agent", "execution_time": 10.0}
        )
        result = detect(event)
        assert result is not None
        assert result.detail["drift_type"] == "slow_execution"

    def test_detector_fires_on_new_agent_contact(self):
        identity = _make_baseline_identity()
        detect = identity.make_drift_detector()
        event = _event(
            attributes={"agent_id": "test-agent", "agents_contacted": ["brand-new"]}
        )
        result = detect(event)
        assert result is not None
        assert result.detail["drift_type"] == "new_agent_contact"

    def test_detector_preserves_trace_id(self):
        identity = _make_baseline_identity(tools=["tool_a"])
        detect = identity.make_drift_detector()
        event = _event(
            attributes={"agent_id": "test-agent", "tools_called": ["new"]}
        )
        event = event.model_copy(update={"trace_id": "trace-xyz"})
        result = detect(event)
        assert result is not None
        assert result.trace_id == "trace-xyz"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_to_dict_round_trips_basic_fields(self):
        identity = AgentIdentity.register(
            agent_id="a", code="def fn(): pass", metadata={"k": "v"}
        )
        d = identity.to_dict()
        restored = AgentIdentity.from_dict(d)
        assert restored.agent_id == identity.agent_id
        assert restored.fingerprint == identity.fingerprint
        assert restored.trust_score == identity.trust_score
        assert restored.status == identity.status
        assert restored.metadata == identity.metadata

    def test_to_dict_includes_baseline(self):
        identity = _make_baseline_identity()
        d = identity.to_dict()
        assert "baseline" in d
        assert "tools_called" in d["baseline"]
        assert "known_agents" in d["baseline"]

    def test_from_dict_restores_baseline(self):
        identity = _make_baseline_identity(tools=["tool_x"])
        d = identity.to_dict()
        restored = AgentIdentity.from_dict(d)
        assert restored.baseline is not None
        assert "tool_x" in restored.baseline.tools_called

    def test_from_dict_restores_execution_count(self):
        identity = AgentIdentity.register(agent_id="a")
        for _ in range(7):
            identity.record_execution()
        d = identity.to_dict()
        restored = AgentIdentity.from_dict(d)
        assert restored._execution_count == 7

    def test_from_dict_restores_violation_type_counts(self):
        identity = AgentIdentity.register(agent_id="a")
        v = ViolationRecord(
            violation_type="test_type", severity="WARN", source_event_id="e1"
        )
        identity.observe_violation(v)
        d = identity.to_dict()
        restored = AgentIdentity.from_dict(d)
        assert restored._violation_type_counts.get("test_type") == 1


# ---------------------------------------------------------------------------
# InMemoryIdentityStore
# ---------------------------------------------------------------------------


class TestInMemoryIdentityStore:
    def test_save_and_load(self):
        store = InMemoryIdentityStore()
        identity = AgentIdentity.register(agent_id="a", store=store)
        loaded = store.load("a")
        assert loaded is identity

    def test_load_missing_returns_none(self):
        store = InMemoryIdentityStore()
        assert store.load("nonexistent") is None

    def test_delete_removes_entry(self):
        store = InMemoryIdentityStore()
        AgentIdentity.register(agent_id="a", store=store)
        store.delete("a")
        assert store.load("a") is None

    def test_list_agents(self):
        store = InMemoryIdentityStore()
        AgentIdentity.register(agent_id="agent-1", store=store)
        AgentIdentity.register(agent_id="agent-2", store=store)
        agents = store.list_agents()
        assert set(agents) == {"agent-1", "agent-2"}

    def test_overwrite_on_save(self):
        store = InMemoryIdentityStore()
        AgentIdentity.register(agent_id="a", store=store)
        id2 = AgentIdentity(agent_id="a", fingerprint="x" * 64, store=store)
        store.save(id2)
        assert store.load("a") is id2


# ---------------------------------------------------------------------------
# SQLiteIdentityStore
# ---------------------------------------------------------------------------


class TestSQLiteIdentityStore:
    def test_save_and_load(self, tmp_path):
        db = tmp_path / "test.db"
        store = SQLiteIdentityStore(db_path=db)
        identity = AgentIdentity.register(
            agent_id="sql-agent", code="def fn(): pass", store=store
        )
        store.save(identity)
        loaded = store.load("sql-agent")
        assert loaded is not None
        assert loaded.agent_id == identity.agent_id
        assert loaded.fingerprint == identity.fingerprint
        store.close()

    def test_load_missing_returns_none(self, tmp_path):
        store = SQLiteIdentityStore(db_path=tmp_path / "test.db")
        assert store.load("nobody") is None
        store.close()

    def test_delete(self, tmp_path):
        store = SQLiteIdentityStore(db_path=tmp_path / "test.db")
        identity = AgentIdentity.register(agent_id="x", store=store)
        store.save(identity)
        store.delete("x")
        assert store.load("x") is None
        store.close()

    def test_list_agents(self, tmp_path):
        store = SQLiteIdentityStore(db_path=tmp_path / "test.db")
        for name in ["a1", "a2", "a3"]:
            id_ = AgentIdentity.register(agent_id=name, store=store)
            store.save(id_)
        assert set(store.list_agents()) == {"a1", "a2", "a3"}
        store.close()

    def test_persistence_across_reopen(self, tmp_path):
        db = tmp_path / "persist.db"
        store = SQLiteIdentityStore(db_path=db)
        identity = AgentIdentity.register(agent_id="persisted", trust_score=75.0, store=store)
        identity.trust_score = 75.0
        store.save(identity)
        store.close()

        store2 = SQLiteIdentityStore(db_path=db)
        loaded = store2.load("persisted")
        assert loaded is not None
        assert loaded.agent_id == "persisted"
        store2.close()

    def test_overwrite_on_save(self, tmp_path):
        store = SQLiteIdentityStore(db_path=tmp_path / "test.db")
        identity = AgentIdentity.register(agent_id="a", store=store)
        store.save(identity)
        identity2 = AgentIdentity(
            agent_id="a",
            fingerprint="f" * 64,
            store=store,
            trust_score=99.0,
        )
        store.save(identity2)
        loaded = store.load("a")
        assert loaded is not None
        assert loaded.trust_score == 99.0
        store.close()


# ---------------------------------------------------------------------------
# Sentinel integration
# ---------------------------------------------------------------------------


class TestSentinelIntegration:
    def test_attach_identity_enriches_push(self):
        identity = AgentIdentity.register(agent_id="sentinel-agent")
        sentinel = Sentinel(detectors=[])
        sentinel.attach_identity(identity)

        event = _event()
        sentinel.push(event)

        with sentinel._lock:
            stored = sentinel._events[0]
        assert stored.attributes["agent_id"] == "sentinel-agent"

    def test_push_enriched_with_trust_score(self):
        identity = AgentIdentity.register(agent_id="a", trust_score=75.0)
        sentinel = Sentinel(detectors=[])
        sentinel.attach_identity(identity)
        sentinel.push(_event())

        with sentinel._lock:
            stored = sentinel._events[0]
        assert stored.attributes["trust_score"] == 75.0

    def test_push_enriched_with_fingerprint(self):
        identity = AgentIdentity.register(agent_id="a", code="def fn(): pass")
        sentinel = Sentinel(detectors=[])
        sentinel.attach_identity(identity)
        sentinel.push(_event())

        with sentinel._lock:
            stored = sentinel._events[0]
        assert stored.attributes["fingerprint"] == identity.fingerprint

    def test_push_enriched_with_identity_status(self):
        identity = AgentIdentity.register(agent_id="a")
        sentinel = Sentinel(detectors=[])
        sentinel.attach_identity(identity)
        sentinel.push(_event())

        with sentinel._lock:
            stored = sentinel._events[0]
        assert stored.attributes["identity_status"] == "active"

    def test_push_without_identity_leaves_attributes_unchanged(self):
        sentinel = Sentinel(detectors=[])
        sentinel.push(_event(attributes={"custom": "val"}))
        with sentinel._lock:
            stored = sentinel._events[0]
        assert stored.attributes == {"custom": "val"}

    def test_attach_identity_registers_drift_detector(self):
        identity = _make_baseline_identity(tools=["tool_a"])
        sentinel = Sentinel(detectors=[])  # no default detectors
        sentinel.attach_identity(identity)

        event = _event(
            attributes={
                "agent_id": "test-agent",
                "tools_called": ["unseen_tool"],
            }
        )
        sentinel.push(event)
        violations = sentinel.detect_violations()
        assert any(v.violation_type == "identity_drift" for v in violations)

    def test_detect_violations_calls_violation_hooks(self):
        identity = AgentIdentity.register(agent_id="a", trust_score=35.0)
        sentinel = Sentinel(detectors=[])
        sentinel.attach_identity(identity)

        # Inject a pre-built violation manually via a custom detector
        critical_v = ViolationRecord(
            violation_type="test_critical", severity="CRITICAL", source_event_id="e1"
        )

        def always_critical(event: SentinelEvent) -> ViolationRecord | None:
            return critical_v

        sentinel.register_detector(always_critical)
        sentinel.push(_event())
        violations = sentinel.detect_violations()
        # trust should have dropped and agent_flagged should appear
        assert identity.trust_score <= 15.0
        assert any(v.violation_type == "agent_flagged" for v in violations)

    def test_watch_calls_on_violation(self):
        identity = _make_baseline_identity(tools=["tool_a"])
        sentinel = Sentinel(detectors=[])
        sentinel.attach_identity(identity)

        observed: list[ViolationRecord] = []

        with sentinel.watch(lambda v: observed.append(v) or [], poll_interval=0.02):
            sentinel.push(
                _event(
                    attributes={
                        "agent_id": "test-agent",
                        "tools_called": ["unseen"],
                    }
                )
            )
            time.sleep(0.15)

        assert any(v.violation_type == "identity_drift" for v in observed)

    def test_watch_updates_trust_via_hooks(self):
        identity = AgentIdentity.register(agent_id="a", trust_score=35.0)

        always_critical_v = ViolationRecord(
            violation_type="test_crit", severity="CRITICAL", source_event_id="e1"
        )

        def always_critical(event: SentinelEvent) -> ViolationRecord | None:
            return always_critical_v

        sentinel = Sentinel(detectors=[always_critical])
        sentinel.attach_identity(identity)

        with sentinel.watch(lambda v: None, poll_interval=0.02):
            sentinel.push(_event())
            time.sleep(0.15)

        assert identity.trust_score <= 15.0

    def test_original_event_attributes_preserved(self):
        identity = AgentIdentity.register(agent_id="a")
        sentinel = Sentinel(detectors=[])
        sentinel.attach_identity(identity)
        sentinel.push(_event(attributes={"my_key": "my_value"}))

        with sentinel._lock:
            stored = sentinel._events[0]
        assert stored.attributes["my_key"] == "my_value"
        assert stored.attributes["agent_id"] == "a"

    def test_original_event_not_mutated(self):
        identity = AgentIdentity.register(agent_id="a")
        sentinel = Sentinel(detectors=[])
        sentinel.attach_identity(identity)
        original = _event()
        original_attrs = dict(original.attributes)
        sentinel.push(original)
        assert original.attributes == original_attrs


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_record_execution(self):
        identity = AgentIdentity.register(agent_id="concurrent")
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(20):
                    identity.record_execution({"tools_called": ["tool_a"]})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert identity._execution_count == 160

    def test_concurrent_observe_violation(self):
        identity = AgentIdentity.register(agent_id="concurrent", trust_score=100.0)
        errors: list[Exception] = []

        def worker():
            try:
                v = ViolationRecord(
                    violation_type="stress", severity="WARN", source_event_id="e1"
                )
                for _ in range(10):
                    identity.observe_violation(v)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert 0.0 <= identity.trust_score <= 100.0

    def test_concurrent_push_with_identity(self):
        identity = AgentIdentity.register(agent_id="a")
        sentinel = Sentinel(detectors=[])
        sentinel.attach_identity(identity)
        errors: list[Exception] = []

        def worker(i: int):
            try:
                for j in range(10):
                    sentinel.push(_event(event_id=f"e-{i}-{j}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        with sentinel._lock:
            assert len(sentinel._events) == 50

    def test_concurrent_sqlite_save_load(self, tmp_path):
        store = SQLiteIdentityStore(db_path=tmp_path / "concurrent.db")
        errors: list[Exception] = []

        def worker(name: str):
            try:
                identity = AgentIdentity.register(agent_id=name, store=store)
                store.save(identity)
                loaded = store.load(name)
                assert loaded is not None
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"agent-{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        store.close()
