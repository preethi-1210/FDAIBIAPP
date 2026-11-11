import re
from typing import Any

import pandas as pd

from ..llm import complete_json, record_step

# A projection needs enough history for the lag features plus a meaningful context window.
MIN_FORECAST_PERIODS = 8
_FREQUENCIES = (  # (max median gap in days, name, offset)
    (1.5, "daily", pd.DateOffset(days=1)),
    (8, "weekly", pd.DateOffset(weeks=1)),
    (40, "monthly", pd.DateOffset(months=1)),
    (100, "quarterly", pd.DateOffset(months=3)),
    (400, "yearly", pd.DateOffset(years=1)),
)
_PERIOD_NAME_HINT = re.compile(r"(date|month|week|day|period|quarter|year|time|label)", re.IGNORECASE)


def _period_column(df: pd.DataFrame) -> str | None:
    candidates = sorted(df.columns, key=lambda column: 0 if _PERIOD_NAME_HINT.search(str(column)) else 1)
    for column in candidates:
        if pd.api.types.is_numeric_dtype(df[column]) and not _PERIOD_NAME_HINT.search(str(column)):
            continue
        parsed = pd.to_datetime(df[column].astype(str), errors="coerce", format="mixed")
        if len(parsed) and parsed.notna().mean() >= 0.9:
            return column
    return None


def _forecast_target(question: str, df: pd.DataFrame, period_column: str) -> str | None:
    numeric = [
        column for column in df.columns
        if column != period_column and pd.api.types.is_numeric_dtype(df[column])
    ]
    if not numeric:
        return None
    words = set(re.findall(r"[a-z]+", question.lower()))
    for column in numeric:
        if words & set(re.split(r"[_\W]+", str(column).lower())):
            return column
    preferred = ("volume", "amount", "revenue", "balance", "premium", "sales", "total", "value")
    return next((c for c in numeric if any(p in str(c).lower() for p in preferred)), numeric[0])


def build_forecast(question: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prepare a next-period projection (scope doc: date parts + lag columns).

    Returns None when the result is not a time series, so callers can fall back to
    scoring individual records. Raises ValueError when it is a time series but has too
    little history for a defensible projection.
    """
    if not rows:
        raise ValueError("No historical rows were returned, so there is nothing to project from.")
    df = pd.DataFrame(rows)
    period_column = _period_column(df)
    if period_column is None:
        return None
    target = _forecast_target(question, df, period_column)
    if target is None:
        return None

    series = pd.DataFrame({
        "period": pd.to_datetime(df[period_column].astype(str), errors="coerce", format="mixed"),
        "value": pd.to_numeric(df[target], errors="coerce"),
    }).dropna()
    series = series.groupby("period", as_index=False)["value"].sum().sort_values("period").reset_index(drop=True)
    periods = len(series)
    if periods < MIN_FORECAST_PERIODS + 2:
        raise ValueError(
            f"Only {periods} period(s) of history are available for {target}; at least "
            f"{MIN_FORECAST_PERIODS + 2} are needed for a projection, so no prediction was made."
        )
    gap_days = series["period"].diff().dt.days.median()
    frequency, offset = next(
        ((name, step) for limit, name, step in _FREQUENCIES if gap_days <= limit),
        ("yearly", pd.DateOffset(years=1)),
    )

    values = series["value"].tolist()

    def features_at(index: int, period: pd.Timestamp) -> dict[str, float]:
        return {
            "t": float(index),
            "month": float(period.month),
            "quarter": float(period.quarter),
            "lag_1": float(values[index - 1]),
            "lag_2": float(values[index - 2]),
            "rolling_mean_3": float(sum(values[max(0, index - 3):index]) / len(values[max(0, index - 3):index])),
        }

    train = pd.DataFrame([features_at(i, series["period"][i]) for i in range(2, periods)])
    next_period = series["period"].iloc[-1] + offset
    query = pd.DataFrame([features_at(periods, next_period)])
    label_format = {"monthly": "%Y-%m", "quarterly": "%Y-%m", "yearly": "%Y"}.get(frequency, "%Y-%m-%d")
    record_step("feature_builder", "time-series features (date parts + lags)")
    return {
        "task": "forecast",
        "target": target,
        "period_column": period_column,
        "frequency": frequency,
        "X": train,
        "y": pd.Series(values[2:], dtype=float),
        "X_query": query,
        "next_period": next_period.strftime(label_format),
        "history_start": series["period"].iloc[0].strftime(label_format),
        "history_end": series["period"].iloc[-1].strftime(label_format),
        "last_value": float(values[-1]),
        "values": values,
        "periods": periods,
    }


def build_features(question: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("No rows available to build prediction features")

    df = pd.DataFrame(rows)
    llm_spec = complete_json(
        system=(
            "Propose a TabPFN feature spec as JSON: "
            '{"task":"classification|regression","target":"column","features":["col",...]} '
            "Target and features must exist in the provided columns."
        ),
        user=f"Question: {question}\nColumns: {list(df.columns)}",
        stage="feature_builder",
    )

    if llm_spec and llm_spec.get("target") in df.columns:
        target = llm_spec["target"]
        features = [c for c in llm_spec.get("features", []) if c in df.columns and c != target]
        task = llm_spec.get("task", "classification")
    else:
        target, features, task = _heuristic_spec(question, df)

    if target not in df.columns:
        raise ValueError(f"Target column '{target}' is not in the SQL result")
    if task not in {"classification", "regression"}:
        raise ValueError(f"Unsupported prediction task '{task}'")
    if not features:
        raise ValueError("No valid feature columns for TabPFN")

    work = df.copy()
    if "order_date" in work.columns:
        work["order_month"] = pd.to_datetime(work["order_date"], errors="coerce").dt.month
        if "order_month" not in features:
            features.append("order_month")

    feature_frame = work[features].copy()
    for col in feature_frame.columns:
        if feature_frame[col].dtype == object:
            feature_frame[col] = feature_frame[col].astype("category").cat.codes
        feature_frame[col] = pd.to_numeric(feature_frame[col], errors="coerce").fillna(0)

    target_values = work[target]
    if task == "classification":
        categories = target_values.astype("category").cat.categories
        y = target_values.astype("category").cat.codes
    else:
        y = pd.to_numeric(target_values, errors="coerce")

    labeled = target_values.notna() & y.ge(0) if task == "classification" else y.notna()
    return {
        "task": task,
        "target": target,
        "features": features,
        "X": feature_frame.loc[labeled],
        "y": y.loc[labeled],
        "classes": categories.tolist() if task == "classification" else [],
        "row_meta": work.loc[labeled].reset_index(drop=True),
    }


def _heuristic_spec(question: str, df: pd.DataFrame) -> tuple[str, list[str], str]:
    q = question.lower()
    if "churn" in q and "churned" in df.columns:
        target = "churned"
        task = "classification"
    elif any(c in df.columns for c in ("revenue", "total_amount")):
        target = "revenue" if "revenue" in df.columns else "total_amount"
        task = "regression"
    else:
        numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        target = numeric[-1] if numeric else df.columns[-1]
        task = "regression"

    skip = {target, "id", "name", "customer_id"}
    features = [c for c in df.columns if c not in skip][:8]
    return target, features, task
