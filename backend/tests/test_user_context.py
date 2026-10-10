import unittest
from types import SimpleNamespace
from unittest.mock import patch

import networkx as nx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.auth import AuthUser
from app.main import user_context
from app.models import Glossary


class UserContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Glossary.__table__.create(self.engine)
        self.db = Session(self.engine)
        self.db.add_all(
            [
                Glossary(
                    domain="banking",
                    business_term="Daily activity",
                    table_name="transactions",
                    column_name="transaction_type",
                    description="A transaction category.",
                ),
                Glossary(
                    domain="banking",
                    business_term="Private identifier",
                    table_name="accounts",
                    column_name="account_number",
                    description="Restricted identifier.",
                ),
                Glossary(
                    domain="retail",
                    business_term="Retail sales",
                    table_name="orders",
                    column_name="total_amount",
                    description="Sales total.",
                ),
            ]
        )
        self.db.commit()
        graph = nx.Graph()
        graph.add_edge("accounts", "transactions", columns=["account_id"])
        self.graph = SimpleNamespace(
            tables={
                "accounts": [
                    {"name": "account_number", "type": "STRING"},
                    {"name": "balance", "type": "DECIMAL"},
                ],
                "transactions": [
                    {"name": "transaction_type", "type": "STRING"},
                    {"name": "amount", "type": "DECIMAL"},
                ],
                "orders": [{"name": "total_amount", "type": "DECIMAL"}],
                "glossary": [{"name": "business_term", "type": "STRING"}],
            },
            domain_tables={},
            graph=graph,
        )
        self.graph_patch = patch("app.main.orchestrator.graph", self.graph)
        self.warehouse_patch = patch("app.main.settings.warehouse", "local")
        self.status_patch = patch("app.main.warehouse_status", return_value={"ready": True})
        self.graph_patch.start()
        self.warehouse_patch.start()
        self.status_patch.start()

    def tearDown(self) -> None:
        self.status_patch.stop()
        self.warehouse_patch.stop()
        self.graph_patch.stop()
        self.db.close()
        self.engine.dispose()

    def test_returns_domain_scoped_context_without_sensitive_columns(self) -> None:
        result = user_context(db=self.db, user=AuthUser("analyst", "banking", "analyst"))

        self.assertEqual(result["user"]["permitted_domains"], ["banking"])
        self.assertEqual([term["term"] for term in result["glossary"]], ["Daily activity"])
        self.assertEqual(result["tables"][0]["name"], "accounts")
        self.assertEqual(result["tables"][0]["columns"], ["balance"])
        self.assertEqual([table["name"] for table in result["tables"]], ["accounts", "transactions"])
        self.assertEqual(result["relationships"][0]["evidence"], "database foreign-key metadata")

    def test_rejects_an_unauthorized_domain(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            user_context(domain="retail", db=self.db, user=AuthUser("analyst", "banking", "analyst"))

        self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
