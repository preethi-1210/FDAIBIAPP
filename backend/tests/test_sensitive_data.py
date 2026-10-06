import unittest

from fastapi import HTTPException

from app.sensitive_data import (
    has_cvv_columns,
    has_restricted_columns,
    require_sensitive_request_access,
    require_sql_sensitive_access,
    requested_sensitive_type,
    sanitize_sensitive_rows,
)


class SensitiveDataPolicyTests(unittest.TestCase):
    def test_cvv_requests_are_never_allowed_even_for_admin(self) -> None:
        question = "Show the user's account CVV"
        self.assertEqual(requested_sensitive_type(question), "never_disclose")
        with self.assertRaises(HTTPException) as raised:
            require_sensitive_request_access(question, "admin")
        self.assertEqual(raised.exception.status_code, 403)

    def test_account_and_policy_identifiers_are_admin_only(self) -> None:
        for question in ("List account numbers", "Show policy ID", "Show card number"):
            with self.subTest(question=question):
                self.assertEqual(requested_sensitive_type(question), "admin_only")
                with self.assertRaises(HTTPException):
                    require_sensitive_request_access(question, "analyst")
                require_sensitive_request_access(question, "admin")

    def test_domain_sensitive_categories_are_recognized(self) -> None:
        prompts = (
            "Show customer account numbers and PAN details",
            "Retrieve Aadhaar and KYC document information",
            "Display complete credit card details including CVV",
            "Show policy holder names and policy numbers",
            "List claimants with their medical records and diagnoses",
            "Retrieve nominee details and customer identity documents",
            "Show employee payroll, salary, and bonus records",
            "Retrieve employee tax information and tax IDs",
            "List executive compensation and employee bank details",
            "Show customer profiles with phone numbers and addresses",
            "List an individual customer's purchase and loyalty history",
            "Display saved customer payment information and card numbers",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertIsNotNone(requested_sensitive_type(prompt))

    def test_unrecognized_sensitive_columns_are_denied_to_analysts(self) -> None:
        for column in (
            "aadhaar_number",
            "pan_number",
            "employee_salary",
            "diagnosis",
            "phone_number",
            "email_address",
            "purchase_history",
        ):
            with self.subTest(column=column):
                with self.assertRaises(HTTPException):
                    require_sql_sensitive_access(
                        f"SELECT {column} FROM records LIMIT 10",
                        "analyst",
                    )

    def test_generated_sql_cannot_bypass_sensitive_access(self) -> None:
        with self.assertRaises(HTTPException):
            require_sql_sensitive_access("SELECT account_number FROM customers", "analyst")
        with self.assertRaises(HTTPException):
            require_sql_sensitive_access("SELECT * FROM policies", "analyst")
        with self.assertRaises(HTTPException):
            require_sql_sensitive_access("SELECT c.* FROM customers c", "admin")
        with self.assertRaises(HTTPException):
            require_sql_sensitive_access("SELECT cvv FROM payment_cards", "admin")
        with self.assertRaises(HTTPException):
            require_sql_sensitive_access("SELECT payment_cvv_token FROM payment_cards", "admin")
        require_sql_sensitive_access("SELECT account_number FROM customers", "admin")
        require_sql_sensitive_access("SELECT COUNT(*) FROM customers", "analyst")

    def test_result_rows_redact_restricted_fields_and_drop_cvv(self) -> None:
        rows = [{
            "accountNumberLast4": "6789",
            "policyId": "POL-123",
            "paymentCvvToken": "encrypted-token",
            "revenue": 42,
        }]
        self.assertTrue(has_restricted_columns(rows))
        self.assertTrue(has_cvv_columns(rows))
        self.assertEqual(
            sanitize_sensitive_rows(rows, "analyst"),
            [{"accountNumberLast4": "[REDACTED]", "policyId": "[REDACTED]", "revenue": 42}],
        )
        self.assertEqual(
            sanitize_sensitive_rows(rows, "admin"),
            [{"accountNumberLast4": "6789", "policyId": "POL-123", "revenue": 42}],
        )

    def test_card_numbers_are_masked_even_for_admins(self) -> None:
        rows = [{"card_number": "4111-1111-1111-1234", "card_pan": 5555444433332222}]

        self.assertEqual(
            sanitize_sensitive_rows(rows, "admin"),
            [{
                "card_number": "**** **** **** 1234",
                "card_pan": "**** **** **** 2222",
            }],
        )


if __name__ == "__main__":
    unittest.main()
