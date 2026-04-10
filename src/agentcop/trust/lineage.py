"""
ExecutionLineage — full execution audit trail across multi-agent pipelines.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Literal

from .models import ExecutionNode

_log = logging.getLogger(__name__)


class ExecutionLineage:
    """
    Records and exports the step-by-step execution history of a chain.

    Thread-safe; keyed by chain_id so multiple concurrent chains coexist.
    """

    def __init__(self) -> None:
        self._steps: dict[str, list[ExecutionNode]] = {}
        self._lock = threading.Lock()

    def record_step(self, node: ExecutionNode, chain_id: str = "default") -> None:
        """Append an ExecutionNode to the lineage for the given chain_id."""
        with self._lock:
            self._steps.setdefault(chain_id, []).append(node)

    def get_lineage(self, chain_id: str = "default") -> list[ExecutionNode]:
        """Return execution nodes for the given chain_id in insertion order."""
        with self._lock:
            return list(self._steps.get(chain_id, []))

    def diff_lineages(self, chain_a: str, chain_b: str) -> list[str]:
        """
        Return a list of human-readable differences between two lineages.

        Compares step count, node IDs, agent IDs, hashes, and tool calls.
        """
        with self._lock:
            nodes_a = list(self._steps.get(chain_a, []))
            nodes_b = list(self._steps.get(chain_b, []))

        diffs: list[str] = []
        len_a, len_b = len(nodes_a), len(nodes_b)

        if len_a != len_b:
            diffs.append(f"length differs: {chain_a}={len_a} vs {chain_b}={len_b}")

        for i in range(min(len_a, len_b)):
            a, b = nodes_a[i], nodes_b[i]
            if a.node_id != b.node_id:
                diffs.append(f"step[{i}] node_id: {a.node_id!r} vs {b.node_id!r}")
            if a.agent_id != b.agent_id:
                diffs.append(f"step[{i}] agent_id: {a.agent_id!r} vs {b.agent_id!r}")
            if a.context_hash != b.context_hash:
                diffs.append(
                    f"step[{i}] context_hash: {a.context_hash[:8]} vs {b.context_hash[:8]}"
                )
            if a.output_hash != b.output_hash:
                diffs.append(f"step[{i}] output_hash: {a.output_hash[:8]} vs {b.output_hash[:8]}")
            if a.tool_calls != b.tool_calls:
                diffs.append(f"step[{i}] tool_calls: {a.tool_calls} vs {b.tool_calls}")

        # Nodes only in the longer chain
        for i in range(min(len_a, len_b), max(len_a, len_b)):
            if i < len_a:
                diffs.append(f"step[{i}] only in {chain_a}: node {nodes_a[i].node_id}")
            else:
                diffs.append(f"step[{i}] only in {chain_b}: node {nodes_b[i].node_id}")

        return diffs

    def export_lineage(
        self,
        chain_id: str = "default",
        format: Literal["json", "mermaid", "text"] = "text",
    ) -> str:
        """Export lineage as JSON, a Mermaid flowchart, or plain text."""
        nodes = self.get_lineage(chain_id)

        if format == "json":
            data = [
                {
                    "node_id": n.node_id,
                    "agent_id": n.agent_id,
                    "tool_calls": n.tool_calls,
                    "context_hash": n.context_hash,
                    "output_hash": n.output_hash,
                    "duration_ms": n.duration_ms,
                }
                for n in nodes
            ]
            return json.dumps({"chain_id": chain_id, "steps": data}, indent=2)

        if format == "mermaid":
            lines = ["flowchart LR"]
            for i, node in enumerate(nodes):
                label = f"{node.agent_id}\\n[{node.node_id[:8]}]"
                lines.append(f'    N{i}["{label}"]')
                if i > 0:
                    tools = ", ".join(node.tool_calls) or "—"
                    lines.append(f'    N{i - 1} -->|"{tools}"| N{i}')
            return "\n".join(lines)

        # text format
        lines = [f"Chain: {chain_id}", f"Steps: {len(nodes)}"]
        for i, n in enumerate(nodes):
            tools_str = str(n.tool_calls)
            lines.append(
                f"  [{i}] {n.agent_id}/{n.node_id[:8]} "
                f"tools={tools_str} ctx={n.context_hash[:8]} "
                f"out={n.output_hash[:8]} {n.duration_ms}ms"
            )
        return "\n".join(lines)
