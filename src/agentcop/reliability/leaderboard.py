"""
Cross-agent reliability leaderboard.

Ranks all tracked agents by reliability score and computes each agent's
percentile relative to the group::

    from agentcop.reliability.leaderboard import ReliabilityLeaderboard
    from agentcop.reliability.store import ReliabilityStore

    store = ReliabilityStore()
    board = ReliabilityLeaderboard(store)
    rankings = board.rank(agent_ids=["agent-a", "agent-b", "agent-c"])

    for entry in rankings:
        print(entry.rank, entry.agent_id, entry.reliability_score,
              entry.percentile_description)
    # → 1  agent-b  91  more reliable than 100% of tracked agents
    # → 2  agent-c  78  more reliable than 50% of tracked agents
    # → 3  agent-a  62  more reliable than 0% of tracked agents
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LeaderboardEntry:
    """One agent's position in the reliability leaderboard."""

    rank: int
    """1-based rank (1 = most reliable)."""

    agent_id: str
    """Agent identifier."""

    reliability_score: int
    """Score 0-100 used for ranking."""

    reliability_tier: str
    """One of ``STABLE``, ``VARIABLE``, ``UNSTABLE``, ``CRITICAL``."""

    window_runs: int
    """Number of runs used to compute this score."""

    percentile: float
    """0-100: fraction of agents this agent outperforms."""

    @property
    def percentile_description(self) -> str:
        """Human-readable percentile string.

        Examples:

        - ``"more reliable than 73% of tracked agents"``
        - ``"more reliable than 100% of tracked agents"``
        - ``"more reliable than 0% of tracked agents"``
        """
        pct = int(round(self.percentile))
        return f"more reliable than {pct}% of tracked agents"


class ReliabilityLeaderboard:
    """Ranks agents by reliability score with percentile calculations.

    Args:
        store: A :class:`~agentcop.reliability.store.ReliabilityStore` used to
               fetch per-agent reports.  Pass ``None`` if you will supply
               pre-computed reports via :meth:`rank_reports`.

    Usage::

        board = ReliabilityLeaderboard(store)
        rankings = board.rank(["agent-a", "agent-b"], window_hours=24)
        top = rankings[0]
        print(top.agent_id, top.percentile_description)
    """

    def __init__(self, store: object | None = None) -> None:
        self._store = store

    # ── Public API ────────────────────────────────────────────────────────

    def rank(
        self,
        agent_ids: list[str],
        *,
        window_hours: int = 24,
    ) -> list[LeaderboardEntry]:
        """Fetch reports for *agent_ids* and return a ranked leaderboard.

        Agents with no recorded runs are included with ``reliability_score=0``.

        Args:
            agent_ids:    IDs of agents to compare.
            window_hours: Analysis window passed to ``store.get_report()``.

        Returns:
            List of :class:`LeaderboardEntry` sorted by rank ascending (best first).

        Raises:
            RuntimeError: When no store was provided at construction time.
        """
        if self._store is None:
            raise RuntimeError(
                "ReliabilityLeaderboard requires a store — "
                "pass store=... at construction or use rank_reports()."
            )
        from .models import ReliabilityReport

        reports: list[ReliabilityReport] = [
            self._store.get_report(aid, window_hours=window_hours)  # type: ignore[attr-defined]
            for aid in agent_ids
        ]
        return self.rank_reports(reports)

    def rank_reports(
        self,
        reports: list[object],
    ) -> list[LeaderboardEntry]:
        """Rank agents from pre-computed :class:`~agentcop.reliability.models.ReliabilityReport` objects.

        Useful when you already have reports and want to avoid an extra DB round-trip.

        Args:
            reports: List of :class:`~agentcop.reliability.models.ReliabilityReport`.

        Returns:
            List of :class:`LeaderboardEntry` sorted by rank ascending (best first).
        """
        if not reports:
            return []

        # Sort descending by score so rank 1 = best
        sorted_reports = sorted(reports, key=lambda r: r.reliability_score, reverse=True)  # type: ignore[attr-defined]
        n = len(sorted_reports)

        entries: list[LeaderboardEntry] = []
        for i, report in enumerate(sorted_reports):
            # Percentile = fraction of *other* agents that this agent beats
            agents_below = sum(
                1
                for other in sorted_reports
                if other.reliability_score < report.reliability_score  # type: ignore[attr-defined]
            )
            percentile = (agents_below / (n - 1) * 100.0) if n > 1 else 100.0

            entries.append(
                LeaderboardEntry(
                    rank=i + 1,
                    agent_id=report.agent_id,  # type: ignore[attr-defined]
                    reliability_score=report.reliability_score,  # type: ignore[attr-defined]
                    reliability_tier=report.reliability_tier,  # type: ignore[attr-defined]
                    window_runs=report.window_runs,  # type: ignore[attr-defined]
                    percentile=round(percentile, 1),
                )
            )
        return entries

    def summary(self, entries: list[LeaderboardEntry]) -> str:
        """Return a plain-text multi-line leaderboard summary.

        Args:
            entries: Output of :meth:`rank` or :meth:`rank_reports`.

        Returns:
            A formatted string suitable for printing to a terminal.
        """
        lines: list[str] = ["Reliability Leaderboard", "=" * 50]
        for e in entries:
            lines.append(
                f"  #{e.rank:>2}  {e.agent_id:<30}  "
                f"{e.reliability_score:>3}/100  {e.reliability_tier:<8}  "
                f"{e.percentile_description}"
            )
        return "\n".join(lines)
