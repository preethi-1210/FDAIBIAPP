import unittest
from unittest.mock import patch

from app.services.synthesis import synthesize


class SynthesisValidationTests(unittest.TestCase):
    def test_synthesis_rejects_numbers_not_present_in_query_results(self) -> None:
        with patch(
            "app.services.synthesis.complete_text",
            return_value="Savings are 10.00 and Salary is 22.00.",
        ):
            answer = synthesize(
                "Show balances by account type",
                [{"account_type": "Savings", "total_balance": 10.0}],
                "SELECT account_type, total_balance FROM accounts",
                None,
                None,
                domain="banking",
            )

        self.assertEqual(
            answer,
            "Returned 1 rows from the warehouse for: Show balances by account type",
        )

    def test_synthesis_keeps_numbers_grounded_in_query_results(self) -> None:
        with patch(
            "app.services.synthesis.complete_text",
            return_value="Savings has a total balance of 10.00.",
        ):
            answer = synthesize(
                "Show balances by account type",
                [{"account_type": "Savings", "total_balance": 10.0}],
                "SELECT account_type, total_balance FROM accounts",
                None,
                None,
                domain="banking",
            )

        self.assertEqual(answer, "Savings has a total balance of 10.00.")


if __name__ == "__main__":
    unittest.main()
