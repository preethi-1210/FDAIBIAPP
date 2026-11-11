"""Parsed table and column allowlists for model-written SQL, for all five domains."""

import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.auth import AuthUser
from app.config import settings
from app.sensitive_data import _CREDENTIAL_COLUMN, _CVV_COLUMN
from app.services import orchestrator
from app.services.domain_sql_policy import (
    ALLOWLIST_DIR,
    DOMAINS,
    DomainAllowlist,
    check_model_sql,
    enforce_domain_policy,
    load_allowlist,
)
from app.services.guardrail import GuardrailError

RETAIL = DomainAllowlist(
    "retail",
    {
        "gold_orders": frozenset({"order_id", "customer_id", "order_date", "total_amount", "channel", "store_id"}),
        "gold_customers": frozenset({"customer_id", "loyalty_tier", "signup_date"}),
        "gold_stores": frozenset({"store_id", "region"}),
    },
    {"gold_customers": frozenset({"email", "first_name"})},
)


def allowed(sql: str, allowlist: DomainAllowlist = RETAIL, role: str = "analyst", catalog: str = "retail") -> None:
    enforce_domain_policy(sql, allowlist, catalog, "gold", role)


class PolicyTests(unittest.TestCase):
    def refused(self, sql: str, fragment: str, role: str = "analyst", allowlist: DomainAllowlist = RETAIL) -> None:
        with self.assertRaises(GuardrailError) as raised:
            allowed(sql, allowlist, role)
        self.assertIn(fragment, str(raised.exception))

    def test_approved_shapes_pass(self) -> None:
        for sql in (
            "SELECT channel, SUM(total_amount) AS revenue FROM gold_orders GROUP BY channel ORDER BY revenue DESC LIMIT 10",
            "SELECT o.channel FROM retail.gold.gold_orders o LIMIT 5",
            "SELECT COUNT(*) AS n FROM gold_orders LIMIT 1",
            "WITH m AS (SELECT DATE_TRUNC('MONTH', order_date) AS month, SUM(total_amount) AS rev FROM gold_orders GROUP BY 1) "
            "SELECT month, rev FROM m ORDER BY month LIMIT 50",
            "SELECT x.tier, COUNT(*) AS n FROM (SELECT c.loyalty_tier AS tier FROM gold_orders o JOIN gold_customers c "
            "ON o.customer_id = c.customer_id) x GROUP BY x.tier LIMIT 10",
            "SELECT o.order_id FROM gold_orders o WHERE EXISTS (SELECT 1 FROM gold_customers c WHERE c.customer_id = o.customer_id "
            "AND c.loyalty_tier = 'GOLD') LIMIT 10",
            "SELECT channel AS label FROM gold_orders UNION ALL SELECT loyalty_tier AS label FROM gold_customers LIMIT 10",
            "SELECT order_id, total_amount FROM gold_orders QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date DESC) = 1 LIMIT 10",
            "SELECT s.region, SUM(o.total_amount) AS revenue FROM gold_orders o JOIN gold_stores s ON s.store_id = o.store_id GROUP BY s.region LIMIT 10",
        ):
            with self.subTest(sql=sql[:60]):
                allowed(sql)

    def test_unknown_columns_fail_closed_wherever_they_are(self) -> None:
        for sql in (
            "SELECT secret_margin FROM gold_orders LIMIT 1",
            "SELECT o.secret_margin FROM gold_orders o LIMIT 1",
            "WITH m AS (SELECT order_id FROM gold_orders) SELECT m.secret_margin FROM m LIMIT 1",
            "WITH m AS (SELECT secret_margin FROM gold_orders) SELECT 1 AS one FROM m LIMIT 1",
            "SELECT x.v FROM (SELECT secret_margin AS v FROM gold_orders) x LIMIT 1",
            "SELECT channel FROM gold_orders UNION ALL SELECT secret_margin FROM gold_customers LIMIT 1",
            "SELECT order_id FROM gold_orders WHERE customer_id IN (SELECT customer_id FROM gold_customers WHERE churn_score > 0.5) LIMIT 1",
        ):
            with self.subTest(sql=sql[:60]):
                self.refused(sql, "not approved")

    def test_tables_outside_the_allowlist_are_refused(self) -> None:
        self.refused("SELECT metric FROM gold_metadata LIMIT 1", "not approved")
        self.refused("WITH gold_orders AS (SELECT 1 AS x) SELECT payload FROM gold_raw_events LIMIT 1", "not approved")

    def test_other_catalogs_and_system_metadata_are_refused(self) -> None:
        self.refused("SELECT amount FROM banking.gold.gold_transactions LIMIT 1", "outside")
        self.refused("SELECT channel FROM retail.silver.gold_orders LIMIT 1", "outside")
        self.refused("SELECT table_name FROM retail.information_schema.tables LIMIT 1", "System metadata")
        self.refused("SELECT * FROM system.access.audit LIMIT 1", "'*'")

    def test_wildcards_are_refused_except_in_count(self) -> None:
        self.refused("SELECT * FROM gold_orders LIMIT 1", "'*'")
        self.refused("SELECT o.* FROM gold_orders o LIMIT 1", "'*'")

    def test_restricted_columns_are_for_administrators_only(self) -> None:
        sql = "SELECT c.email FROM gold_customers c LIMIT 5"
        self.refused(sql, "restricted to administrators")
        allowed(sql, role="admin")
        self.refused("SELECT loyalty_tier FROM gold_customers WHERE first_name = 'A' LIMIT 5", "restricted")

    def test_unparseable_or_multiple_statements_are_refused(self) -> None:
        self.refused("SELEC channel FRM gold_orders", "could not be parsed")
        self.refused("SELECT channel FROM gold_orders LIMIT 1; SELECT channel FROM gold_orders LIMIT 1", "Exactly one")

    def test_allowlist_is_narrowed_to_the_live_schema(self) -> None:
        live = {"gold_orders": [{"name": "order_id"}, {"name": "channel"}]}
        narrowed = RETAIL.narrowed_to(live)
        self.assertEqual(narrowed.tables, frozenset({"gold_orders"}))
        enforce_domain_policy("SELECT channel FROM gold_orders LIMIT 1", narrowed, "retail", "gold", "analyst")
        with self.assertRaises(GuardrailError):
            enforce_domain_policy("SELECT total_amount FROM gold_orders LIMIT 1", narrowed, "retail", "gold", "analyst")
        with self.assertRaises(GuardrailError):
            enforce_domain_policy("SELECT region FROM gold_stores LIMIT 1", narrowed, "retail", "gold", "analyst")


class ModeTests(unittest.TestCase):
    live = {"gold_orders": [{"name": "channel"}]}

    def test_modes(self) -> None:
        bad = "SELECT secret_margin FROM gold_orders LIMIT 1"
        with patch("app.services.domain_sql_policy.load_allowlist", return_value=RETAIL):
            with patch.object(settings, "sql_allowlist_mode", "enforce"), self.assertRaises(GuardrailError):
                check_model_sql(bad, "retail", "retail", "gold", "analyst", self.live)
            with patch.object(settings, "sql_allowlist_mode", "shadow"), self.assertLogs("app.services.domain_sql_policy", "WARNING") as logs:
                self.assertIn("not approved", check_model_sql(bad, "retail", "retail", "gold", "analyst", self.live))
            self.assertNotIn("SELECT", logs.output[0])
            with patch.object(settings, "sql_allowlist_mode", "off"):
                self.assertIsNone(check_model_sql(bad, "retail", "retail", "gold", "analyst", self.live))
            # An unknown mode is treated as enforce, not as off.
            with patch.object(settings, "sql_allowlist_mode", "enforced"), self.assertRaises(GuardrailError):
                check_model_sql(bad, "retail", "retail", "gold", "analyst", self.live)

    def test_a_domain_without_an_allowlist_fails_closed(self) -> None:
        with patch.object(settings, "sql_allowlist_mode", "enforce"), self.assertRaises(GuardrailError):
            check_model_sql("SELECT 1 AS x LIMIT 1", "crypto", "crypto", "gold", "analyst", {})

    def test_orchestrator_refuses_model_sql_in_enforce_mode(self) -> None:
        user = AuthUser(username="ret.user", domain="retail", role="analyst")
        with (
            patch.object(settings, "warehouse", "databricks"),
            patch.object(settings, "sql_allowlist_mode", "enforce"),
            patch("app.services.domain_sql_policy.load_allowlist", return_value=RETAIL),
            patch("app.services.orchestrator.databricks_domain_catalog", return_value="retail"),
            patch.object(settings, "databricks_domain_schema", "gold"),
        ):
            with self.assertRaises(HTTPException) as raised:
                orchestrator._apply_domain_allowlist("SELECT secret_margin FROM gold_orders LIMIT 1", user, {"tables": self.live})
            orchestrator._apply_domain_allowlist("SELECT channel FROM gold_orders LIMIT 1", user, {"tables": self.live})
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("No query was run", raised.exception.detail)


class AllowlistFileTests(unittest.TestCase):
    def test_every_domain_has_a_well_formed_allowlist(self) -> None:
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                data = json.loads((ALLOWLIST_DIR / f"{domain}.json").read_text(encoding="utf-8"))
                self.assertEqual(data["domain"], domain)
                allowlist = load_allowlist(domain)
                self.assertTrue(allowlist.tables)
                for table in ("gold_metadata", "gold_column_profile", "gold_table_profile", "gold_data_quality", "gold_load_summary"):
                    self.assertNotIn(table, allowlist.tables)
                for table in allowlist.tables:
                    usable = allowlist.columns[table] | allowlist.restricted.get(table, frozenset())
                    self.assertFalse([c for c in usable if _CVV_COLUMN.search(c) or _CREDENTIAL_COLUMN.search(c)])
                    self.assertFalse(allowlist.columns[table] & allowlist.restricted.get(table, frozenset()))

    def test_banking_keeps_to_the_registry_tables(self) -> None:
        from app.services.banking_registry import APPROVED_TABLES

        self.assertEqual(load_allowlist("banking").tables, APPROVED_TABLES)

    def test_each_domain_refuses_another_domains_catalog(self) -> None:
        for domain in DOMAINS:
            other = "retail" if domain != "retail" else "banking"
            allowlist = load_allowlist(domain)
            table = sorted(allowlist.tables)[0]
            column = sorted(allowlist.columns[table])[0]
            with self.subTest(domain=domain):
                enforce_domain_policy(f"SELECT {column} FROM {domain}.gold.{table} LIMIT 1", allowlist, domain, "gold", "analyst")
                with self.assertRaises(GuardrailError):
                    enforce_domain_policy(f"SELECT {column} FROM {other}.gold.{table} LIMIT 1", allowlist, domain, "gold", "analyst")
                with self.assertRaises(GuardrailError):
                    enforce_domain_policy(f"SELECT {column} FROM {domain}.information_schema.columns LIMIT 1", allowlist, domain, "gold", "analyst")

    def test_compiled_dashboard_sql_uses_only_allowlisted_tables_and_columns(self) -> None:
        # Code-owned SQL may use restricted keys to count or join, so it is checked as an administrator.
        mapping = "banking:banking,finance:finance,insurance:insurance,retail:retail"
        for domain in ("banking", "finance", "insurance", "retail"):
            with patch.object(settings, "databricks_domain_catalogs", mapping):
                queries = orchestrator._databricks_dashboard_queries(domain)
            for sql in queries:
                with self.subTest(domain=domain, sql=" ".join(sql.split())[:70]):
                    enforce_domain_policy(sql.replace("`", ""), load_allowlist(domain), domain, "gold", "admin")


if __name__ == "__main__":
    unittest.main()
