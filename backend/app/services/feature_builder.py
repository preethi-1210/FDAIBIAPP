from typing import Any

import pandas as pd

from ..llm import complete_json


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
