import unittest
from datetime import date
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.auth import AuthUser, get_current_user
from app.db import Base
from app.domains import DEMO_USERS, DOMAINS
from app.models import (
    AppUser,
    HitlReview,
    HealthcarePatient,
    QueryHistory,
    UserDomainTenant,
)
from app.services.context_graph import ContextGraphService
from app.services.guardrail import GuardrailError
from app.services.orchestrator import Orchestrator
from app.services.tenant_scope import scope_healthcare_sql
from app.seed import _seed_demo_healthcare
from app.security import hash_password
from app.seed import ensure_schema
from app.main import history, hitl_action, hitl_queue


class HealthcareTenantScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = {
            "tables": {
                "healthcare_patients": [
                    {"name": "tenant_id"},
                    {"name": "patient_key"},
                    {"name": "postal_region"},
                    {"name": "patient_status"},
                ],
                "healthcare_encounters": [
                    {"name": "tenant_id"},
                    {"name": "encounter_key"},
                    {"name": "patient_key"},
                    {"name": "encounter_type"},
                ],
            }
        }

    def test_rewrites_every_source_with_the_authoritative_tenant(self) -> None:
        sql = (
            "SELECT p.postal_region, COUNT(*) AS patient_count "
            "FROM healthcare_patients p LEFT JOIN healthcare_encounters e "
            "ON e.patient_key = p.patient_key GROUP BY p.postal_region"
        )
        scoped = scope_healthcare_sql(sql, "tenant-a' OR 1=1 --", self.schema)

        self.assertIn("p.tenant_id = 'tenant-a'' OR 1=1 --'", scoped)
        self.assertIn("e.tenant_id = 'tenant-a'' OR 1=1 --'", scoped)
        self.assertIn("LEFT JOIN", scoped.upper())

    def test_rejects_missing_assignment_and_non_healthcare_sources(self) -> None:
        with self.assertRaises(GuardrailError):
            scope_healthcare_sql("SELECT COUNT(*) FROM healthcare_patients", None, self.schema)
        with self.assertRaises(GuardrailError):
            scope_healthcare_sql("SELECT COUNT(*) FROM customers", "tenant-a", self.schema)

    def test_rejects_unscoped_record_level_identifiers(self) -> None:
        sql = "SELECT patient_key, COUNT(*) FROM healthcare_patients GROUP BY patient_key"
        with self.assertRaises(GuardrailError):
            scope_healthcare_sql(sql, "tenant-a", self.schema)

    def test_rejects_sources_without_verified_tenant_columns(self) -> None:
        schema = {"tables": {"healthcare_patients": [{"name": "patient_key"}]}}
        with self.assertRaises(GuardrailError):
            scope_healthcare_sql("SELECT COUNT(*) FROM healthcare_patients", "tenant-a", schema)

    def test_healthcare_context_fallback_never_returns_other_domain_tables(self) -> None:
        service = ContextGraphService()
        service.tables = {
            "healthcare_patients": [{"name": "tenant_id"}],
            "customers": [{"name": "id"}],
        }
        with patch.object(service.rag, "retrieve", return_value=[]):
            result = service.resolve("unmatched query", domain="healthcare")
        self.assertEqual(set(result["tables"]), {"healthcare_patients"})


class HealthcareAuthorizationAndSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_healthcare_requires_an_authoritative_user_domain_tenant_mapping(self) -> None:
        self.db.add(
            AppUser(
                username="health.user",
                password_hash=hash_password("healthcare123"),
                domain="healthcare",
                display_name="Healthcare Analyst",
            )
        )
        self.db.commit()

        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="signed-token")
        with patch("app.auth.jwt.decode", return_value={
            "sub": "health.user",
            "domain": "healthcare",
            "role": "analyst",
        }):
            with self.assertRaises(HTTPException) as raised:
                get_current_user(credentials, self.db)
        self.assertEqual(raised.exception.status_code, 403)

        self.db.add(
            UserDomainTenant(username="health.user", domain="healthcare", tenant_id="tenant-a")
        )
        self.db.commit()
        with patch("app.auth.jwt.decode", return_value={
            "sub": "health.user",
            "domain": "healthcare",
            "role": "analyst",
        }):
            user = get_current_user(credentials, self.db)
        self.assertEqual(user.tenant_id, "tenant-a")

    def test_healthcare_seed_is_synthetic_and_idempotent(self) -> None:
        for user in DEMO_USERS:
            self.db.add(
                AppUser(
                    username=user["username"],
                    password_hash=hash_password(user["password"]),
                    domain=user["domain"],
                    display_name=user["display_name"],
                )
            )
        self.db.commit()

        _seed_demo_healthcare(self.db)
        patient_count = self.db.query(HealthcarePatient).count()
        assignment_count = self.db.query(UserDomainTenant).filter(
            UserDomainTenant.domain == "healthcare"
        ).count()
        _seed_demo_healthcare(self.db)

        self.assertEqual(self.db.query(HealthcarePatient).count(), patient_count)
        self.assertEqual(
            self.db.query(UserDomainTenant).filter(UserDomainTenant.domain == "healthcare").count(),
            assignment_count,
        )
        self.assertGreater(patient_count, 0)
        self.assertEqual(set(DOMAINS), {"banking", "finance", "healthcare", "insurance", "retail"})
        self.assertTrue(
            all(patient.patient_key.startswith("DEMO-PAT-") for patient in self.db.query(HealthcarePatient).all())
        )

    def test_healthcare_dashboard_isolated_by_tenant(self) -> None:
        _seed_demo_healthcare(self.db)
        self.db.add(
            HealthcarePatient(
                tenant_id="other-tenant",
                patient_key="DEMO-PAT-OTHER",
                age_band="35-49",
                sex="unspecified",
                postal_region="West",
                enrollment_date=date.today(),
                patient_status="active",
            )
        )
        self.db.commit()
        with (
            patch("app.services.orchestrator.settings.warehouse", "local"),
            patch("app.services.orchestrator.settings.database_url", "sqlite:///test.db"),
        ):
            demo_metrics = Orchestrator._healthcare_dashboard_metrics(
                object(),
                self.db,
                "demo-healthcare",
            )
            other_metrics = Orchestrator._healthcare_dashboard_metrics(
                object(),
                self.db,
                "other-tenant",
            )

        self.assertEqual(demo_metrics["customer_label"], "Patients")
        self.assertNotEqual(demo_metrics["customers"], other_metrics["customers"])
        self.assertEqual(other_metrics["customers"], 1)

    def test_legacy_schema_failure_preserves_existing_rows(self) -> None:
        legacy_engine = create_engine("sqlite:///:memory:")
        try:
            with legacy_engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
                connection.exec_driver_sql("INSERT INTO customers (name) VALUES ('preserved')")
            with patch("app.seed.engine", legacy_engine):
                with self.assertRaises(RuntimeError):
                    ensure_schema()
            with legacy_engine.connect() as connection:
                self.assertEqual(
                    connection.exec_driver_sql("SELECT name FROM customers").scalar_one(),
                    "preserved",
                )
        finally:
            legacy_engine.dispose()

    def test_healthcare_history_and_review_records_are_tenant_scoped(self) -> None:
        user = AuthUser(
            username="health.user",
            domain="healthcare",
            role="analyst",
            tenant_id="tenant-b",
        )
        for tenant_id in ("tenant-a", "tenant-b"):
            self.db.add(
                QueryHistory(
                    username="health.user",
                    domain="healthcare",
                    tenant_id=tenant_id,
                    question=f"Summary for {tenant_id}",
                    answer="Aggregate result",
                )
            )
            self.db.add(
                HitlReview(
                    audit_id=f"AU-{tenant_id}",
                    username="health.user",
                    domain="healthcare",
                    tenant_id=tenant_id,
                    question=f"Review for {tenant_id}",
                )
            )
        self.db.commit()

        history_rows = history(self.db, user)
        review_rows = hitl_queue(self.db, user)
        self.assertEqual([row["question"] for row in history_rows], ["Summary for tenant-b"])
        self.assertEqual([row["question"] for row in review_rows], ["Review for tenant-b"])
        with self.assertRaises(HTTPException) as raised:
            hitl_action(1, "approve", self.db, user)
        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
