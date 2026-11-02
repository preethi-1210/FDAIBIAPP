import unittest

from app.services.guardrail import validate_sql
from app.services.orchestrator import (
    RISK_RATIO_DEFINITIONS,
    _build_risk_ratio_rows,
    _risk_ratio_query,
)


class DashboardRiskRatioTests(unittest.TestCase):
    def test_each_domain_has_explicit_ratio_definition(self) -> None:
        self.assertEqual(
            set(RISK_RATIO_DEFINITIONS),
            {"banking", "finance", "healthcare", "insurance", "retail"},
        )
        self.assertIn("demo proxy", RISK_RATIO_DEFINITIONS["finance"]["description"])
        self.assertIn("demo proxy", RISK_RATIO_DEFINITIONS["retail"]["description"])
        self.assertIn("tenant", RISK_RATIO_DEFINITIONS["healthcare"]["description"])

    def test_each_domain_query_is_read_only_scoped_and_sql_guardrail_compatible(self) -> None:
        for domain in RISK_RATIO_DEFINITIONS:
            with self.subTest(domain=domain):
                query = _risk_ratio_query(domain, f"'{domain}'")
                self.assertTrue(query.lstrip().lower().startswith("select"))
                self.assertIn(f"'{domain}'", query)
                self.assertIn("numerator", query)
                self.assertIn("denominator", query)
                self.assertIn("LIMIT", validate_sql(query))

    def test_risk_ratio_rows_use_zero_for_empty_denominators(self) -> None:
        rows = _build_risk_ratio_rows(
            [
                {"region": "North", "numerator": 25, "denominator": 100},
                {"region": "South", "numerator": 5, "denominator": 0},
            ]
        )

        self.assertEqual(rows[0]["loss_ratio"], 0.25)
        self.assertEqual(rows[1]["loss_ratio"], 0.0)
        self.assertEqual(rows[1]["numerator"], 5.0)


if __name__ == "__main__":
    unittest.main()
