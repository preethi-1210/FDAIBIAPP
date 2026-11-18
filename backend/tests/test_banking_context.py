"""Banking business context: consistent with the registry, the data policy and itself."""

import json
import os
import re
import unittest
from unittest.mock import patch

from app.services import banking_context as ctx
from app.services.banking_query import parse_question
from app.services.banking_registry import APPROVED_TABLES, METRICS, metric_by_id
from app.services.domain_sql_policy import ALLOWLIST_DIR

# Pinned when the context was verified. If a metric formula changes, this fails on purpose:
# update it only after the formula change itself has been approved.
APPROVED_FORMULA_FINGERPRINT = "20205b448c9016033a3ac3518ace0b5a46820a0ffb09baa8c4e0f5de56750999"
USER_PENDING_RULES = {
    "closed_accounts_in_total_balance", "closed_accounts_in_deposits", "active_customer_definition",
    "loan_default_definition", "fraud_classification", "profitability_calculation",
}


def _dimensions() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for metric in METRICS:
        for dimension in metric.source.dimensions:
            found.setdefault(dimension.id, set()).update(dimension.values)
    return found


class RegistryUnchangedTests(unittest.TestCase):
    def test_metric_formulas_are_unchanged(self) -> None:
        self.assertEqual(ctx.registry_fingerprint(), APPROVED_FORMULA_FINGERPRINT)

    def test_every_registry_metric_has_a_governed_view(self) -> None:
        views = {view["id"]: view for view in ctx.metric_registry()}
        self.assertEqual(set(views), {metric.id for metric in METRICS})
        self.assertEqual(len(views), 19)
        for metric in METRICS:
            view = views[metric.id]
            self.assertEqual(view["description"], metric.definition)
            for measure in metric.measures:
                self.assertIn(measure.expression, view["formula"])
            self.assertEqual((view["source_catalog"], view["source_schema"]), ("banking", "gold"))
            self.assertTrue(set(view["source_tables"]) <= APPROVED_TABLES and view["source_tables"])
            self.assertIsNone(view["business_owner"])
            self.assertNotEqual(view["validation_status"], "BUSINESS_APPROVED")

    def test_balance_metrics_disclose_the_pending_closed_account_rule(self) -> None:
        views = {view["id"]: view for view in ctx.metric_registry()}
        for metric_id in ("total_account_balance", "average_account_balance"):
            self.assertIn("closed_accounts_in_total_balance", views[metric_id]["pending_rules"])


class RuleTests(unittest.TestCase):
    def test_the_six_disputed_rules_stay_pending(self) -> None:
        rules = {rule.id: rule for rule in ctx.RULES}
        for rule_id in USER_PENDING_RULES:
            self.assertEqual(rules[rule_id].status, ctx.PENDING, rule_id)
            self.assertTrue(rules[rule_id].while_pending, rule_id)

    def test_rules_are_well_formed(self) -> None:
        ids = [rule.id for rule in ctx.RULES]
        self.assertEqual(len(ids), len(set(ids)))
        for rule in ctx.RULES:
            self.assertIn(rule.status, (ctx.VERIFIED, ctx.PENDING))
            self.assertTrue(rule.evidence)
            for metric_id in rule.applies_to:
                self.assertIsNotNone(metric_by_id(metric_id), f"{rule.id} names unknown metric {metric_id}")


class CapabilityTests(unittest.TestCase):
    def test_all_ten_capabilities_with_valid_references(self) -> None:
        self.assertEqual([cap.id for cap in ctx.CAPABILITIES], list("ABCDEFGHIJ"))
        rule_ids = {rule.id for rule in ctx.RULES}
        for cap in ctx.CAPABILITIES:
            self.assertIn(cap.status, ("available", "partial", "unavailable"))
            for metric_id in cap.metrics:
                self.assertIsNotNone(metric_by_id(metric_id), f"{cap.id} names unknown metric {metric_id}")
            self.assertTrue(set(cap.pending_rules) <= rule_ids)
            for _topic, reason in cap.unavailable:
                self.assertTrue(reason)

    def test_capabilities_without_data_claim_no_metrics(self) -> None:
        caps = {cap.id: cap for cap in ctx.CAPABILITIES}
        for cap_id in ("D", "H", "J"):
            self.assertEqual(caps[cap_id].status, "unavailable")
            self.assertEqual(caps[cap_id].metrics, ())


class GlossaryTests(unittest.TestCase):
    def test_every_mapping_points_at_something_real(self) -> None:
        dimensions = _dimensions()
        for term in ctx.GLOSSARY:
            self.assertIn(term.status, ctx.STATUSES)
            if term.maps_to is None:
                self.assertNotEqual(term.status, ctx.VERIFIED, f"{term.term} is verified but maps to nothing")
                continue
            kind, target = term.maps_to
            if kind == "metric":
                self.assertIsNotNone(metric_by_id(target), term.term)
            elif kind == "dimension":
                dimension, _, value = target.partition("=")
                self.assertIn(dimension, dimensions, term.term)
                if value:
                    self.assertIn(value, dimensions[dimension], term.term)
            else:
                table, column = target.split(".")
                self.assertIn(column, {col.name for col in ctx.table(table).columns}, term.term)
            if term.table:
                self.assertIn(term.column, {col.name for col in ctx.table(term.table).columns}, term.term)

    def test_unverified_terms_map_to_nothing(self) -> None:
        for term in ctx.GLOSSARY:
            if term.status in (ctx.PENDING, ctx.UNAVAILABLE):
                self.assertIsNone(term.maps_to, term.term)
                self.assertFalse(term.synonyms, f"{term.term} must not resolve by synonym while {term.status}")

    def test_synonyms_are_unique_and_not_also_ambiguous(self) -> None:
        seen: dict[str, str] = {}
        ambiguous = {word for term in ctx.GLOSSARY for word in term.ambiguous}
        for term in ctx.GLOSSARY:
            for word in term.synonyms:
                self.assertNotIn(word, seen, f"'{word}' is a synonym of both {seen.get(word)} and {term.term}")
                self.assertNotIn(word, ambiguous, f"'{word}' cannot be both a synonym and ambiguous")
                seen[word] = term.term

    def test_merchant_is_not_merchant_category(self) -> None:
        terms = {term.term: term for term in ctx.GLOSSARY}
        self.assertEqual(terms["Merchant"].status, ctx.UNAVAILABLE)
        self.assertIsNone(terms["Merchant"].maps_to)
        self.assertEqual(terms["Merchant transaction"].status, ctx.UNAVAILABLE)
        self.assertEqual(terms["Merchant category"].maps_to, ("dimension", "merchant_category"))
        self.assertNotIn("merchant", terms["Merchant category"].synonyms)
        self.assertNotIn("merchants", terms["Merchant category"].synonyms)

    def test_known_misleading_words_are_ambiguous_not_synonyms(self) -> None:
        ambiguous = {word for term in ctx.GLOSSARY for word in term.ambiguous}
        for word in ("inactive accounts", "deposits", "payments", "outstanding loans", "money held by customers", "merchants", "fraud"):
            self.assertIn(word, ambiguous)

    def test_postgres_shaped_rows_replace_the_demo_glossary(self) -> None:
        rows = ctx.glossary_for_postgres_shape()
        self.assertTrue(rows)
        terms = {row["business_term"] for row in rows}
        self.assertNotIn("npl", terms)
        self.assertNotIn("churn", terms)
        for row in rows:
            self.assertEqual(row["domain"], "banking")
            self.assertIn(row["table_name"], APPROVED_TABLES)
            self.assertIn(ctx.sensitivity(row["column_name"]), ("business", "linking_key", "restricted"))


class DictionaryTests(unittest.TestCase):
    def test_dictionary_covers_exactly_the_approved_tables(self) -> None:
        self.assertEqual({tbl.name for tbl in ctx.DICTIONARY}, APPROVED_TABLES)

    def test_dictionary_columns_match_the_reviewed_allowlist(self) -> None:
        allowlist = json.loads((ALLOWLIST_DIR / "banking.json").read_text(encoding="utf-8"))["tables"]
        for tbl in ctx.DICTIONARY:
            listed = set(allowlist[tbl.name]["columns"]) | set(allowlist[tbl.name]["restricted"]) | set(allowlist[tbl.name].get("never", []))
            self.assertEqual({col.name for col in tbl.columns}, listed, tbl.name)

    def test_sensitivity_agrees_with_the_allowlist(self) -> None:
        allowlist = json.loads((ALLOWLIST_DIR / "banking.json").read_text(encoding="utf-8"))["tables"]
        for tbl in ctx.DICTIONARY:
            for col in tbl.columns:
                restricted = col.name in allowlist[tbl.name]["restricted"]
                self.assertEqual(ctx.sensitivity(col.name) in ("restricted", "always_redacted"), restricted, f"{tbl.name}.{col.name}")

    def test_personal_fields_are_classified_restricted(self) -> None:
        for column in ("first_name", "last_name", "email", "phone", "pan_number", "address", "dob", "account_id", "expiry_date"):
            self.assertIn(ctx.sensitivity(column), ("restricted", "always_redacted"), column)

    def test_each_entity_has_one_primary_key_and_valid_foreign_keys(self) -> None:
        for tbl in ctx.DICTIONARY:
            if tbl.kind != "entity":
                continue
            self.assertEqual(sum(1 for col in tbl.columns if col.key == "PK"), 1, tbl.name)
            for col in tbl.columns:
                if col.key and col.key.startswith("FK "):
                    parent, parent_column = col.key[3:].split(".")
                    self.assertIn(parent_column, {c.name for c in ctx.table(parent).columns})


class RelationshipTests(unittest.TestCase):
    def test_relationships_match_foreign_keys_in_the_dictionary(self) -> None:
        declared = {
            (tbl.name, col.name, *col.key[3:].split("."))
            for tbl in ctx.DICTIONARY for col in tbl.columns if col.key and col.key.startswith("FK ")
        }
        self.assertEqual({(r.child, r.child_column, r.parent, r.parent_column) for r in ctx.RELATIONSHIPS}, declared)

    def test_registry_joins_use_only_verified_relationships(self) -> None:
        verified = {(r.parent, r.parent_column, r.child_column) for r in ctx.RELATIONSHIPS}
        joins = set()
        for metric in METRICS:
            joins.update(re.findall(r"LEFT JOIN (\w+) \w+ ON \w+\.(\w+) = \w+\.(\w+)", metric.source.from_clause))
            for dimension in metric.source.dimensions:
                for clause in dimension.joins:
                    joins.update(re.findall(r"LEFT JOIN (\w+) \w+ ON \w+\.(\w+) = \w+\.(\w+)", clause))
        self.assertTrue(joins)
        for parent, parent_column, child_column in joins:
            self.assertIn((parent, parent_column, child_column), verified, f"unverified join to {parent}")
        used = {(r.parent, r.parent_column, r.child_column) for r in ctx.RELATIONSHIPS if r.used_in_registry}
        self.assertEqual(used, joins)


class SourceTests(unittest.TestCase):
    def test_databricks_is_the_only_analytical_source(self) -> None:
        self.assertEqual(ctx.SOURCES["analytical"]["system"], "Databricks")
        self.assertIn("consistency", ctx.SOURCES["consistency"]["role"].lower())
        self.assertIn("None", ctx.SOURCES["fallback"])
        for gold, pg in ctx.SOURCES["consistency"]["tables"].items():
            self.assertIn(gold, APPROVED_TABLES)
            self.assertTrue(pg.startswith("apex_bank_data_"))


class BehaviourUnchangedTests(unittest.TestCase):
    """Phase 2 adds context only: planning of the brief's questions is as it was."""

    def test_planner_results_are_unchanged(self) -> None:
        from datetime import date

        expected = {
            "How many active accounts do we have?": "account_count",
            "What is the total account balance?": "total_account_balance",
            "What is the average transaction amount?": "average_transaction_amount",
            "How much loan repayment was collected?": "repayment_collected",
        }
        for question, metric_id in expected.items():
            self.assertEqual(parse_question(question, None, date(2026, 3, 15)).plan.metric_id, metric_id)

    def test_context_endpoint_serves_the_gold_banking_glossary(self) -> None:
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app import main
        from app.auth import create_access_token, hash_password
        from app.config import settings
        from app.db import Base, get_db
        from app.models import AppUser, Glossary

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as db:
            db.add(AppUser(username="bank.user", password_hash=hash_password("pw-123456"), domain="banking", display_name="B"))
            db.add(Glossary(domain="banking", business_term="npl", table_name="loans", column_name="status", description="demo"))
            db.commit()

        def override_db():
            with Session() as db:
                yield db

        main.fastapi_app.dependency_overrides[get_db] = override_db
        self.addCleanup(main.fastapi_app.dependency_overrides.clear)
        headers = {"Authorization": f"Bearer {create_access_token('bank.user', 'banking', 'analyst')}"}
        client = TestClient(main.fastapi_app)
        with patch.object(settings, "warehouse", "databricks"), patch.object(settings, "databricks_domain_catalogs", "banking:banking"):
            body = client.get("/context", headers=headers).json()
        terms = {item["term"] for item in body["glossary"]}
        self.assertNotIn("npl", terms)
        self.assertIn("active account", terms)
        with patch.object(settings, "warehouse", "local"):
            body = client.get("/context", headers=headers).json()
        self.assertEqual({item["term"] for item in body["glossary"]}, {"npl"})


@unittest.skipUnless(os.getenv("RYZE_LIVE_TESTS") == "1", "set RYZE_LIVE_TESTS=1 to check against the configured Databricks and PostgreSQL")
class LiveContextTests(unittest.TestCase):
    """Read-only: the recorded facts still hold, and PostgreSQL banking still agrees with gold."""

    def test_recorded_row_counts_and_orphans_hold(self) -> None:
        from app.warehouse import execute_readonly

        for tbl in ctx.DICTIONARY:
            if tbl.kind == "entity":
                rows = execute_readonly(f"SELECT COUNT(*) AS n FROM {tbl.name} LIMIT 1", "banking")
                self.assertEqual(int(rows[0]["n"]), tbl.rows, tbl.name)
        for rel in ctx.RELATIONSHIPS:
            sql = (
                f"SELECT COUNT(*) AS n FROM {rel.child} c LEFT JOIN {rel.parent} p ON p.{rel.parent_column} = c.{rel.child_column} "
                f"WHERE p.{rel.parent_column} IS NULL LIMIT 1"
            )
            self.assertEqual(int(execute_readonly(sql, "banking")[0]["n"]), rel.unmatched_children, f"{rel.child}->{rel.parent}")

    def test_postgres_banking_reconciles_with_gold(self) -> None:
        from sqlalchemy import text

        from app.db import engine
        from app.warehouse import execute_readonly

        gold = {
            row["status"]: (int(row["n"]), round(float(row["bal"] or 0), 2))
            for row in execute_readonly("SELECT status, COUNT(*) AS n, ROUND(SUM(balance), 2) AS bal FROM gold_accounts GROUP BY status LIMIT 10", "banking")
        }
        tables = ctx.SOURCES["consistency"]["tables"]
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            pg = {
                status: (int(n), round(float(bal or 0), 2))
                for status, n, bal in connection.execute(text(
                    f'SELECT status, COUNT(*), ROUND(SUM(NULLIF(balance, \'\')::numeric), 2) FROM banking."{tables["gold_accounts"]}" GROUP BY status'
                ))
            }
            for gold_table in ("gold_loans", "gold_loan_repayments", "gold_cards", "gold_branches"):
                count = connection.execute(text(f'SELECT COUNT(*) FROM banking."{tables[gold_table]}"')).scalar()
                self.assertEqual(count, ctx.table(gold_table).rows, gold_table)
        self.assertEqual(pg, gold)


if __name__ == "__main__":
    unittest.main()
