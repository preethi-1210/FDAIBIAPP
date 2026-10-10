import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.main import app
from app.db import Base
from app.models import BankAccount, BankLoan, BankTransaction, Customer, Glossary
from app.seed import _seed_banking_demo_data
from app.services.sql_generator import generate_sql


class BankingDemoDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all(
            Customer(
                domain="banking",
                name=f"Banking demo customer {index}",
                segment="Mass",
                region="North",
                signup_date=date(2024, 1, 1),
                churned=0,
            )
            for index in range(80)
        )
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_seed_creates_linked_synthetic_accounts_transactions_and_loans_once(self) -> None:
        _seed_banking_demo_data(self.session)
        first_counts = (
            self.session.query(BankAccount).count(),
            self.session.query(BankTransaction).count(),
            self.session.query(BankLoan).count(),
        )
        _seed_banking_demo_data(self.session)

        self.assertGreaterEqual(first_counts[0], 80)
        self.assertGreater(first_counts[1], first_counts[0])
        self.assertGreater(first_counts[2], 0)
        self.assertEqual(
            first_counts,
            (
                self.session.query(BankAccount).count(),
                self.session.query(BankTransaction).count(),
                self.session.query(BankLoan).count(),
            ),
        )
        self.assertTrue(
            all(
                account.account_number.startswith("DEMO-BANK-")
                for account in self.session.query(BankAccount).all()
            )
        )
        self.assertEqual(
            self.session.query(Glossary).filter_by(domain="banking", table_name="loans").count(),
            2,
        )

    def test_banking_queries_use_dedicated_domain_scoped_tables(self) -> None:
        with patch("app.services.sql_generator.complete_json", return_value=None), patch(
            "app.services.sql_generator.settings.warehouse", "local"
        ), patch(
            "app.services.sql_generator.settings.database_url", "sqlite:///./test.db"
        ):
            account_sql = generate_sql(
                "What is total deposit balance by region?",
                {},
                False,
                "banking",
            )
            transaction_sql = generate_sql(
                "Show transaction volume for last week",
                {},
                False,
                "banking",
            )
            loan_sql = generate_sql("Show NPL by segment", {}, False, "banking")

        self.assertIn("FROM accounts a", account_sql)
        self.assertIn("c.region", account_sql)
        self.assertIn("a.domain = 'banking'", account_sql)
        self.assertIn("FROM transactions t", transaction_sql)
        self.assertIn("t.transaction_date >= DATE('now', '-7 days')", transaction_sql)
        self.assertIn("FROM loans l", loan_sql)
        self.assertIn("l.status = 'delinquent'", loan_sql)
        self.assertIn("c.segment", loan_sql)


if __name__ == "__main__":
    unittest.main()
