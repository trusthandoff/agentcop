"""
Prometheus metrics export for agent reliability.

Generates Prometheus text format (exposition format v0.0.4) for 8 gauges
per agent::

    from agentcop.reliability.prometheus import PrometheusExporter
    from agentcop.reliability.store import ReliabilityStore

    store = ReliabilityStore()
    exporter = PrometheusExporter(store)
    print(exporter.export(["agent-a", "agent-b"]))

Output format::

    # HELP agentcop_reliability_score Agent reliability score (0-100)
    # TYPE agentcop_reliability_score gauge
    agentcop_reliability_score{agent_id="agent-a"} 87.0
    ...

The eight gauges are:

1. ``agentcop_reliability_score``      — overall score 0-100
2. ``agentcop_path_entropy``           — normalized path entropy 0-1
3. ``agentcop_tool_variance``          — normalized tool variance 0-1
4. ``agentcop_retry_explosion_score``  — normalized retry explosion 0-1
5. ``agentcop_branch_instability``     — normalized branch instability 0-1
6. ``agentcop_tokens_per_run_avg``     — average tokens per run
7. ``agentcop_cost_per_run_avg``       — average cost per run in USD
8. ``agentcop_window_runs_total``      — number of runs in the analysis window
"""

from __future__ import annotations

from .models import ReliabilityReport

_METRICS: list[tuple[str, str, str]] = [
    # (metric_name, help_text, attribute_on_report)
    ("agentcop_reliability_score", "Agent reliability score (0-100)", "reliability_score"),
    ("agentcop_path_entropy", "Normalized path entropy (0-1)", "path_entropy"),
    ("agentcop_tool_variance", "Normalized tool variance (0-1)", "tool_variance"),
    (
        "agentcop_retry_explosion_score",
        "Normalized retry explosion score (0-1)",
        "retry_explosion_score",
    ),
    (
        "agentcop_branch_instability",
        "Normalized branch instability (0-1)",
        "branch_instability",
    ),
    (
        "agentcop_tokens_per_run_avg",
        "Average total tokens consumed per run",
        "tokens_per_run_avg",
    ),
    (
        "agentcop_cost_per_run_avg",
        "Average estimated cost per run in USD",
        "cost_per_run_avg",
    ),
    (
        "agentcop_window_runs_total",
        "Number of runs in the analysis window",
        "window_runs",
    ),
]


def _format_label(agent_id: str) -> str:
    """Escape and format agent_id as a Prometheus label."""
    escaped = agent_id.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'agent_id="{escaped}"'


def _metric_block(name: str, help_text: str, samples: list[tuple[str, float]]) -> str:
    """Format a single metric block in Prometheus exposition format."""
    lines: list[str] = [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} gauge",
    ]
    for label, value in samples:
        lines.append(f"{name}{{{label}}} {value}")
    return "\n".join(lines)


def reports_to_prometheus(
    reports: list[ReliabilityReport],
    *,
    timestamp_ms: int | None = None,
) -> str:
    """Convert a list of :class:`~agentcop.reliability.models.ReliabilityReport`
    objects to Prometheus exposition format.

    Args:
        reports:      Pre-computed reports, one per agent.
        timestamp_ms: Optional Unix timestamp in milliseconds to append to each
                      sample line (per Prometheus exposition spec).

    Returns:
        A string in Prometheus text exposition format (v0.0.4), ending with a
        trailing newline.
    """
    if not reports:
        return ""

    blocks: list[str] = []
    ts_suffix = f" {timestamp_ms}" if timestamp_ms is not None else ""

    for name, help_text, attr in _METRICS:
        samples: list[tuple[str, float]] = []
        for report in reports:
            value = float(getattr(report, attr))
            label = _format_label(report.agent_id)
            samples.append((label, value))

        lines: list[str] = [
            f"# HELP {name} {help_text}",
            f"# TYPE {name} gauge",
        ]
        for label, value in samples:
            lines.append(f"{name}{{{label}}} {value}{ts_suffix}")
        blocks.append("\n".join(lines))

    return "\n".join(blocks) + "\n"


class PrometheusExporter:
    """Fetches reliability reports and formats them as Prometheus metrics.

    Args:
        store:        A :class:`~agentcop.reliability.store.ReliabilityStore`.
        window_hours: Default analysis window when fetching reports.

    Usage::

        exporter = PrometheusExporter(store, window_hours=24)
        print(exporter.export(["agent-a", "agent-b"]))
    """

    def __init__(self, store: object, *, window_hours: int = 24) -> None:
        self._store = store
        self._window_hours = window_hours

    def export(
        self,
        agent_ids: list[str],
        *,
        window_hours: int | None = None,
        timestamp_ms: int | None = None,
    ) -> str:
        """Fetch reports for *agent_ids* and return Prometheus text output.

        Args:
            agent_ids:    IDs of agents to export metrics for.
            window_hours: Override the default window (set at construction).
            timestamp_ms: Optional Unix timestamp in milliseconds for each sample.

        Returns:
            Prometheus text exposition format string.
        """
        wh = window_hours if window_hours is not None else self._window_hours
        reports: list[ReliabilityReport] = [
            self._store.get_report(aid, window_hours=wh)  # type: ignore[attr-defined]
            for aid in agent_ids
        ]
        return reports_to_prometheus(reports, timestamp_ms=timestamp_ms)

    def export_reports(
        self,
        reports: list[ReliabilityReport],
        *,
        timestamp_ms: int | None = None,
    ) -> str:
        """Format pre-computed reports as Prometheus metrics.

        Args:
            reports:      List of :class:`~agentcop.reliability.models.ReliabilityReport`.
            timestamp_ms: Optional Unix timestamp in milliseconds.

        Returns:
            Prometheus text exposition format string.
        """
        return reports_to_prometheus(reports, timestamp_ms=timestamp_ms)
