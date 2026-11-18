"""GET /context/foresight: authorization, tenant isolation and consistency with the registries."""

import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.auth import create_access_token, hash_password
from app.config import settings
from app.db import Base, get_db
from app.models import AppUser, UserDomainTenant
from app.services import foresight
from app.services.banking_registry import BANKING
from app.services.domain_registries import REGISTRIES

DOMAINS = ("banking", "finance", "insurance", "retail", "healthcare")
CATALOGS = "banking:banking,finance:finance,insurance:insurance,retail:retail,healthcare:healthcare"
JOIN = re.compile(r"JOIN\s+(\w+)\s+(\w+)\s+ON\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)", re.IGNORECASE)


def registry_joins(registry) -> set[frozenset]:
    """Each registry join as an unordered pair of (table, column) ends."""
    joins = set()
    for metric in registry.metrics:
        aliases = {}
        first = re.match(r"\s*(\w+)\s+(\w+)", metric.source.from_clause)
        if first:
            aliases[first.group(2)] = first.group(1)
        clauses = [metric.source.from_clause, *(j for d in metric.source.dimensions for j in d.joins)]
        for clause in clauses:
            for table, alias, *_ in JOIN.findall(clause):
                aliases[alias] = table
        for clause in clauses:
            for _table, _alias, a1, c1, a2, c2 in JOIN.findall(clause):
                if a1 in aliases and a2 in aliases:
                    joins.add(frozenset({(aliases[a1], c1), (aliases[a2], c2)}))
    return joins


def fake_graph():
    """Column metadata as Unity Catalog returns it, for every approved table, plus one technical table."""
    import networkx as nx

    class Graph:
        domain_tables = {}
        tables = {}
        graph = nx.Graph()
        synchronized_at = "2026-11-12T07:00:00+00:00"

    for domain in DOMAINS:
        registry = foresight.registry_for(domain)
        tables = {}
        for table in registry.approved_tables:
            columns = {"email", "first_name", "_gold_source"}
            for metric in registry.metrics:
                columns.update(metric.required_columns)
            for rel in foresight.relationships(domain):
                if rel["child"] == table:
                    columns.add(rel["child_column"])
                if rel["parent"] == table:
                    columns.add(rel["parent_column"])
            tables[table] = [{"name": name, "type": "STRING"} for name in sorted(columns)]
        tables["gold_metadata"] = [{"name": "metric", "type": "STRING"}]
        tables["snapshot_x"] = [{"name": "id", "type": "STRING"}]
        Graph.domain_tables[domain] = tables
    return Graph()


class ForesightApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([
                AppUser(username="admin", password_hash=hash_password("pw-123456"), domain="all", display_name="Admin"),
                AppUser(username="bank.user", password_hash=hash_password("pw-123456"), domain="banking", display_name="Bank"),
                AppUser(username="health.user", password_hash=hash_password("pw-123456"), domain="healthcare", display_name="Health"),
                UserDomainTenant(username="health.user", domain="healthcare", tenant_id="tenant-a"),
            ])
            db.commit()

        def override_db():
            with self.Session() as db:
                yield db

        main.fastapi_app.dependency_overrides[get_db] = override_db
        self.addCleanup(main.fastapi_app.dependency_overrides.clear)
        for target, value in (
            (patch.object(settings, "warehouse", "databricks"), None),
            (patch.object(settings, "databricks_domain_catalogs", CATALOGS), None),
            (patch.object(settings, "databricks_domain_schema", "gold"), None),
            (patch.object(settings, "databricks_healthcare_tenant_catalogs", "tenant-a:healthcare_tenant_a"), None),
            (patch.object(main.orchestrator, "graph", fake_graph()), None),
            # Foresight must never query the warehouse.
            (patch("app.warehouse.execute_readonly", side_effect=AssertionError("warehouse queried")), None),
        ):
            target.start()
            self.addCleanup(target.stop)
        self.client = TestClient(main.fastapi_app)

    def get(self, username: str, domain: str, role: str = "analyst", query: str = ""):
        token = create_access_token(username, domain, role)
        return self.client.get(f"/context/foresight{query}", headers={"Authorization": f"Bearer {token}"})

    def test_requires_authentication(self) -> None:
        self.assertEqual(self.client.get("/context/foresight").status_code, 401)

    def test_a_business_user_sees_only_their_domain(self) -> None:
        self.assertEqual(self.get("bank.user", "banking").status_code, 200)
        for other in ("finance", "insurance", "retail", "healthcare"):
            response = self.get("bank.user", "banking", query=f"?domain={other}")
            self.assertEqual(response.status_code, 403, other)

    def test_healthcare_needs_a_tenant_and_uses_its_catalog(self) -> None:
        refused = self.get("admin", "banking", "admin", "?domain=healthcare")
        self.assertEqual(refused.status_code, 403)
        self.assertIn("tenant", refused.json()["detail"])
        allowed = self.get("health.user", "healthcare").json()
        self.assertEqual(allowed["overview"]["analytical_source"], "healthcare_tenant_a.gold")
        self.assertTrue(any(rule["id"] == "healthcare_tenant_isolation" for rule in allowed["rules"]))

    def test_context_endpoint_keeps_the_same_authorization(self) -> None:
        token = create_access_token("bank.user", "banking", "analyst")
        response = self.client.get("/context?domain=retail", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 403)
        body = self.client.get("/context", headers={"Authorization": f"Bearer {token}"}).json()
        self.assertEqual(set(body), {"user", "source", "tables", "relationships", "glossary", "metrics", "rules", "synchronized_at"})

    def test_every_domain_is_served_with_all_five_sections(self) -> None:
        for domain in DOMAINS[:4]:
            with self.subTest(domain=domain):
                body = self.get("admin", "banking", "admin", f"?domain={domain}").json()
                self.assertTrue(body["available"])
                for section in ("overview", "entities", "relationships", "metrics", "rules"):
                    self.assertTrue(body[section], f"{domain} {section}")
                self.assertEqual({e["table"] for e in body["entities"]}, set(foresight.registry_for(domain).approved_tables))
                self.assertEqual({t["table"] for t in body["technical_tables"]}, {"gold_metadata", "snapshot_x"})


class ConsistencyTests(unittest.TestCase):
    graph = fake_graph()

    def build(self, domain: str) -> dict:
        return foresight.build(domain, self.graph, domain, "2026-11-12T07:00:00+00:00")

    def test_registry_metrics_and_formulas_are_exactly_the_registrys(self) -> None:
        for domain in DOMAINS:
            registry = foresight.registry_for(domain)
            with self.subTest(domain=domain):
                with patch.object(settings, "warehouse", "databricks"):
                    shown = {m["id"]: m for m in self.build(domain)["metrics"] if m["status"] == "registry"}
                self.assertEqual(set(shown), {metric.id for metric in registry.metrics})
                for metric in registry.metrics:
                    for measure in metric.measures:
                        self.assertIn(measure.expression, shown[metric.id]["formula"])
                    self.assertEqual(shown[metric.id]["description"], metric.definition)

    def test_configured_relationships_are_joins_the_registry_uses(self) -> None:
        for domain in DOMAINS:
            registry = foresight.registry_for(domain)
            used = registry_joins(registry)
            with self.subTest(domain=domain):
                for rel in foresight.relationships(domain):
                    pair = frozenset({(rel["child"], rel["child_column"]), (rel["parent"], rel["parent_column"])})
                    self.assertIn(rel["child"], registry.approved_tables)
                    self.assertIn(rel["parent"], registry.approved_tables)
                    self.assertEqual(rel["status"] == "configured", pair in used, rel["join"])
                configured = {frozenset({(r["child"], r["child_column"]), (r["parent"], r["parent_column"])}) for r in foresight.relationships(domain) if r["status"] == "configured"}
                self.assertEqual(configured, used, "every registry join is listed as a relationship")

    def test_sensitive_columns_are_never_listed(self) -> None:
        with patch.object(settings, "warehouse", "databricks"):
            for domain in DOMAINS:
                for entity in self.build(domain)["entities"]:
                    names = {column["name"] for column in entity["columns"]}
                    self.assertFalse({"email", "first_name"} & names, entity["table"])
                    self.assertGreaterEqual(entity["hidden_sensitive_columns"], 2)

    def test_nothing_is_labelled_enforced_that_is_not(self) -> None:
        for domain in DOMAINS:
            for rule in self.build(domain)["rules"]:
                if rule["kind"] == "check":
                    self.assertEqual(rule["status"], "PENDING_BUSINESS_APPROVAL")
                    self.assertIn("not enforced", rule["description"])
                if rule["kind"] in {"enforced", "shadow"}:
                    self.assertTrue(rule.get("implementation"), rule["id"])
            shadow = [r for r in self.build(domain)["rules"] if r["id"] == "platform_model_sql_allowlist"]
            self.assertEqual(shadow[0]["kind"], "shadow")

    def test_secondary_metrics_are_marked_and_never_presented_as_registry(self) -> None:
        from app.services.metrics import METRICS

        for domain in DOMAINS:
            secondary = [m for m in self.build(domain)["metrics"] if m["status"] == "secondary"]
            self.assertEqual({m["id"] for m in secondary}, {m.key for m in METRICS if m.domain == domain})
            for metric in secondary:
                self.assertIn("Not yet reconciled", metric["validation_note"])

    def test_banking_pending_rules_and_capabilities_are_carried_through(self) -> None:
        body = self.build("banking")
        pending = {r["id"] for r in body["rules"] if r["kind"] == "pending"}
        self.assertEqual(len(pending), 9)
        self.assertEqual([c["id"] for c in body["capabilities"]], list("ABCDEFGHIJ"))
        balance = next(m for m in body["metrics"] if m["id"] == "total_account_balance")
        self.assertIn("closed_accounts_in_total_balance", balance["pending_rules"])

    def test_unavailable_metadata_is_reported_not_hidden(self) -> None:
        class Empty:
            domain_tables: dict = {}

        with patch.object(settings, "warehouse", "databricks"):
            body = foresight.build("retail", Empty(), "retail", None)
        self.assertFalse(body["overview"]["metadata_available"])
        self.assertTrue(all(not e["metadata_available"] for e in body["entities"]))
        self.assertTrue(body["metrics"])


if __name__ == "__main__":
    unittest.main()
