"""
CLI entry point for agentcop reliability subcommands.

Exposed as ``agentcop reliability`` via the ``[project.scripts]`` entry point
in ``pyproject.toml``.

Subcommands
-----------
report   Print a reliability report for one or more agents.
compare  Side-by-side comparison of two agents.
watch    Continuously refresh the report (Ctrl-C to stop).
export   Export report(s) as JSON or Prometheus metrics.

Usage examples::

    agentcop reliability report --agent my-agent
    agentcop reliability compare --agents agent-a agent-b
    agentcop reliability watch --agent my-agent --interval 10
    agentcop reliability export --agent my-agent --format prometheus
    agentcop reliability export --agents agent-a agent-b --format json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def _get_store(db_path: str) -> Any:
    """Open (or create) a ReliabilityStore at *db_path*."""
    from .store import ReliabilityStore

    return ReliabilityStore(db_path)


def _print_report(report: Any, *, verbose: bool = False) -> None:
    """Pretty-print a single ReliabilityReport to stdout."""
    tier_emoji = {
        "STABLE": "🟢",
        "VARIABLE": "🟡",
        "UNSTABLE": "🟠",
        "CRITICAL": "🔴",
    }
    trend_arrow = {"IMPROVING": "↑", "STABLE": "→", "DEGRADING": "↓"}
    emoji = tier_emoji.get(report.reliability_tier, "❓")
    arrow = trend_arrow.get(report.trend, "?")

    print(f"\n{'─' * 56}")
    print(f"  Agent: {report.agent_id}")
    print(
        f"  Score: {report.reliability_score}/100  {emoji} {report.reliability_tier}  {arrow} {report.trend}"
    )
    print(f"  Window: {report.window_runs} runs / {report.window_hours}h")
    print(f"  Computed: {report.computed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if verbose:
        print("\n  Metrics:")
        print(f"    path_entropy          {report.path_entropy:.4f}")
        print(f"    tool_variance         {report.tool_variance:.4f}")
        print(f"    retry_explosion       {report.retry_explosion_score:.4f}")
        print(f"    branch_instability    {report.branch_instability:.4f}")
        print(f"    tokens_per_run_avg    {report.tokens_per_run_avg:.1f}")
        print(f"    cost_per_run_avg      ${report.cost_per_run_avg:.6f}")
        print(f"    token_spike           {'YES' if report.token_spike_detected else 'no'}")
        print(f"    drift_detected        {'YES' if report.drift_detected else 'no'}")
        if report.drift_description:
            print(f"    drift_description     {report.drift_description}")
        if report.top_issues:
            print("\n  Top issues:")
            for issue in report.top_issues:
                print(f"    • {issue}")
    print(f"{'─' * 56}\n")


# ---------------------------------------------------------------------------
# Subcommand: report
# ---------------------------------------------------------------------------


def cmd_report(args: argparse.Namespace) -> int:
    """Print a reliability report for one or more agents."""
    store = _get_store(args.db)
    agent_ids: list[str] = []
    if args.agent:
        agent_ids = [args.agent]
    elif args.agents:
        agent_ids = args.agents
    else:
        print("Error: specify --agent <id> or --agents <id> [id ...]", file=sys.stderr)
        return 1

    for aid in agent_ids:
        report = store.get_report(aid, window_hours=args.window_hours)
        _print_report(report, verbose=args.verbose)

    store.close()
    return 0


# ---------------------------------------------------------------------------
# Subcommand: compare
# ---------------------------------------------------------------------------


def cmd_compare(args: argparse.Namespace) -> int:
    """Side-by-side comparison of two or more agents."""
    if not args.agents or len(args.agents) < 2:
        print("Error: --agents requires at least two agent IDs", file=sys.stderr)
        return 1

    store = _get_store(args.db)
    reports = [store.get_report(aid, window_hours=args.window_hours) for aid in args.agents]
    store.close()

    # Build leaderboard
    from .leaderboard import ReliabilityLeaderboard

    board = ReliabilityLeaderboard()
    rankings = board.rank_reports(reports)
    print(board.summary(rankings))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: watch
# ---------------------------------------------------------------------------


def cmd_watch(args: argparse.Namespace) -> int:
    """Continuously refresh the reliability report."""
    if not args.agent:
        print("Error: --agent <id> is required for watch mode", file=sys.stderr)
        return 1

    store = _get_store(args.db)
    try:
        while True:
            # Clear screen
            print("\033[2J\033[H", end="")
            report = store.get_report(args.agent, window_hours=args.window_hours)
            _print_report(report, verbose=True)
            print(f"  Refreshing every {args.interval}s — Ctrl-C to stop")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        store.close()
    return 0


# ---------------------------------------------------------------------------
# Subcommand: export
# ---------------------------------------------------------------------------


def _report_to_dict(report: Any) -> dict[str, Any]:
    """Convert a ReliabilityReport to a JSON-serializable dict."""
    return {
        "agent_id": report.agent_id,
        "reliability_score": report.reliability_score,
        "reliability_tier": report.reliability_tier,
        "window_runs": report.window_runs,
        "window_hours": report.window_hours,
        "path_entropy": report.path_entropy,
        "tool_variance": report.tool_variance,
        "retry_explosion_score": report.retry_explosion_score,
        "branch_instability": report.branch_instability,
        "drift_detected": report.drift_detected,
        "drift_description": report.drift_description,
        "trend": report.trend,
        "tokens_per_run_avg": report.tokens_per_run_avg,
        "cost_per_run_avg": report.cost_per_run_avg,
        "token_spike_detected": report.token_spike_detected,
        "computed_at": report.computed_at.isoformat(),
        "top_issues": report.top_issues,
    }


def cmd_export(args: argparse.Namespace) -> int:
    """Export report(s) as JSON or Prometheus metrics."""
    store = _get_store(args.db)
    agent_ids: list[str] = []
    if args.agent:
        agent_ids = [args.agent]
    elif args.agents:
        agent_ids = args.agents
    else:
        print("Error: specify --agent <id> or --agents <id> [id ...]", file=sys.stderr)
        store.close()
        return 1

    reports = [store.get_report(aid, window_hours=args.window_hours) for aid in agent_ids]
    store.close()

    output: str
    if args.format == "prometheus":
        from .prometheus import reports_to_prometheus

        output = reports_to_prometheus(reports)
    else:  # json
        data = [_report_to_dict(r) for r in reports]
        output = json.dumps(data if len(data) > 1 else data[0], indent=2)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Exported to {args.output}")
    else:
        print(output)

    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentcop reliability",
        description="agentcop reliability — inspect, compare, and export agent reliability metrics.",
    )
    parser.add_argument(
        "--db",
        default="agentcop.db",
        help="Path to the SQLite database (default: agentcop.db).",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # ── report ────────────────────────────────────────────────────────────
    p_report = sub.add_parser("report", help="Print a reliability report.")
    _add_agent_args(p_report)
    p_report.add_argument(
        "--window-hours",
        type=int,
        default=24,
        metavar="N",
        help="Analysis window in hours (default: 24).",
    )
    p_report.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed metric breakdown.",
    )

    # ── compare ───────────────────────────────────────────────────────────
    p_compare = sub.add_parser("compare", help="Side-by-side leaderboard comparison.")
    p_compare.add_argument(
        "--agents",
        nargs="+",
        metavar="AGENT_ID",
        required=True,
        help="Two or more agent IDs to compare.",
    )
    p_compare.add_argument(
        "--window-hours",
        type=int,
        default=24,
        metavar="N",
        help="Analysis window in hours (default: 24).",
    )

    # ── watch ─────────────────────────────────────────────────────────────
    p_watch = sub.add_parser("watch", help="Live-refresh reliability report.")
    p_watch.add_argument("--agent", required=True, metavar="AGENT_ID")
    p_watch.add_argument(
        "--interval",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="Refresh interval in seconds (default: 5).",
    )
    p_watch.add_argument(
        "--window-hours",
        type=int,
        default=24,
        metavar="N",
    )

    # ── export ────────────────────────────────────────────────────────────
    p_export = sub.add_parser("export", help="Export metrics as JSON or Prometheus.")
    _add_agent_args(p_export)
    p_export.add_argument(
        "--format",
        choices=["json", "prometheus"],
        default="json",
        help="Output format (default: json).",
    )
    p_export.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="Write output to FILE instead of stdout.",
    )
    p_export.add_argument(
        "--window-hours",
        type=int,
        default=24,
        metavar="N",
    )

    return parser


def _add_agent_args(parser: argparse.ArgumentParser) -> None:
    """Add mutually exclusive --agent / --agents arguments."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--agent", metavar="AGENT_ID", help="Single agent ID.")
    group.add_argument(
        "--agents",
        nargs="+",
        metavar="AGENT_ID",
        help="Multiple agent IDs.",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_COMMANDS = {
    "report": cmd_report,
    "compare": cmd_compare,
    "watch": cmd_watch,
    "export": cmd_export,
}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns an exit code (0 = success)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
