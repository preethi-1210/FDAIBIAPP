import unittest
from unittest.mock import patch

from app.services.sql_generator import generate_sql


class AdminSensitiveSQLGenerationTests(unittest.TestCase):
    def test_admin_request_selects_only_existing_approved_policy_fields(self) -> None:
        schema = {
            "tables": {
                "claims": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "domain", "type": "VARCHAR"},
                    {"name": "policy_number", "type": "VARCHAR"},
                    {"name": "customer_id", "type": "INTEGER"},
                    {"name": "amount", "type": "FLOAT"},
                ],
                "customers": [
                    {"name": "id", "type": "INTEGER"},
                    {"name": "domain", "type": "VARCHAR"},
                    {"name": "name", "type": "VARCHAR"},
                ],
            }
        }
        with patch("app.services.sql_generator.complete_json") as llm:
            sql = generate_sql("Show policy numbers", schema, False, "insurance", role="admin")

        self.assertIn('FROM "claims"', sql)
        self.assertIn('"policy_number"', sql)
        self.assertIn('"domain" = \'insurance\'', sql)
        self.assertIn("LIMIT 200", sql)
        self.assertNotIn("SELECT *", sql)
        llm.assert_not_called()

    def test_admin_card_number_request_selects_an_approved_pan_field(self) -> None:
        schema = {
            "tables": {
                "payment_cards": [
                    {"name": "domain", "type": "VARCHAR"},
                    {"name": "card_pan", "type": "VARCHAR"},
                    {"name": "cardholder_name", "type": "VARCHAR"},
                ],
            }
        }

        sql = generate_sql(
            "Show card number",
            schema,
            False,
            "banking",
            role="admin",
        )

        self.assertIn('"card_pan"', sql)
        self.assertIn('"domain" = \'banking\'', sql)
        self.assertNotIn("SELECT *", sql)

    def test_admin_sensitive_request_fails_clearly_when_source_field_is_missing(self) -> None:
        schema = {"tables": {"customers": [
            {"name": "id", "type": "INTEGER"},
            {"name": "domain", "type": "VARCHAR"},
            {"name": "name", "type": "VARCHAR"},
        ]}}
        with self.assertRaisesRegex(ValueError, "No authorized.*column is available"):
            generate_sql("Show account numbers", schema, False, "banking", role="admin")

    def test_analyst_cannot_use_admin_sensitive_sql_generation(self) -> None:
        schema = {"tables": {"customers": [
            {"name": "id", "type": "INTEGER"},
            {"name": "domain", "type": "VARCHAR"},
            {"name": "account_number", "type": "VARCHAR"},
        ]}}
        with patch("app.services.sql_generator.complete_json", return_value=None):
            sql = generate_sql("Show account numbers", schema, False, "banking", role="analyst")
        self.assertNotIn("account_number", sql)


if __name__ == "__main__":
    unittest.main()
