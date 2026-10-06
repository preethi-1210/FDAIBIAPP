import sys
import types
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.services.feature_builder import build_features
from app.services.tabpfn_service import TabPFNService


class TabPFNServiceTests(unittest.TestCase):
    def test_diagnostic_uses_tabpfn_on_synthetic_data_and_reports_accuracy(self) -> None:
        tabpfn = types.ModuleType("tabpfn")
        tabpfn.TabPFNClassifier = type("TabPFNClassifier", (), {})
        tabpfn.TabPFNRegressor = type("TabPFNRegressor", (), {})
        probabilities = np.vstack([np.tile([0.9, 0.1], (16, 1)), np.tile([0.1, 0.9], (16, 1))])
        with patch.dict(sys.modules, {"tabpfn": tabpfn}), patch(
            "app.services.tabpfn_service.cross_val_predict", return_value=probabilities
        ):
            result = TabPFNService().diagnostic_test()

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["model"], "TabPFNClassifier")
        self.assertEqual(result["rows_scored"], 32)
        self.assertEqual(result["accuracy"], 1.0)
        self.assertIn("synthetic", result["data"])

    def test_classification_predictions_keep_original_target_labels(self) -> None:
        tabpfn = types.ModuleType("tabpfn")
        tabpfn.TabPFNClassifier = type("TabPFNClassifier", (), {})
        tabpfn.TabPFNRegressor = type("TabPFNRegressor", (), {})
        probabilities = np.tile([[0.8, 0.2], [0.1, 0.9]], (4, 1))
        prepared = {
            "task": "classification",
            "X": pd.DataFrame({"feature": range(8)}),
            "y": pd.Series([0, 1] * 4),
            "classes": ["no", "yes"],
        }
        with patch.dict(sys.modules, {"tabpfn": tabpfn}), patch(
            "app.services.tabpfn_service.cross_val_predict", return_value=probabilities
        ):
            prediction, uncertainty = TabPFNService().predict(prepared)

        self.assertEqual(prediction.labels, ["no", "yes"] * 4)
        self.assertEqual(prediction.probabilities, [0.2, 0.9] * 4)
        self.assertEqual(len(uncertainty.confidence_range), 2)

    def test_feature_builder_excludes_unlabeled_rows_and_target(self) -> None:
        rows = [
            {"churned": label, "segment": "retail", "amount": float(index)}
            for index, label in enumerate([0, 1] * 4)
        ]
        rows[-1]["churned"] = None
        spec = {"task": "classification", "target": "churned", "features": ["segment", "amount", "churned"]}
        with patch("app.services.feature_builder.complete_json", return_value=spec):
            prepared = build_features("predict churn", rows)

        self.assertEqual(len(prepared["X"]), 7)
        self.assertEqual(prepared["features"], ["segment", "amount"])
        self.assertEqual(prepared["classes"], [0, 1])
        self.assertTrue(prepared["y"].isin([0, 1]).all())

    def test_small_datasets_are_rejected_before_model_loading(self) -> None:
        prepared = {
            "task": "regression",
            "X": pd.DataFrame({"feature": range(7)}),
            "y": pd.Series(range(7)),
        }
        with self.assertRaisesRegex(ValueError, "minimum 8"):
            TabPFNService().predict(prepared)


if __name__ == "__main__":
    unittest.main()
