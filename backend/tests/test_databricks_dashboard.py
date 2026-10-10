import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.governance import build_lineage, extract_tables
from app.services.guardrail import validate_sql
from app.services.orchestrator import _databricks_dashboard_queries, _validate_query_sources
from app.services.sql_generator import generate_sql


class DatabricksDashboardQueryTests(unittest.TestCase):
    def test_dashboard_queries_are_guarded_aggregate_selects_for_real_domain_schemas(self) -> None:
        for domain in ("banking", "insurance", "retail"):
            with self.subTest(domain=domain):
                queries = _databricks_dashboard_queries(domain)
                self.assertEqual(len(queries), 9)
                for query in queries:
                    checked = validate_sql(query)
                    self.assertTrue(checked.lstrip().lower().startswith("select"))
                    self.assertIn("LIMIT", checked.upper())

    def test_dashboard_queries_do_not_select_sensitive_test_data_columns(self) -> None:
        queries = _databricks_dashboard_queries("insurance") + _databricks_dashboard_queries("retail")
        all_sql = "\n".join(queries).lower()
        for column in ("cvv", "account_number", "account_password", "email_password", "phone_number"):
            self.assertNotIn(column, all_sql)
        self.assertNotIn("select *", all_sql)
        for projection in ("select cvv", "select account_number", "select account_id", "select policy_id"):
            self.assertNotIn(projection, all_sql)

    def test_finance_requires_an_explicit_catalog_mapping(self) -> None:
        from app.services.guardrail import GuardrailError

        with self.assertRaises(GuardrailError):
            _databricks_dashboard_queries("finance")

    def test_chat_query_sources_are_limited_to_retrieved_authorized_schema(self) -> None:
        schema = {"tables": {"customers": [], "transactions": []}}
        with (
            patch("app.services.orchestrator.settings.warehouse", "databricks"),
            patch("app.services.orchestrator.settings.databricks_domain_schema", "silver"),
            patch("app.services.orchestrator.databricks_domain_catalog", return_value="banking"),
        ):
            _validate_query_sources(
                "SELECT c.credit_score FROM banking.silver.customers AS c LIMIT 10",
                schema,
                "banking",
            )
            with self.assertRaises(HTTPException) as missing:
                _validate_query_sources(
                    "SELECT * FROM banking.silver.fact_customers LIMIT 10",
                    schema,
                    "banking",
                )
            with self.assertRaises(HTTPException) as other_catalog:
                _validate_query_sources(
                    "SELECT * FROM retail.silver.customers LIMIT 10",
                    schema,
                    "banking",
                )

        self.assertEqual(missing.exception.status_code, 422)
        self.assertIn("No query was run", missing.exception.detail)
        self.assertEqual(other_catalog.exception.status_code, 422)

    def test_lineage_reports_actual_databricks_source_instead_of_inferred_layers(self) -> None:
        with (
            patch("app.governance.settings.warehouse", "databricks"),
            patch("app.governance.settings.databricks_domain_schema", "silver"),
            patch("app.governance.databricks_domain_catalog", return_value="retail"),
        ):
            lineage = build_lineage(
                "retail",
                "What is the order count?",
                "SELECT COUNT(*) FROM retail_orders LIMIT 10",
                [{"order_count": 1}],
                "analyst",
                "AU-1234",
                ["retail_orders"],
                True,
                {},
            )

        self.assertEqual(lineage["catalog"], "retail")
        self.assertIn("retail.silver.retail_orders", lineage["trace"][2]["detail"])
        self.assertEqual([layer["layer"] for layer in lineage["medallion"]], ["Queried Databricks source"])
        self.assertNotIn("Bronze · raw", str(lineage["medallion"]))
        self.assertEqual(extract_tables("WITH recent AS (SELECT id FROM retail_orders) SELECT id FROM recent"), ["retail_orders"])

    def test_credit_score_distribution_uses_the_real_customers_table(self) -> None:
        schema = {
            "tables": {
                "customers": [
                    {"name": "customer_id", "type": "STRING"},
                    {"name": "credit_score", "type": "INT"},
                ],
                "banking_customers": [
                    {"name": "account_number", "type": "STRING"},
                    {"name": "credit_score", "type": "INT"},
                ],
            }
        }
        with patch("app.services.sql_generator.settings.warehouse", "databricks"):
            sql = generate_sql(
                "Show the distribution of credit_score in fact_customers",
                schema,
                False,
                "banking",
            )

        self.assertIn("FROM `customers`", sql)
        self.assertIn("GROUP BY score_range", sql)
        self.assertIn("LIMIT 200", sql)
        self.assertNotIn("fact_customers", sql)

    def test_databricks_llm_prompt_uses_catalog_scope_not_nonexistent_domain_column(self) -> None:
        schema = {"tables": {"retail_orders": [{"name": "order_amount", "type": "DOUBLE"}]}}
        with (
            patch("app.services.sql_generator.settings.warehouse", "databricks"),
            patch(
                "app.services.sql_generator.complete_json",
                return_value={"sql": "SELECT SUM(order_amount) FROM retail_orders LIMIT 200"},
            ) as complete,
        ):
            generate_sql("Total order amount", schema, False, "retail")

        system_prompt = complete.call_args.kwargs["system"]
        self.assertIn("Do not add a domain predicate", system_prompt)
        self.assertNotIn("Always filter domain =", system_prompt)


if __name__ == "__main__":
    unittest.main()
