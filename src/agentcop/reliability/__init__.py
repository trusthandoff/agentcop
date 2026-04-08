"""
agentcop.reliability — agent reliability scoring, storage, and prediction.

Core usage::

    from agentcop.reliability import AgentRun, ToolCall, ReliabilityEngine
    from agentcop.reliability.store import ReliabilityStore
    from agentcop.reliability.instrumentation import ReliabilityTracer

    store  = ReliabilityStore()          # SQLite persistence
    engine = ReliabilityEngine()

    with ReliabilityTracer("my-agent", store=store) as tracer:
        tracer.record_tool_call("bash", args, result)
        tracer.record_branch("chose_path_A")
        tracer.record_tokens(input=100, output=250, model="gpt-4o")

    report, events = engine.compute_report("my-agent", store.get_runs("my-agent"))
    print(report.reliability_tier)   # STABLE | VARIABLE | UNSTABLE | CRITICAL

Optional import — this module is never imported from agentcop.__init__
so it does not require any additional dependencies beyond pydantic.
"""

from .badge_integration import (
    RELIABILITY_EMOJI,
    combined_badge_text,
    reliability_emoji,
    reliability_markdown_badge,
    reliability_shield_url,
)
from .causality import CausalAnalyzer, CausalFinding
from .clustering import AgentCluster, AgentClusterAnalyzer
from .events import (
    branch_instability_critical,
    reliability_drift_detected,
    retry_explosion,
    token_budget_spike,
    tool_variance_spike,
)
from .instrumentation import ReliabilityTracer, wrap_for_reliability
from .leaderboard import LeaderboardEntry, ReliabilityLeaderboard
from .metrics import (
    BranchInstabilityAnalyzer,
    DriftDetector,
    ExplosionEvent,
    PathEntropyCalculator,
    ReliabilityEngine,
    ReliabilityScorer,
    RetryExplosionDetector,
    TokenBudgetAnalyzer,
    ToolVarianceCalculator,
)
from .models import AgentRun, ReliabilityReport, ToolCall
from .prediction import Prediction, ReliabilityPredictor
from .prometheus import PrometheusExporter, reports_to_prometheus
from .store import ReliabilityStore

__all__ = [
    # Data models
    "AgentRun",
    "ToolCall",
    "ReliabilityReport",
    # Calculators
    "PathEntropyCalculator",
    "ToolVarianceCalculator",
    "RetryExplosionDetector",
    "ExplosionEvent",
    "BranchInstabilityAnalyzer",
    "TokenBudgetAnalyzer",
    "ReliabilityScorer",
    "DriftDetector",
    # Engine
    "ReliabilityEngine",
    # Storage
    "ReliabilityStore",
    # Instrumentation
    "ReliabilityTracer",
    "wrap_for_reliability",
    # Causal analysis
    "CausalAnalyzer",
    "CausalFinding",
    # Prediction
    "ReliabilityPredictor",
    "Prediction",
    # Clustering
    "AgentClusterAnalyzer",
    "AgentCluster",
    # SentinelEvent factories
    "reliability_drift_detected",
    "retry_explosion",
    "branch_instability_critical",
    "tool_variance_spike",
    "token_budget_spike",
    # Badge integration
    "RELIABILITY_EMOJI",
    "reliability_emoji",
    "combined_badge_text",
    "reliability_shield_url",
    "reliability_markdown_badge",
    # Leaderboard
    "ReliabilityLeaderboard",
    "LeaderboardEntry",
    # Prometheus
    "PrometheusExporter",
    "reports_to_prometheus",
]
