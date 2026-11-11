"""Statistical baseline for next-period projections, chosen and sized by backtesting."""

from typing import Any, Callable

import numpy as np

from ..schemas import PredictionPayload, UncertaintyPayload


def _naive(history: np.ndarray) -> float:
    return float(history[-1])


def _seasonal_naive(history: np.ndarray) -> float:
    return float(history[-12])


def _linear_trend(history: np.ndarray) -> float:
    window = history[-12:]
    slope, intercept = np.polyfit(np.arange(len(window)), window, 1)
    return float(slope * len(window) + intercept)


def baseline_forecast(prepared: dict[str, Any]) -> tuple[PredictionPayload, UncertaintyPayload, dict[str, Any]]:
    """Pick the simple method with the lowest one-step backtest error and use its errors as the range."""
    values = np.asarray(prepared["values"], dtype=float)
    methods: dict[str, Callable[[np.ndarray], float]] = {"last value": _naive, "linear trend": _linear_trend}
    if prepared["frequency"] == "monthly" and len(values) >= 24:
        methods["same month last year"] = _seasonal_naive
    holdout = max(3, min(12, len(values) // 3))
    minimum_history = 12 if "same month last year" in methods else 2
    start = max(len(values) - holdout, minimum_history + 1)

    errors = {
        name: np.array([abs(method(values[:index]) - values[index]) for index in range(start, len(values))])
        for name, method in methods.items()
    }
    best = min(errors, key=lambda name: float(errors[name].mean()))
    point = methods[best](values)
    margin = float(np.quantile(errors[best], 0.8))
    payload = PredictionPayload(type="forecast", labels=[prepared["next_period"]], values=[point])
    uncertainty = UncertaintyPayload(confidence_range=[point - margin, point + margin])
    return payload, uncertainty, {
        "model": f"statistical baseline ({best})",
        "backtest_periods": len(errors[best]),
        "backtest_mean_absolute_error": float(errors[best].mean()),
    }
