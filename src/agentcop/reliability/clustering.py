"""
Cross-agent reliability clustering.

Groups agents by their four-dimensional reliability fingerprint
[path_entropy, tool_variance, retry_score, branch_instability] using K-means
with k-means++ initialisation.

No external dependencies — pure stdlib.
"""

import math
import random
from dataclasses import dataclass

from .models import AgentRun, ReliabilityReport

# ---------------------------------------------------------------------------
# K-means (stdlib-only)
# ---------------------------------------------------------------------------


def _euclidean(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=False)))


def _kmeans(
    points: list[list[float]],
    k: int,
    *,
    max_iter: int = 100,
    seed: int = 42,
) -> list[int]:
    """K-means with k-means++ initialisation.  Returns cluster index per point."""
    n = len(points)
    if n == 0:
        return []
    if n <= k:
        return list(range(n))

    rng = random.Random(seed)

    # k-means++ seed selection
    centroids: list[list[float]] = [list(points[rng.randrange(n)])]
    while len(centroids) < k:
        dists = [min(_euclidean(p, c) ** 2 for c in centroids) for p in points]
        total = sum(dists)
        if total == 0.0:
            centroids.append(list(points[rng.randrange(n)]))
            continue
        threshold = rng.random() * total
        cumulative = 0.0
        chosen = n - 1
        for i, d in enumerate(dists):
            cumulative += d
            if cumulative >= threshold:
                chosen = i
                break
        centroids.append(list(points[chosen]))

    assignments = [0] * n
    for _ in range(max_iter):
        new_assignments = [
            min(range(k), key=lambda ci: _euclidean(p, centroids[ci])) for p in points
        ]
        if new_assignments == assignments:
            break
        assignments = new_assignments
        dim = len(points[0])
        for ci in range(k):
            cluster_pts = [points[i] for i, a in enumerate(assignments) if a == ci]
            if cluster_pts:
                centroids[ci] = [
                    sum(p[d] for p in cluster_pts) / len(cluster_pts) for d in range(dim)
                ]
    return assignments


# ---------------------------------------------------------------------------
# Fingerprint helpers
# ---------------------------------------------------------------------------


def _fingerprint(report: ReliabilityReport) -> list[float]:
    return [
        report.path_entropy,
        report.tool_variance,
        report.retry_explosion_score,
        report.branch_instability,
    ]


def _tier_from_centroid(centroid: list[float]) -> str:
    pe, tv, retry, bi = centroid
    penalty = pe * 0.25 + tv * 0.25 + retry * 0.30 + bi * 0.20
    score = max(0, min(100, int(round(100.0 - penalty * 100.0))))
    if score >= 80:
        return "STABLE"
    if score >= 60:
        return "VARIABLE"
    if score >= 40:
        return "UNSTABLE"
    return "CRITICAL"


def _describe_pattern(centroid: list[float]) -> str:
    pe, tv, retry, bi = centroid
    parts: list[str] = []
    if pe > 0.5:
        parts.append("high path entropy")
    if tv > 0.5:
        parts.append("inconsistent tool usage")
    if retry > 0.4:
        parts.append("excessive retries")
    if bi > 0.5:
        parts.append("branch instability")
    return ", ".join(parts) if parts else "stable, consistent behaviour"


def _recommend_action(tier: str) -> str:
    if tier == "STABLE":
        return "Continue monitoring; no immediate action required."
    if tier == "VARIABLE":
        return "Review agent configuration and tool selection; consider adding retry backoff."
    if tier == "UNSTABLE":
        return (
            "Investigate root cause of instability; "
            "add circuit-breaker pattern or reduce tool set."
        )
    # CRITICAL
    return (
        "Immediate intervention required; consider suspending agent and reviewing execution logs."
    )


# ---------------------------------------------------------------------------
# AgentCluster dataclass
# ---------------------------------------------------------------------------


@dataclass
class AgentCluster:
    """A group of agents sharing a reliability fingerprint pattern."""

    cluster_id: int
    agent_ids: list[str]
    centroid: list[float]  # [path_entropy, tool_variance, retry_score, branch_instability]
    shared_pattern: str  # human-readable description of dominant characteristics
    recommended_action: str
    tier: str  # STABLE | VARIABLE | UNSTABLE | CRITICAL


# ---------------------------------------------------------------------------
# AgentClusterAnalyzer
# ---------------------------------------------------------------------------


class AgentClusterAnalyzer:
    """
    Groups agents by their reliability fingerprints using K-means clustering.

    Accepts either pre-computed :class:`~agentcop.reliability.models.ReliabilityReport`
    objects or raw ``{agent_id: [AgentRun]}`` dicts (metrics computed on the fly).

    Usage::

        analyzer = AgentClusterAnalyzer(k=3)

        # From pre-computed reports
        clusters = analyzer.cluster_reports(reports)

        # From raw runs — metrics computed internally
        clusters = analyzer.cluster_runs({"agent-a": runs_a, "agent-b": runs_b})

        for cluster in clusters:
            print(cluster.tier, cluster.shared_pattern)
            print("  agents:", cluster.agent_ids)
            print("  action:", cluster.recommended_action)

    Parameters
    ----------
    k:
        Number of clusters (default 3: one per broad reliability tier).
    seed:
        Random seed for k-means++ initialisation (default 42 for reproducibility).
    """

    def __init__(self, k: int = 3, *, seed: int = 42) -> None:
        self._k = k
        self._seed = seed

    def cluster_reports(self, reports: list[ReliabilityReport]) -> list[AgentCluster]:
        """Cluster agents from pre-computed ReliabilityReports."""
        if not reports:
            return []
        return self._build_clusters(
            [r.agent_id for r in reports],
            [_fingerprint(r) for r in reports],
        )

    def cluster_runs(self, agent_runs: dict[str, list[AgentRun]]) -> list[AgentCluster]:
        """Cluster agents from raw run lists, computing metrics on the fly."""
        from .metrics import (
            BranchInstabilityAnalyzer,
            PathEntropyCalculator,
            RetryExplosionDetector,
            ToolVarianceCalculator,
        )

        pe_calc = PathEntropyCalculator()
        tv_calc = ToolVarianceCalculator()
        retry_det = RetryExplosionDetector()
        bi_calc = BranchInstabilityAnalyzer()

        agent_ids: list[str] = []
        fingerprints: list[list[float]] = []
        for agent_id, runs in agent_runs.items():
            if not runs:
                continue
            retry_score, _ = retry_det.calculate(runs)
            agent_ids.append(agent_id)
            fingerprints.append(
                [
                    pe_calc.calculate(runs),
                    tv_calc.calculate(runs),
                    retry_score,
                    bi_calc.calculate(runs),
                ]
            )
        return self._build_clusters(agent_ids, fingerprints)

    # ── Internal ───────────────────────────────────────────────────────────

    def _build_clusters(
        self,
        agent_ids: list[str],
        fingerprints: list[list[float]],
    ) -> list[AgentCluster]:
        if not agent_ids:
            return []

        k = min(self._k, len(agent_ids))
        assignments = _kmeans(fingerprints, k, seed=self._seed)

        cluster_agents: dict[int, list[str]] = {}
        cluster_fps: dict[int, list[list[float]]] = {}
        for i, (agent_id, fp) in enumerate(zip(agent_ids, fingerprints, strict=False)):
            ci = assignments[i]
            cluster_agents.setdefault(ci, []).append(agent_id)
            cluster_fps.setdefault(ci, []).append(fp)

        clusters: list[AgentCluster] = []
        for ci in sorted(cluster_agents):
            pts = cluster_fps[ci]
            dim = len(pts[0])
            centroid = [sum(p[d] for p in pts) / len(pts) for d in range(dim)]
            tier = _tier_from_centroid(centroid)
            clusters.append(
                AgentCluster(
                    cluster_id=ci,
                    agent_ids=sorted(cluster_agents[ci]),
                    centroid=centroid,
                    shared_pattern=_describe_pattern(centroid),
                    recommended_action=_recommend_action(tier),
                    tier=tier,
                )
            )
        return clusters
