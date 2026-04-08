"""
Predictive reliability — fires SentinelEvents BEFORE problems occur.

Uses simple ordinary least-squares linear regression over a sliding window
of the last N runs to project metric trajectories.  When a projection exceeds
a threshold before ``horizon_hours``, a ``reliability_prediction`` SentinelEvent
is emitted so the caller can act proactively.

No external ML dependencies — pure stdlib math.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agentcop.event import SentinelEvent

from .models import AgentRun

_SOURCE_SYSTEM = "agentcop.reliability"


# ---------------------------------------------------------------------------
# OLS helpers
# ---------------------------------------------------------------------------


def _ols(x: list[float], y: list[float]) -> tuple[float, float]:
    """Ordinary least-squares linear regression.

    Returns ``(slope, intercept)`` such that ``y ≈ slope * x + intercept``.
    Falls back to a zero-slope horizontal line when the system is degenerate.
    """
    n = len(x)
    if n < 2:
        return 0.0, (y[0] if y else 0.0)
    sx = sum(x)
    sy = sum(y)
    sxx = sum(xi * xi for xi in x)
    sxy = sum(xi * yi for xi, yi in zip(x, y))
    denom = n * sxx - sx * sx
    if denom == 0.0:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def _r_squared(
    x: list[float], y: list[float], slope: float, intercept: float
) -> float:
    """Coefficient of determination R² — how well the line fits the data."""
    if len(y) < 2:
        return 0.0
    y_mean = sum(y) / len(y)
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)
    if ss_tot == 0.0:
        return 1.0  # constant series: perfect fit
    ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))
    return max(0.0, 1.0 - ss_res / ss_tot)


# ---------------------------------------------------------------------------
# Prediction dataclass
# ---------------------------------------------------------------------------


@dataclass
class Prediction:
    """Projected future metric value with confidence estimate."""

    metric: str
    current_value: float
    predicted_value: float
    horizon_hours: float
    confidence: float          # R², 0-1
    will_exceed_threshold: bool
    threshold: float
    slope_per_hour: float
    description: str
    sentinel_event: SentinelEvent | None = None


# ---------------------------------------------------------------------------
# ReliabilityPredictor
# ---------------------------------------------------------------------------


class ReliabilityPredictor:
    """
    Sliding-window linear regression over metric time series.

    Uses the last ``window`` runs to estimate each metric's slope over time.
    When the projection at ``horizon_hours`` exceeds a threshold, a predictive
    :class:`~agentcop.event.SentinelEvent` is produced for the caller to push
    into a :class:`~agentcop.Sentinel`::

        predictor = ReliabilityPredictor()
        predictions = predictor.predict(runs, horizon_hours=2.0)
        for pred in predictions:
            if pred.sentinel_event:
                sentinel.push(pred.sentinel_event)
            print(pred.description)
        # → "WARNING: retry_count likely to exceed threshold (3.0) — ..."

    Parameters
    ----------
    window:
        Maximum number of recent runs to include in the regression (default 100).
    thresholds:
        Override default metric thresholds.  Pass ``None`` for a metric to use
        the built-in dynamic threshold (e.g. 2× current mean for ``total_tokens``).
    min_confidence:
        Minimum R² required to emit a prediction (default 0.2).
    """

    # Metric → threshold at which a warning fires.
    # None = dynamic (computed per-call).
    DEFAULT_THRESHOLDS: dict[str, float | None] = {
        "retry_count": 3.0,
        "tool_variance": 0.6,
        "path_entropy": 0.7,
        "total_tokens": None,  # dynamic: 2× current mean
    }

    def __init__(
        self,
        window: int = 100,
        *,
        thresholds: dict[str, float | None] | None = None,
        min_confidence: float = 0.2,
    ) -> None:
        self._window = window
        self._thresholds: dict[str, float | None] = dict(self.DEFAULT_THRESHOLDS)
        if thresholds:
            self._thresholds.update(thresholds)
        self._min_confidence = min_confidence

    def predict(
        self,
        runs: list[AgentRun],
        horizon_hours: float = 2.0,
    ) -> list[Prediction]:
        """Return predictions for all tracked metrics.

        Only predictions whose R² ≥ ``min_confidence`` are included.
        Results are sorted by confidence descending.
        """
        if len(runs) < 5:
            return []

        window_runs = runs[-self._window :]
        predictions: list[Prediction] = []

        for metric, values_fn in [
            ("retry_count", lambda r: float(r.retry_count)),
            ("total_tokens", lambda r: float(r.total_tokens)),
        ]:
            values = [values_fn(r) for r in window_runs]
            predictions.extend(
                self._predict_metric(metric, values, window_runs, horizon_hours)
            )

        predictions.extend(self._predict_path_entropy(window_runs, horizon_hours))
        predictions.extend(self._predict_tool_variance(window_runs, horizon_hours))

        result = [p for p in predictions if p.confidence >= self._min_confidence]
        result.sort(key=lambda p: p.confidence, reverse=True)
        return result

    # ── Per-metric regression ──────────────────────────────────────────────

    def _predict_metric(
        self,
        metric: str,
        values: list[float],
        runs: list[AgentRun],
        horizon_hours: float,
    ) -> list[Prediction]:
        if not values or not runs:
            return []

        t0 = runs[0].timestamp
        x = [(r.timestamp - t0).total_seconds() / 3600.0 for r in runs]
        slope, intercept = _ols(x, values)
        r2 = _r_squared(x, values, slope, intercept)

        current_x = x[-1]
        predicted_value = slope * (current_x + horizon_hours) + intercept
        current_value = values[-1]

        threshold = self._thresholds.get(metric)
        if threshold is None and metric == "total_tokens":
            threshold = 2.0 * (sum(values) / len(values))
        if threshold is None:
            return []

        will_exceed = predicted_value > threshold and slope > 0

        desc = (
            f"{metric} projected at {predicted_value:.1f} in {horizon_hours:.0f}h "
            f"(now: {current_value:.1f}, slope: {slope:+.2f}/h, R²={r2:.2f})"
        )
        if will_exceed:
            desc = (
                f"WARNING: {metric} likely to exceed threshold "
                f"({threshold:.1f}) — {desc}"
            )

        event: SentinelEvent | None = None
        if will_exceed and r2 >= self._min_confidence:
            agent_id = runs[-1].agent_id
            event = SentinelEvent(
                event_id=f"reliability-predict-{uuid.uuid4()}",
                event_type="reliability_prediction",
                timestamp=datetime.now(UTC),
                severity="WARN",
                producer_id=agent_id,
                body=desc,
                source_system=_SOURCE_SYSTEM,
                attributes={
                    "agent_id": agent_id,
                    "metric": metric,
                    "current_value": current_value,
                    "predicted_value": predicted_value,
                    "threshold": threshold,
                    "horizon_hours": horizon_hours,
                    "slope_per_hour": slope,
                    "r_squared": r2,
                },
            )

        return [
            Prediction(
                metric=metric,
                current_value=current_value,
                predicted_value=predicted_value,
                horizon_hours=horizon_hours,
                confidence=r2,
                will_exceed_threshold=will_exceed,
                threshold=threshold,
                slope_per_hour=slope,
                description=desc,
                sentinel_event=event,
            )
        ]

    def _predict_path_entropy(
        self,
        runs: list[AgentRun],
        horizon_hours: float,
    ) -> list[Prediction]:
        """Compute rolling path entropy and project it forward."""
        from .metrics import PathEntropyCalculator

        calc = PathEntropyCalculator()
        win = min(10, len(runs) // 2)
        if win < 3:
            return []

        values: list[float] = [
            calc.calculate(runs[i - win : i])
            for i in range(win, len(runs) + 1)
        ]
        # The representative run for each window is the last run in that window
        synthetic_runs = runs[win - 1 :]
        return self._predict_metric("path_entropy", values, synthetic_runs, horizon_hours)

    def _predict_tool_variance(
        self,
        runs: list[AgentRun],
        horizon_hours: float,
    ) -> list[Prediction]:
        """Compute rolling tool variance and project it forward."""
        from .metrics import ToolVarianceCalculator

        calc = ToolVarianceCalculator()
        win = min(10, len(runs) // 2)
        if win < 3:
            return []

        values: list[float] = [
            calc.calculate(runs[i - win : i])
            for i in range(win, len(runs) + 1)
        ]
        synthetic_runs = runs[win - 1 :]
        return self._predict_metric("tool_variance", values, synthetic_runs, horizon_hours)
