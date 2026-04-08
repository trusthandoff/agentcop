"""
Data models for the reliability module.

These are pure Pydantic value objects — no logic, no I/O.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """A single tool invocation within an AgentRun."""

    tool_name: str
    args_hash: str  # SHA256 of serialised arguments
    result_hash: str  # SHA256 of the result
    duration_ms: int
    success: bool
    retry_count: int


class AgentRun(BaseModel):
    """One complete agent execution."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    timestamp: datetime
    input_hash: str  # SHA256 of input
    tool_calls: list[ToolCall] = Field(default_factory=list)
    execution_path: list[str] = Field(default_factory=list)
    duration_ms: int
    success: bool
    retry_count: int
    output_hash: str  # SHA256 of output
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReliabilityReport(BaseModel):
    """Aggregated reliability assessment over a window of agent runs."""

    agent_id: str
    window_runs: int
    window_hours: int
    path_entropy: float  # 0-1 (0=stable, 1=chaotic)
    tool_variance: float  # 0-1
    retry_explosion_score: float  # 0-1
    branch_instability: float  # 0-1
    reliability_score: int  # 0-100
    reliability_tier: Literal["STABLE", "VARIABLE", "UNSTABLE", "CRITICAL"]
    drift_detected: bool
    drift_description: str | None = None
    top_issues: list[str] = Field(default_factory=list)
    trend: Literal["IMPROVING", "STABLE", "DEGRADING"]
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tokens_per_run_avg: float
    cost_per_run_avg: float
    token_spike_detected: bool
