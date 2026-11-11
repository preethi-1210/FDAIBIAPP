"""Time-series forecasting from an approved history query, judged against a baseline.

The series comes from a compiled registry query, not generated SQL. Before anything is fitted, the
history is checked for length, gaps, duplicates, invalid values and partial periods; a series that
fails is reported as insufficient rather than forecast. Methods are compared by rolling-origin
backtest, always training on the past and scoring on later periods.
"""

import math
import warnings
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

import numpy as np

MIN_PERIODS = 24
# A series that stopped longer ago than this says nothing about the months ahead.
MAX_STALENESS_DAYS = 365
BACKTEST_ORIGINS = 12
SEASON = 12
INTERVAL_LEVEL = 0.8
# A forecast this far above anything observed means the fit is unsound for this series.
MAX_REASONABLE_MULTIPLE = 3.0
WAPE_NOTE = (
    "WAPE divides total absolute error by total actual value. It is undefined when actuals sum to zero and "
    "unreliable when they are very small, so MAE, in the metric's own unit, is the deciding measure."
)


class InsufficientHistoryError(ValueError):
    """The history cannot support a forecast; the message says why and is shown to the user."""

    def __init__(self, message: str, assessment: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.assessment = assessment or {}


def _month_index(label: str) -> int:
    year, month = label.split("-")
    return int(year) * 12 + int(month) - 1


def _month_label(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


@dataclass(frozen=True)
class History:
    labels: tuple[str, ...]
    values: tuple[float, ...]
    excluded_partial: str | None
    last_observed_date: str | None


def assess_history(rows: list[dict[str, Any]], value_column: str, last_date: str | None, today: date | None = None) -> History:
    """Order and check a monthly series. Raises InsufficientHistoryError when it cannot be used."""
    observed: dict[str, float] = {}
    for row in rows:
        label, value = row.get("period"), row.get(value_column)
        if label is None:
            raise InsufficientHistoryError("The history has records with no date, so it cannot be ordered.")
        if str(label) in observed:
            raise InsufficientHistoryError(f"The history has duplicate observations for {label}.")
        if value is None or not math.isfinite(float(value)):
            raise InsufficientHistoryError(f"The history has no valid value for {label}.")
        observed[str(label)] = float(value)
    labels = sorted(observed)
    assessment = {"periods_available": len(labels), "first": labels[0] if labels else None, "last": labels[-1] if labels else None}
    if not labels:
        raise InsufficientHistoryError("No history exists for this metric and filter.", assessment)

    # The last month counts only if the data reaches its final day.
    excluded = None
    if last_date and labels[-1] == last_date[:7]:
        final = date.fromisoformat(last_date[:10])
        following = date(final.year + (final.month == 12), final.month % 12 + 1, 1)
        if (following - final).days > 1:
            excluded = labels.pop()
    assessment["excluded_partial_period"] = excluded
    expected = _month_index(labels[-1]) - _month_index(labels[0]) + 1 if labels else 0
    if len(labels) != expected:
        present = {_month_index(label) for label in labels}
        missing = [_month_label(index) for index in range(_month_index(labels[0]), _month_index(labels[-1]) + 1) if index not in present]
        raise InsufficientHistoryError(f"The history has {len(missing)} missing month(s), starting at {missing[0]}, so the series is not continuous.", assessment)
    if len(labels) < MIN_PERIODS:
        span = f" ({labels[0]} to {labels[-1]})" if labels else ""
        raise InsufficientHistoryError(
            f"Only {len(labels)} complete month(s) of history exist{span}; at least {MIN_PERIODS} are required so that a "
            "seasonal baseline can be compared by backtest.",
            assessment,
        )
    if today and last_date and (today - date.fromisoformat(last_date[:10])).days > MAX_STALENESS_DAYS:
        raise InsufficientHistoryError(
            f"The data stops at {last_date[:10]}, more than a year before today, so a forecast of the following "
            "months would describe a period already past.",
            assessment,
        )
    values = [observed[label] for label in labels]
    if any(value < 0 for value in values):
        raise InsufficientHistoryError("The history has negative values, which this forecast does not support.", assessment)
    if max(values) == min(values):
        raise InsufficientHistoryError("The history does not vary, so there is nothing to model.", assessment)
    return History(tuple(labels), tuple(values), excluded, last_date)


SIMPLE_BASELINES = ("naive", "seasonal naive (same month last year)")


def _naive(train: np.ndarray, horizon: int) -> np.ndarray:
    return np.repeat(train[-1], horizon)


def _seasonal_naive(train: np.ndarray, horizon: int) -> np.ndarray:
    return np.array([train[-SEASON + (step % SEASON)] for step in range(horizon)], dtype=float)


def _ets(train: np.ndarray, horizon: int, *, seasonal: bool) -> np.ndarray:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ExponentialSmoothing(
            train,
            trend="add",
            damped_trend=True,
            seasonal="add" if seasonal else None,
            seasonal_periods=SEASON if seasonal else None,
            initialization_method="estimated",
        ).fit()
    return np.asarray(fit.forecast(horizon), dtype=float)


def _ets_trend(train: np.ndarray, horizon: int) -> np.ndarray:
    return _ets(train, horizon, seasonal=False)


def _ets_seasonal(train: np.ndarray, horizon: int) -> np.ndarray:
    return _ets(train, horizon, seasonal=True)


def _methods(length: int, origins: int) -> dict[str, tuple[Callable[[np.ndarray, int], np.ndarray], int]]:
    """Method name to (function, shortest training length it may run on)."""
    available = {
        "naive": (_naive, 1),
        "seasonal naive (same month last year)": (_seasonal_naive, SEASON),
        "ETS (damped trend)": (_ets_trend, 12),
    }
    # Seasonal ETS needs two cycles in every backtest fold, not just in the full series.
    if length - origins >= 2 * SEASON + 1:
        available["ETS (damped trend, additive seasonality)"] = (_ets_seasonal, 2 * SEASON + 1)
    return available


def backtest(values: np.ndarray, horizon: int, origins: int = BACKTEST_ORIGINS) -> dict[str, dict[str, Any]]:
    """Rolling-origin evaluation: each fold fits on earlier months and predicts the next `horizon`."""
    origins = min(origins, max(1, (len(values) - SEASON - horizon + 1)))
    first_origin = len(values) - horizon - origins + 1
    results: dict[str, dict[str, Any]] = {}
    for name, (method, minimum) in _methods(len(values), origins + horizon - 1).items():
        errors: list[list[float]] = [[] for _ in range(horizon)]
        actual_total = 0.0
        failed = None
        for origin in range(first_origin, first_origin + origins):
            if origin < minimum:
                continue
            try:
                predicted = method(values[:origin], horizon)
            except Exception as exc:  # A method that cannot fit this series is out of the comparison.
                failed = type(exc).__name__
                break
            if not np.all(np.isfinite(predicted)):
                failed = "non-finite forecast"
                break
            for step in range(horizon):
                errors[step].append(float(predicted[step] - values[origin + step]))
                actual_total += abs(float(values[origin + step]))
        if failed or not errors[0]:
            continue
        flat = np.abs(np.array([error for step in errors for error in step]))
        results[name] = {
            "mae": float(flat.mean()),
            "wape_pct": float(flat.sum() / actual_total * 100) if actual_total > 0 else None,
            "folds": len(errors[0]),
            "errors_by_step": errors,
        }
    return results


def _interval(point: float, errors: list[float]) -> tuple[float, float]:
    """Empirical range from this method's own backtest errors at this horizon step."""
    tail = (1 - INTERVAL_LEVEL) / 2
    low, high = np.quantile(np.array(errors), [tail, 1 - tail])
    # Errors are prediction minus actual, so a positive error means the actual came in lower.
    return float(max(point - high, 0.0)), float(max(point - low, 0.0))


def forecast_series(history: History, horizon: int) -> dict[str, Any]:
    """Select by backtest, forecast, and validate. Raises InsufficientHistoryError if unsound."""
    values = np.array(history.values, dtype=float)
    results = backtest(values, horizon)
    simple = [name for name in SIMPLE_BASELINES if name in results]
    if not simple:
        raise InsufficientHistoryError("A simple baseline could not be evaluated on this history.")
    # A model has skill only if it beats the better of the simple methods, not merely the worse one.
    baseline_name = min(simple, key=lambda name: results[name]["mae"])
    baseline = results[baseline_name]
    chosen_name = min(results, key=lambda name: results[name]["mae"])
    chosen = results[chosen_name]
    method = _methods(len(values), 0)[chosen_name][0]
    points = [float(value) for value in method(values, horizon)]
    if not all(math.isfinite(point) for point in points) or max(points) > MAX_REASONABLE_MULTIPLE * values.max():
        raise InsufficientHistoryError(f"{chosen_name} produced an implausible forecast, so none is reported.")
    points = [max(point, 0.0) for point in points]
    intervals = [_interval(points[step], chosen["errors_by_step"][step]) for step in range(horizon)]
    next_index = _month_index(history.labels[-1]) + 1
    labels = [_month_label(next_index + step) for step in range(horizon)]
    skill = 1 - chosen["mae"] / baseline["mae"] if baseline["mae"] > 0 else None
    comparison = [
        {"method": name, "mae": round(item["mae"], 2), "wape_pct": round(item["wape_pct"], 2) if item["wape_pct"] is not None else None, "folds": item["folds"]}
        for name, item in sorted(results.items(), key=lambda pair: pair[1]["mae"])
    ]
    limitations = [
        f"Ranges are approximate {INTERVAL_LEVEL:.0%} ranges from {chosen['folds']} backtest folds, not a statistical guarantee, "
        "and the method was chosen on those same folds, so its error is somewhat optimistic.",
        WAPE_NOTE,
    ]
    if len(values) < 3 * SEASON:
        limitations.append(f"Only {len(values)} months of history: seasonality rests on few cycles.")
    return {
        "model": chosen_name,
        "baseline": baseline_name,
        "labels": labels,
        "values": [round(point, 2) for point in points],
        "intervals": [[round(low, 2), round(high, 2)] for low, high in intervals],
        "interval_level": INTERVAL_LEVEL,
        "backtest": comparison,
        "skill_vs_baseline_pct": round(skill * 100, 1) if skill is not None else None,
        "history": {
            "first": history.labels[0],
            "last": history.labels[-1],
            "periods": len(history.labels),
            "excluded_partial_period": history.excluded_partial,
            "last_observed_date": history.last_observed_date,
        },
        "limitations": limitations,
    }
