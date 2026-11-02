import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.governance import build_lineage, extract_tables
from app.services.guardrail import validate_sql
from app.services.orchestrator import (
    _databricks_healthcare_dashboard_queries,
    _databricks_dashboard_queries,
    _stale_weekly_aggregate_error,
    _validate_query_relevance,
    _validate_query_sources,
)
from app.services.sql_generator import generate_sql
from app.services.guardrail import GuardrailError
from app.warehouse import execute_databricks_domain_batch


class DatabricksDashboardQueryTests(unittest.TestCase):
    def test_healthcare_dashboard_uses_only_aggregate_safe_gold_sources(self) -> None:
        queries = _databricks_healthcare_dashboard_queries()
        self.assertEqual(len(queries), 10)
        all_sql = "\n".join(validate_sql(query) for query in queries).lower()
        for source in (
            "agg_claims_monthly",
            "agg_claims_daily",
            "gold_claims",
            "gold_patients",
            "gold_encounters",
            "gold_facilities",
        ):
            self.assertIn(source, all_sql)
        for sensitive_column in ("first_name", "last_name", "dob", "phone", "email", "national_id", "patient_id", "claim_id"):
            self.assertNotIn(sensitive_column, all_sql)

    def test_healthcare_dashboard_requires_and_uses_tenant_catalog_mapping(self) -> None:
        queries = ["SELECT COUNT(*) AS total FROM gold_claims LIMIT 1"]
        with (
            patch.multiple(
                "app.warehouse.settings",
                warehouse="databricks",
                databricks_healthcare_tenant_catalogs="tenant-a:healthcare_tenant_a",
                databricks_domain_schema="gold",
                databricks_server_hostname="workspace.example.com",
                databricks_http_path="/sql/warehouse",
                databricks_warehouse_id="",
                databricks_token="test-token",
            ),
            patch("app.warehouse.warehouse_status", return_value={"ready": True}),
            patch("app.warehouse._databricks_run", return_value=[[{"total": 0}]]) as run,
        ):
            result = execute_databricks_domain_batch("healthcare", queries, tenant_id="tenant-a")
            self.assertEqual(result, [[{"total": 0}]])
            self.assertEqual(run.call_args.args[:2], ("healthcare_tenant_a", "gold"))

            with self.assertRaisesRegex(GuardrailError, "No Databricks catalog is configured for this Healthcare tenant"):
                execute_databricks_domain_batch("healthcare", queries, tenant_id="tenant-b")

    def test_dashboard_queries_are_guarded_aggregate_selects_for_real_domain_schemas(self) -> None:
        for domain in ("banking", "insurance", "retail"):
            with self.subTest(domain=domain):
                queries = _databricks_dashboard_queries(domain)
                self.assertEqual(len(queries), 10)
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

        with patch(
            "app.services.orchestrator.settings.databricks_domain_catalogs",
            "banking:banking,insurance:insurance,retail:retail",
        ), self.assertRaises(GuardrailError):
            _databricks_dashboard_queries("finance")

    def test_finance_dashboard_uses_only_its_configured_catalog(self) -> None:
        with patch(
            "app.services.orchestrator.settings.databricks_domain_catalogs",
            "banking:banking,finance:finance_warehouse,retail:retail",
        ):
            queries = _databricks_dashboard_queries("finance")

        qualified_sources = [
            source
            for query in queries
            for source in ("`finance_warehouse`.gold.", "`banking`.gold.")
            if source in query
        ]
        self.assertTrue(qualified_sources)
        self.assertNotIn("`banking`.gold.", "\n".join(queries))
        self.assertEqual(len(queries), 10)
        self.assertIn("WHERE 1 = 0", queries[-1])
        for query in queries:
            with self.subTest(query=query):
                safe_query = validate_sql(query)
                self.assertTrue(safe_query.lstrip().lower().startswith("select"))
                self.assertIn("LIMIT", safe_query.upper())

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

    def test_retail_order_count_last_week_uses_the_daily_gold_aggregate(self) -> None:
        schema = {
            "tables": {
                "agg_retail_orders_daily": [
                    {"name": "record_count", "type": "BIGINT"},
                    {"name": "_gold_date", "type": "DATE"},
                ]
            }
        }
        with (
            patch("app.services.sql_generator.settings.warehouse", "databricks"),
            patch(
                "app.services.sql_generator.complete_json",
                side_effect=AssertionError("supported weekly order queries should not need the LLM"),
            ) as complete_json,
        ):
            sql = generate_sql(
                "How many orders did we get last week?",
                schema,
                False,
                "retail",
            )

        self.assertIn("AS order_count", sql)
        self.assertIn("THEN record_count ELSE 0 END", sql)
        self.assertIn("FROM `agg_retail_orders_daily`", sql)
        self.assertIn("DATE_TRUNC('WEEK', CURRENT_DATE())", sql)
        self.assertIn("MAX(_gold_date) AS latest_data_date", sql)
        self.assertIn("requested_period_start", sql)
        complete_json.assert_not_called()

    def test_retail_weekly_answer_does_not_call_stale_data_zero_orders(self) -> None:
        sql = "SELECT order_count, latest_data_date, requested_period_start FROM agg_retail_orders_daily"
        message = _stale_weekly_aggregate_error(
            "How many orders did we get last week?",
            sql,
            [{
                "order_count": 0,
                "latest_data_date": "2026-06-18",
                "requested_period_start": "2026-10-05",
            }],
            "retail",
        )

        self.assertIsNotNone(message)
        self.assertIn("latest order data is 2026-06-18", message)
        self.assertIn("source is refreshed", message)
        self.assertIsNone(
            _stale_weekly_aggregate_error(
                "How many orders did we get last week?",
                sql,
                [{
                    "order_count": 3,
                    "latest_data_date": "2026-10-12",
                    "requested_period_start": "2026-10-05",
                }],
                "retail",
            )
        )

    def test_domain_suggestions_use_their_verified_gold_sources_without_llm_sql(self) -> None:
        cases = (
            (
                "banking",
                "Show transaction volume for last week",
                "agg_transactions_daily",
                "transaction_volume",
            ),
            (
                "finance",
                "Show account balances by month",
                "agg_accounts_monthly",
                "total_balance",
            ),
            (
                "insurance",
                "Show total premium by policy status",
                "dim_policies",
                "policy_status",
            ),
        )
        schemas = {
            "banking": {
                "agg_transactions_daily": [
                    {"name": "_gold_date"},
                    {"name": "record_count"},
                    {"name": "total_amount"},
                ]
            },
            "finance": {
                "agg_accounts_monthly": [
                    {"name": "_gold_month"},
                    {"name": "record_count"},
                    {"name": "total_balance"},
                ]
            },
            "insurance": {
                "dim_policies": [
                    {"name": "status"},
                    {"name": "premium_amount"},
                ]
            },
        }

        with (
            patch("app.services.sql_generator.settings.warehouse", "databricks"),
            patch(
                "app.services.sql_generator.complete_json",
                side_effect=AssertionError("supported domain prompts should not need the LLM"),
            ) as complete_json,
        ):
            for domain, question, source, result_column in cases:
                with self.subTest(domain=domain):
                    sql = generate_sql(
                        question,
                        {"tables": schemas[domain]},
                        False,
                        domain,
                    )
                    self.assertIn(f"FROM {source}", sql)
                    self.assertIn(result_column, sql)

        complete_json.assert_not_called()

    def test_chat_query_rejects_sql_for_an_unrelated_business_entity(self) -> None:
        sql = (
            "SELECT total_claim_amount FROM agg_claims_daily "
            "WHERE _gold_date = (SELECT MAX(_gold_date) FROM agg_claims_daily) LIMIT 200"
        )

        with self.assertRaises(HTTPException) as raised:
            _validate_query_relevance(
                "How many orders did we get last week?",
                sql,
                "banking",
            )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("does not address the requested order data", raised.exception.detail)
        self.assertIn("No query was run", raised.exception.detail)

    def test_chat_query_accepts_relevant_aggregate_table_names(self) -> None:
        _validate_query_relevance(
            "How many orders did we get last week?",
            "SELECT SUM(record_count) FROM agg_retail_orders_daily",
            "retail",
        )

    def test_chat_query_cannot_pass_relevance_check_with_an_alias_only(self) -> None:
        with self.assertRaises(HTTPException):
            _validate_query_relevance(
                "How many orders did we get last week?",
                "SELECT total_claim_amount AS order_count FROM agg_claims_daily",
                "banking",
            )

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
