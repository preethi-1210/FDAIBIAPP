import unittest
from unittest.mock import patch

from app.services.conversation_context import format_conversation_context
from app.services.planner import plan_query
from app.services.sql_generator import generate_sql
from app.services.synthesis import synthesize


class ConversationContextPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = [
            {"role": "user", "content": "Compare revenue by region"},
            {"role": "assistant", "content": "West has the highest revenue."},
        ]

    def test_context_formatter_limits_history_and_ignores_unknown_roles(self) -> None:
        history = [
            {"role": "user", "content": f"turn-{index}"}
            for index in range(13)
        ] + [{"role": "system", "content": "ignore this"}]

        formatted = format_conversation_context(history)

        self.assertNotIn("turn-0", formatted)
        self.assertIn("turn-12", formatted)
        self.assertNotIn("ignore this", formatted)

    def test_planner_uses_fast_deterministic_classification(self) -> None:
        plan = plan_query("predict which customers will churn")

        self.assertTrue(plan["is_prediction"])
        self.assertEqual(plan["intent"], "prediction")

    @patch(
        "app.services.sql_generator.complete_json",
        return_value={"sql": "SELECT COUNT(*) FROM orders WHERE domain = 'retail'"},
    )
    def test_sql_generation_receives_recent_turns(self, complete_json) -> None:
        generate_sql(
            "and the prior month?",
            {"tables": {"orders": []}},
            False,
            "retail",
            conversation_context=self.context,
        )

        prompt = complete_json.call_args.kwargs["user"]
        self.assertIn("Compare revenue by region", prompt)
        self.assertIn("and the prior month?", prompt)

    @patch("app.services.synthesis.complete_text", return_value="Revenue declined.")
    def test_synthesis_receives_recent_turns(self, complete_text) -> None:
        answer = synthesize(
            "and the prior month?",
            [{"revenue": 10}],
            "SELECT 10",
            None,
            None,
            conversation_context=self.context,
        )

        self.assertEqual(answer, "Revenue declined.")
        prompt = complete_text.call_args.kwargs["user"]
        self.assertIn("Compare revenue by region", prompt)
        self.assertIn("and the prior month?", prompt)


if __name__ == "__main__":
    unittest.main()
