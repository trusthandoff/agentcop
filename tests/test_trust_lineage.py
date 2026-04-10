"""Tests for agentcop.trust.lineage — ExecutionLineage."""
from __future__ import annotations

import json
import threading

from agentcop.trust.lineage import ExecutionLineage
from agentcop.trust.models import ExecutionNode


def _node(node_id: str, agent_id: str = "agent-a", tool_calls: list[str] | None = None) -> ExecutionNode:
    return ExecutionNode(
        node_id=node_id,
        agent_id=agent_id,
        tool_calls=tool_calls or ["search"],
        context_hash=f"ctx-{node_id}",
        output_hash=f"out-{node_id}",
        duration_ms=42,
    )


class TestExecutionLineageBasic:
    def test_empty_lineage(self):
        el = ExecutionLineage()
        assert el.get_lineage("chain-1") == []

    def test_record_step_appends(self):
        el = ExecutionLineage()
        n = _node("n1")
        el.record_step(n, "chain-1")
        lineage = el.get_lineage("chain-1")
        assert len(lineage) == 1
        assert lineage[0].node_id == "n1"

    def test_get_lineage_returns_in_order(self):
        el = ExecutionLineage()
        for i in range(5):
            el.record_step(_node(f"n{i}"), "chain-1")
        lineage = el.get_lineage("chain-1")
        assert [n.node_id for n in lineage] == ["n0", "n1", "n2", "n3", "n4"]

    def test_default_chain_id(self):
        el = ExecutionLineage()
        el.record_step(_node("n1"))
        assert len(el.get_lineage("default")) == 1

    def test_multiple_chains_coexist(self):
        el = ExecutionLineage()
        el.record_step(_node("a"), "chain-a")
        el.record_step(_node("b"), "chain-b")
        assert el.get_lineage("chain-a")[0].node_id == "a"
        assert el.get_lineage("chain-b")[0].node_id == "b"


class TestExecutionLineageDiff:
    def test_identical_lineages_no_diffs(self):
        el = ExecutionLineage()
        n = _node("n1")
        el.record_step(n, "a")
        el.record_step(n, "b")
        diffs = el.diff_lineages("a", "b")
        assert diffs == []

    def test_length_diff(self):
        el = ExecutionLineage()
        el.record_step(_node("n1"), "a")
        el.record_step(_node("n1"), "b")
        el.record_step(_node("n2"), "b")
        diffs = el.diff_lineages("a", "b")
        assert any("length" in d for d in diffs)

    def test_agent_id_diff(self):
        el = ExecutionLineage()
        el.record_step(_node("n1", "agent-x"), "a")
        el.record_step(_node("n1", "agent-y"), "b")
        diffs = el.diff_lineages("a", "b")
        assert any("agent_id" in d for d in diffs)

    def test_context_hash_diff(self):
        el = ExecutionLineage()
        n_a = ExecutionNode("n1", "a", [], "ctx-x", "out-1", 10)
        n_b = ExecutionNode("n1", "a", [], "ctx-y", "out-1", 10)
        el.record_step(n_a, "a")
        el.record_step(n_b, "b")
        diffs = el.diff_lineages("a", "b")
        assert any("context_hash" in d for d in diffs)

    def test_output_hash_diff(self):
        el = ExecutionLineage()
        n_a = ExecutionNode("n1", "a", [], "ctx-1", "out-x", 10)
        n_b = ExecutionNode("n1", "a", [], "ctx-1", "out-y", 10)
        el.record_step(n_a, "a")
        el.record_step(n_b, "b")
        diffs = el.diff_lineages("a", "b")
        assert any("output_hash" in d for d in diffs)

    def test_tool_calls_diff(self):
        el = ExecutionLineage()
        el.record_step(_node("n1", tool_calls=["A"]), "a")
        el.record_step(_node("n1", tool_calls=["B"]), "b")
        diffs = el.diff_lineages("a", "b")
        assert any("tool_calls" in d for d in diffs)

    def test_extra_steps_reported(self):
        el = ExecutionLineage()
        el.record_step(_node("n1"), "a")
        el.record_step(_node("n1"), "b")
        el.record_step(_node("n2"), "b")
        diffs = el.diff_lineages("a", "b")
        assert any("only in" in d for d in diffs)


class TestExecutionLineageExport:
    def test_export_text_format(self):
        el = ExecutionLineage()
        el.record_step(_node("n1", "agent-a"), "chain-1")
        text = el.export_lineage("chain-1", format="text")
        assert "chain-1" in text
        assert "agent-a" in text

    def test_export_json_format(self):
        el = ExecutionLineage()
        el.record_step(_node("n1"), "chain-1")
        out = el.export_lineage("chain-1", format="json")
        data = json.loads(out)
        assert data["chain_id"] == "chain-1"
        assert len(data["steps"]) == 1

    def test_export_json_has_all_fields(self):
        el = ExecutionLineage()
        el.record_step(_node("n1", tool_calls=["tool_a"]), "c")
        data = json.loads(el.export_lineage("c", format="json"))
        step = data["steps"][0]
        assert "node_id" in step
        assert "agent_id" in step
        assert "tool_calls" in step

    def test_export_mermaid_format(self):
        el = ExecutionLineage()
        el.record_step(_node("n1", "agent-a"), "chain-1")
        el.record_step(_node("n2", "agent-b"), "chain-1")
        mermaid = el.export_lineage("chain-1", format="mermaid")
        assert "flowchart LR" in mermaid

    def test_export_mermaid_has_nodes(self):
        el = ExecutionLineage()
        el.record_step(_node("n1"), "chain-1")
        mermaid = el.export_lineage("chain-1", format="mermaid")
        assert "N0" in mermaid

    def test_export_empty_chain(self):
        el = ExecutionLineage()
        text = el.export_lineage("nothing", format="text")
        assert "Steps: 0" in text


class TestExecutionLineageThreadSafety:
    def test_concurrent_record_step(self):
        el = ExecutionLineage()
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                el.record_step(_node(f"n{i}"), "chain-1")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(el.get_lineage("chain-1")) == 20
