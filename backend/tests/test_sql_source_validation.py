"""Regression tests for generated-SQL source and column validation.

Column lists are the Unity Catalog metadata verified against the live warehouse
(banking.information_schema.columns etc.); no columns are invented here.
"""

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.config import settings
from app.governance import extract_tables, source_tables
from app.services.orchestrator import _validate_query_columns, _validate_query_sources


def _schema(**tables: tuple[str, ...]) -> dict:
    return {"tables": {name: [{"name": column, "type": "string"} for column in columns] for name, columns in tables.items()}}


BANKING = _schema(
    gold_transactions=(
        "account_id", "amount", "channel", "city", "counterparty_account", "merchant_category", "status",
        "txn_id", "txn_timestamp", "txn_type", "ingestion_timestamp", "source_file",
        "_gold_processed_timestamp", "_gold_source",
    ),
    agg_transactions_daily=("_gold_date", "record_count", "total_amount", "avg_amount"),
    summary_transactions=("account_id", "avg_amount", "max_amount", "min_amount", "total_amount"),
)
INSURANCE = _schema(
    gold_claims=(
        "approved_amount", "claim_date", "claim_id", "claim_status", "claim_type", "claimed_amount",
        "hospital_or_garage", "incident_date", "policy_id", "settlement_date",
    ),
)


class SqlValidationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        for patcher in (
            patch.object(settings, "warehouse", "databricks"),
            patch.object(settings, "databricks_domain_schema", "gold"),
            patch.object(
                settings,
                "databricks_domain_catalogs",
                "banking:banking,finance:banking,insurance:insurance,retail:retail,healthcare:healthcare",
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def validate(self, sql: str, schema: dict = BANKING, domain: str = "banking") -> None:
        _validate_query_sources(sql, schema, domain)
        _validate_query_columns(sql, schema, domain)

    def rejected(self, sql: str, schema: dict = BANKING, domain: str = "banking") -> str:
        with self.assertRaises(HTTPException) as raised:
            self.validate(sql, schema, domain)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("No query was run", raised.exception.detail)
        return raised.exception.detail


class ColumnValidationTests(SqlValidationTestCase):
    def test_txn_timestamp_amount_and_txn_type_are_accepted_on_gold_transactions(self) -> None:
        self.validate(
            "SELECT txn_type, SUM(amount) AS volume FROM gold_transactions "
            "WHERE txn_timestamp >= '2026-03-01' AND txn_timestamp < '2026-04-01' GROUP BY txn_type"
        )

    def test_fully_qualified_names_and_aliases_resolve_to_the_right_table(self) -> None:
        self.validate(
            "SELECT g.txn_type, SUM(g.amount) AS volume FROM banking.gold.gold_transactions AS g "
            "WHERE g.txn_timestamp >= '2026-03-01' GROUP BY g.txn_type"
        )
        self.validate("SELECT SUM(`g`.`amount`) AS volume FROM `banking`.`gold`.`gold_transactions` `g`")
        self.validate(
            "SELECT t.txn_type, d.total_amount FROM gold_transactions t "
            "JOIN agg_transactions_daily d ON d._gold_date = CAST(t.txn_timestamp AS DATE)"
        )

    def test_a_nonexistent_column_is_rejected_and_the_table_is_named(self) -> None:
        detail = self.rejected("SELECT SUM(fee_amount) AS fees FROM gold_transactions")
        self.assertIn("fee_amount (not a column of gold_transactions)", detail)

    def test_a_column_of_another_table_is_not_accepted(self) -> None:
        # txn_timestamp exists on gold_transactions, but not on the aggregate tables.
        detail = self.rejected("SELECT total_amount FROM summary_transactions WHERE txn_timestamp >= '2026-03-01'")
        self.assertIn("txn_timestamp (not a column of summary_transactions)", detail)
        detail = self.rejected("SELECT t._gold_date FROM gold_transactions t")
        self.assertIn("t._gold_date (not a column of gold_transactions)", detail)
        self.rejected("SELECT d.txn_timestamp FROM agg_transactions_daily d")

    def test_an_alias_reused_in_a_subquery_is_not_falsely_rejected(self) -> None:
        self.validate(
            "SELECT t.txn_type FROM gold_transactions t "
            "WHERE t.amount > (SELECT AVG(t.total_amount) FROM agg_transactions_daily t)"
        )


class SourceValidationTests(SqlValidationTestCase):
    def test_from_inside_function_syntax_is_not_mistaken_for_a_table(self) -> None:
        sql = (
            "SELECT merchant_category, SUM(amount) AS total FROM gold_transactions "
            "WHERE EXTRACT(MONTH FROM txn_timestamp) = 3 AND EXTRACT(YEAR FROM txn_timestamp) = 2026 "
            "AND TRIM(BOTH ' ' FROM txn_type) <> '' AND SUBSTRING(channel FROM 1 FOR 3) <> 'X' "
            "GROUP BY merchant_category"
        )
        self.validate(sql)
        self.assertEqual(extract_tables(sql), ["gold_transactions"])

    def test_ctes_are_not_sources_but_the_tables_they_read_are(self) -> None:
        sql = (
            "WITH monthly AS (SELECT date_format(txn_timestamp, 'yyyy-MM') AS month, SUM(amount) AS volume "
            "FROM gold_transactions GROUP BY 1) SELECT month, volume FROM monthly"
        )
        self.validate(sql)
        self.assertEqual(source_tables(sql), [("gold_transactions",)])
        self.rejected("WITH m AS (SELECT amount FROM loan_writeoffs) SELECT amount FROM m")

    def test_each_domain_is_checked_against_its_own_catalog_and_schema(self) -> None:
        self.validate("SELECT claim_status, SUM(claimed_amount) AS claimed FROM insurance.gold.gold_claims GROUP BY 1", INSURANCE, "insurance")
        # Finance is mapped to the banking catalog.
        self.validate("SELECT SUM(total_amount) AS total FROM banking.gold.agg_transactions_daily", BANKING, "finance")
        detail = self.rejected("SELECT SUM(amount) AS total FROM banking.gold.gold_transactions", BANKING, "insurance")
        self.assertIn("outside the authorized insurance source", detail)
        self.rejected("SELECT SUM(amount) AS total FROM banking.silver.gold_transactions")

    def test_cross_domain_and_unauthorized_sources_remain_blocked(self) -> None:
        self.rejected("SELECT SUM(billed_amount) AS billed FROM healthcare.gold.fact_claims")
        self.rejected("SELECT SUM(amount) AS total FROM healthcare.gold.gold_transactions")
        self.rejected("SELECT action_name FROM system.access.audit")
        self.rejected("SELECT * FROM read_files('s3://bucket/data.csv')")
        self.rejected("SELECT amount FROM gold_transactions UNION ALL SELECT claimed_amount FROM insurance.gold.gold_claims")


if __name__ == "__main__":
    unittest.main()
