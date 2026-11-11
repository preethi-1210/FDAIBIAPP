"""Evaluate a structured prediction task on real banking data: will this loan instalment be missed?

Target: amount_paid = 0 for an instalment. Features are known by the instalment's due date: loan
terms, instalment number and how many earlier instalments were missed. The split is by time, training
on earlier due dates and testing on later ones.

Only scikit-learn baselines run by default. TabPFN runs only with --tabpfn and TABPFN_ENABLED=true,
and only from weights already on disk: this script never downloads weights or sends data anywhere.
Results stay on this machine and contain no loan identifiers.
"""

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

logging.disable(logging.WARNING)

from app.config import settings  # noqa: E402
from app.warehouse import _databricks_run, _databricks_http_path  # noqa: E402

SPLIT_DATE = "2025-10-01"
FEATURE_VERSION = "1"
NUMERIC = ["principal_amount", "interest_rate", "tenure_months", "emi_amount", "instalment_number", "prior_missed", "prior_missed_last_3"]
CATEGORICAL = ["loan_type"]
_DATASET_SQL = """
SELECT
  l.loan_type, l.principal_amount, l.interest_rate, l.tenure_months, l.emi_amount,
  CAST(r.due_date AS STRING) AS due_date,
  ROW_NUMBER() OVER (PARTITION BY r.loan_id ORDER BY r.due_date) AS instalment_number,
  COALESCE(SUM(CASE WHEN r.amount_paid = 0 THEN 1 ELSE 0 END) OVER (PARTITION BY r.loan_id ORDER BY r.due_date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS prior_missed,
  COALESCE(SUM(CASE WHEN r.amount_paid = 0 THEN 1 ELSE 0 END) OVER (PARTITION BY r.loan_id ORDER BY r.due_date ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING), 0) AS prior_missed_last_3,
  CASE WHEN r.amount_paid = 0 THEN 1 ELSE 0 END AS missed
FROM gold_loan_repayments r JOIN gold_loans l ON l.loan_id = r.loan_id
"""


def load_dataset() -> pd.DataFrame:
    namespace_rows = _databricks_run("banking", "gold", [_DATASET_SQL], None, 60000)[0]
    frame = pd.DataFrame(namespace_rows)
    for column in NUMERIC + ["missed"]:
        frame[column] = pd.to_numeric(frame[column])
    return frame


def scores(y_true: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    return {
        "roc_auc": round(float(roc_auc_score(y_true, probability)), 4),
        "average_precision": round(float(average_precision_score(y_true, probability)), 4),
        "brier": round(float(brier_score_loss(y_true, probability)), 4),
    }


def _preprocess() -> ColumnTransformer:
    return ColumnTransformer([("num", StandardScaler(), NUMERIC), ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL)])


def evaluate(frame: pd.DataFrame, include_tabpfn: bool) -> dict:
    train = frame[frame["due_date"] < SPLIT_DATE]
    test = frame[frame["due_date"] >= SPLIT_DATE]
    x_columns = NUMERIC + CATEGORICAL
    y_train, y_test = train["missed"].to_numpy(), test["missed"].to_numpy()
    models = {
        "majority/base rate": make_pipeline(_preprocess(), DummyClassifier(strategy="prior")),
        "logistic regression": make_pipeline(_preprocess(), LogisticRegression(max_iter=1000)),
        "gradient boosting": make_pipeline(_preprocess(), HistGradientBoostingClassifier(random_state=42)),
    }
    results = {}
    for name, model in models.items():
        started = time.perf_counter()
        model.fit(train[x_columns], y_train)
        probability = model.predict_proba(test[x_columns])[:, 1]
        results[name] = {**scores(y_test, probability), "seconds": round(time.perf_counter() - started, 2)}
    tabpfn = {"status": "not evaluated", "reason": "TabPFN is disabled (TABPFN_ENABLED=false): no approved weights or license."}
    if include_tabpfn and settings.tabpfn_enabled:
        from tabpfn import TabPFNClassifier

        started = time.perf_counter()
        # TabPFN is limited in training size on CPU; the most recent rows are the fairest sample.
        sample = train.sort_values("due_date").tail(1000)
        encoder = _preprocess()
        model = TabPFNClassifier(model_path=settings.tabpfn_model_path, device="cpu")
        model.fit(encoder.fit_transform(sample[x_columns]), sample["missed"].to_numpy())
        probability = model.predict_proba(encoder.transform(test[x_columns].head(5000)))[:, 1]
        tabpfn = {"status": "evaluated", **scores(y_test[:5000], probability), "seconds": round(time.perf_counter() - started, 2)}
    return {
        "target": "instalment recorded with amount_paid = 0",
        "feature_version": FEATURE_VERSION,
        "features": x_columns,
        "train": {"rows": len(train), "from": train["due_date"].min(), "to": train["due_date"].max(), "positive_rate": round(float(y_train.mean()), 4)},
        "test": {"rows": len(test), "from": test["due_date"].min(), "to": test["due_date"].max(), "positive_rate": round(float(y_test.mean()), 4)},
        "models": results,
        "tabpfn": tabpfn,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tabpfn", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args()
    _databricks_http_path()
    report = evaluate(load_dataset(), args.tabpfn)
    print(json.dumps(report, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
