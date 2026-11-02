from importlib.metadata import PackageNotFoundError, version
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold, StratifiedKFold, cross_val_predict

from ..schemas import PredictionPayload, UncertaintyPayload


class ModelUnavailableError(RuntimeError):
    pass


def _as_model_error(exc: Exception) -> Exception:
    text = f"{type(exc).__name__}: {exc}"
    if any(marker in text for marker in ("LocalEntryNotFound", "huggingface", ".ckpt", "CERTIFICATE_VERIFY_FAILED")):
        return ModelUnavailableError(
            "The self-hosted TabPFN model weights are not available on this server (they are not in the "
            "local model cache and could not be downloaded). No prediction was produced."
        )
    return exc


class TabPFNService:
    """Run cross-validated predictions with the self-hosted TabPFN estimators.

    Never calls a hosted prediction API or Databricks Genie.
    """

    @staticmethod
    def package_status() -> dict[str, Any]:
        try:
            installed_version = version("tabpfn")
        except PackageNotFoundError:
            return {"installed": False, "version": None, "runtime": "self-hosted"}
        return {"installed": True, "version": installed_version, "runtime": "self-hosted"}

    def diagnostic_test(self) -> dict[str, Any]:
        scores = np.arange(32, dtype=float)
        labels = (scores >= 16).astype(int)
        prepared = {
            "task": "classification",
            "X": pd.DataFrame(
                {
                    "risk_score": scores,
                    "account_age_months": 48 - scores,
                    "recent_activity": (scores * 3) % 11,
                }
            ),
            "y": pd.Series(labels),
            "classes": ["Stable", "Review"],
        }
        prediction, uncertainty = self.predict(prepared)
        accuracy = sum(
            predicted == ("Review" if actual else "Stable")
            for predicted, actual in zip(prediction.labels, labels, strict=True)
        ) / len(labels)
        return {
            "status": "passed",
            "model": "TabPFNClassifier",
            "data": "Generated synthetic data (not customer records)",
            "rows_scored": len(prediction.labels),
            "accuracy": accuracy,
            "confidence_range": uncertainty.confidence_range,
            "sample": [
                {
                    "predicted_label": prediction.labels[index],
                    "confidence": prediction.probabilities[index],
                }
                for index in range(min(5, len(prediction.labels)))
            ],
        }

    def forecast(self, prepared: dict[str, Any]) -> tuple[PredictionPayload, UncertaintyPayload]:
        """Project the next period from history: fit on past periods, predict the query row."""
        from tabpfn import TabPFNRegressor

        try:
            model = TabPFNRegressor()
            model.fit(prepared["X"], prepared["y"])
            quantiles = model.predict(prepared["X_query"], output_type="quantiles", quantiles=[0.1, 0.5, 0.9])
        except Exception as exc:
            raise _as_model_error(exc) from exc
        low, median, high = (float(np.asarray(values).ravel()[0]) for values in quantiles)
        payload = PredictionPayload(type="forecast", labels=[prepared["next_period"]], values=[median])
        return payload, UncertaintyPayload(confidence_range=[low, high])

    def predict(self, prepared: dict[str, Any]) -> tuple[PredictionPayload, UncertaintyPayload]:
        try:
            return self._predict(prepared)
        except ValueError:
            raise
        except Exception as exc:
            raise _as_model_error(exc) from exc

    def _predict(self, prepared: dict[str, Any]) -> tuple[PredictionPayload, UncertaintyPayload]:
        if len(prepared["X"]) < 8:
            raise ValueError("Not enough labeled rows for a stable TabPFN prediction (minimum 8)")
        from tabpfn import TabPFNClassifier, TabPFNRegressor

        X = prepared["X"]
        y = prepared["y"]
        if prepared["task"] == "classification":
            classes, counts = np.unique(y, return_counts=True)
            if len(classes) < 2:
                raise ValueError("TabPFN classification requires at least two target classes")
            folds = min(5, int(counts.min()))
            if folds < 2:
                raise ValueError("TabPFN classification requires at least two examples per target class")
            cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=42)
            model = TabPFNClassifier()
            proba = cross_val_predict(model, X, y, cv=cv, method="predict_proba")
            class_labels = prepared.get("classes") or classes.tolist()
            labels = np.asarray(class_labels, dtype=object)[np.argmax(proba, axis=1)].tolist()
            conf = proba.max(axis=1)
            reported_probabilities = proba[:, 1] if proba.shape[1] == 2 else conf
            payload = PredictionPayload(
                type="classification",
                labels=labels,
                probabilities=reported_probabilities.tolist(),
                probability_label=class_labels[1] if proba.shape[1] == 2 else None,
            )
            uncertainty = UncertaintyPayload(
                confidence_range=[float(np.quantile(conf, 0.25)), float(np.quantile(conf, 0.75))]
            )
            return payload, uncertainty

        model = TabPFNRegressor()
        cv = KFold(n_splits=min(5, len(X)), shuffle=True, random_state=42)
        preds = cross_val_predict(model, X, y, cv=cv)
        payload = PredictionPayload(type="regression", values=preds.tolist())
        lo, hi = float(np.quantile(preds, 0.25)), float(np.quantile(preds, 0.75))
        return payload, UncertaintyPayload(confidence_range=[lo, hi])
