import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token, hash_password
from app.db import Base, get_db
from app.main import fastapi_app
from app.models import AccessAuditEvent, AppUser, DomainAccessRequest, UserDomainGrant


class AccessControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([
                AppUser(username="admin", password_hash=hash_password("admin-password"), domain="all", display_name="Admin"),
                AppUser(username="bank.user", password_hash=hash_password("bank-password"), domain="banking", display_name="Bank"),
                AppUser(username="ret.user", password_hash=hash_password("retail-password"), domain="retail", display_name="Retail"),
            ])
            db.commit()

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        fastapi_app.dependency_overrides[get_db] = override_db
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _headers(self, username: str, domain: str, role: str = "analyst") -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(username, domain, role)}"}

    def _request(self, domain: str = "retail", username: str = "bank.user") -> dict:
        response = self.client.post(
            "/access/requests",
            json={"domain": domain, "reason": "Need retail figures for the quarterly review."},
            headers=self._headers(username, "banking"),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_unauthenticated_and_unauthorized_calls_are_rejected(self) -> None:
        self.assertEqual(self.client.get("/access/domains").status_code, 401)
        self.assertEqual(self.client.get("/admin/users").status_code, 401)
        # A forged role claim does not make a business user an administrator.
        forged = self._headers("bank.user", "banking", role="admin")
        for method, path in (
            ("get", "/admin/overview"),
            ("get", "/admin/users"),
            ("get", "/admin/access-requests"),
            ("get", "/admin/audit"),
            ("post", "/admin/access-requests/1/approve"),
            ("post", "/admin/users/bank.user/domains"),
            ("patch", "/admin/users/bank.user/role"),
        ):
            with self.subTest(path=path):
                kwargs = {"json": {"domain": "retail", "role": "admin"}} if method != "get" else {}
                response = getattr(self.client, method)(path, headers=forged, **kwargs)
                self.assertEqual(response.status_code, 403, response.text)

    def test_a_business_user_cannot_open_an_unassigned_domain_by_token_or_switch(self) -> None:
        response = self.client.get("/me", headers=self._headers("bank.user", "retail"))
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            "/auth/switch-domain", json={"domain": "retail"}, headers=self._headers("bank.user", "banking")
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            "/auth/login", json={"username": "bank.user", "password": "bank-password", "domain": "retail"}
        )
        self.assertEqual(response.status_code, 403)

    def test_request_approval_grants_the_domain_and_is_audited(self) -> None:
        request = self._request()
        self.assertEqual(request["status"], "pending")
        self.assertEqual(request["current_domain"], "banking")

        duplicate = self.client.post(
            "/access/requests",
            json={"domain": "retail", "reason": "Second attempt for the same domain."},
            headers=self._headers("bank.user", "banking"),
        )
        self.assertEqual(duplicate.status_code, 409)
        admin_me = self.client.get("/me", headers=self._headers("admin", "retail", "admin")).json()
        self.assertEqual(admin_me["notifications"], 1)

        # Requesting does not grant access.
        self.assertEqual(self.client.get("/me", headers=self._headers("bank.user", "retail")).status_code, 403)

        approved = self.client.post(
            f"/admin/access-requests/{request['id']}/approve",
            json={"note": "Approved for Q4"},
            headers=self._headers("admin", "banking", "admin"),
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        body = approved.json()
        self.assertEqual((body["status"], body["decided_by"], body["decision_note"]), ("approved", "admin", "Approved for Q4"))
        self.assertIsNotNone(body["decided_at"])

        again = self.client.post(
            f"/admin/access-requests/{request['id']}/reject", headers=self._headers("admin", "banking", "admin")
        )
        self.assertEqual(again.status_code, 409)

        me = self.client.get("/me", headers=self._headers("bank.user", "retail"))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["authorized_domains"], ["banking", "retail"])
        self.assertEqual(me.json()["notifications"], 1)
        switched = self.client.post(
            "/auth/switch-domain", json={"domain": "retail"}, headers=self._headers("bank.user", "banking")
        )
        self.assertEqual(switched.status_code, 200)
        self.assertEqual(switched.json()["domain"], "retail")
        self.assertIn("ryze_session=", switched.headers["set-cookie"])
        # The session cookie from the switch is now stored, so (like a browser) send Origin;
        # cookie-authenticated writes from unknown origins are refused.
        login = self.client.post(
            "/auth/login",
            json={"username": "bank.user", "password": "bank-password", "domain": "retail"},
            headers={"Origin": "http://localhost:3000"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["domains"], ["banking", "retail"])

        history = self.client.get("/access/requests", headers=self._headers("bank.user", "banking")).json()
        self.assertEqual(history[0]["status"], "approved")
        self.assertEqual(self.client.get("/me", headers=self._headers("bank.user", "banking")).json()["notifications"], 0)

        with self.Session() as db:
            actions = [row.action for row in db.query(AccessAuditEvent).order_by(AccessAuditEvent.id)]
            grant = db.query(UserDomainGrant).one()
        self.assertEqual(actions, ["request_submitted", "request_approved"])
        self.assertEqual((grant.username, grant.domain, grant.granted_by), ("bank.user", "retail", "admin"))

    def test_rejection_keeps_access_unchanged_and_is_visible_to_the_user(self) -> None:
        request = self._request("insurance")
        rejected = self.client.post(
            f"/admin/access-requests/{request['id']}/reject",
            json={"note": "Not required for your role"},
            headers=self._headers("admin", "banking", "admin"),
        )
        self.assertEqual(rejected.json()["status"], "rejected")
        self.assertEqual(self.client.get("/me", headers=self._headers("bank.user", "insurance")).status_code, 403)
        history = self.client.get("/access/requests", headers=self._headers("bank.user", "banking")).json()
        self.assertEqual((history[0]["status"], history[0]["decision_note"]), ("rejected", "Not required for your role"))
        # A new request may be filed after a rejection.
        self._request("insurance")

    def test_invalid_requests_are_refused(self) -> None:
        headers = self._headers("bank.user", "banking")
        for body, expected in (
            ({"domain": "banking", "reason": "Already my primary domain."}, 409),
            ({"domain": "all", "reason": "I would like every domain please."}, 422),
            ({"domain": "retail", "reason": "short"}, 422),
        ):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/access/requests", json=body, headers=headers).status_code, expected)
        admin_request = self.client.post(
            "/access/requests",
            json={"domain": "retail", "reason": "Administrators never need this."},
            headers=self._headers("admin", "banking", "admin"),
        )
        self.assertEqual(admin_request.status_code, 400)

    def test_revoking_a_grant_takes_effect_on_the_next_request(self) -> None:
        admin = self._headers("admin", "banking", "admin")
        self.assertEqual(
            self.client.post("/admin/users/bank.user/domains", json={"domain": "retail"}, headers=admin).status_code, 201
        )
        retail_token = self._headers("bank.user", "retail")
        self.assertEqual(self.client.get("/me", headers=retail_token).status_code, 200)
        self.assertEqual(self.client.delete("/admin/users/bank.user/domains/retail", headers=admin).status_code, 200)
        self.assertEqual(self.client.get("/me", headers=retail_token).status_code, 403)
        self.assertEqual(self.client.delete("/admin/users/bank.user/domains/banking", headers=admin).status_code, 400)

    def test_role_changes_are_guarded(self) -> None:
        admin = self._headers("admin", "banking", "admin")
        self_change = self.client.patch(
            "/admin/users/admin/role", json={"role": "analyst", "primary_domain": "retail"}, headers=admin
        )
        self.assertEqual(self_change.status_code, 400)
        promoted = self.client.patch("/admin/users/ret.user/role", json={"role": "admin"}, headers=admin)
        self.assertEqual(promoted.json()["role"], "admin")
        self.assertEqual(
            self.client.get("/me", headers=self._headers("ret.user", "insurance")).json()["role"], "admin"
        )
        demoted = self.client.patch(
            "/admin/users/ret.user/role", json={"role": "analyst", "primary_domain": "finance"}, headers=admin
        )
        self.assertEqual(demoted.json()["role"], "analyst")
        self.assertEqual(self.client.get("/me", headers=self._headers("ret.user", "retail")).status_code, 403)
        missing_domain = self.client.patch("/admin/users/bank.user/role", json={"role": "analyst"}, headers=admin)
        self.assertEqual(missing_domain.status_code, 422)

    def test_the_database_rejects_duplicate_pending_requests(self) -> None:
        with self.Session() as db:
            for _ in range(2):
                db.add(DomainAccessRequest(
                    username="bank.user", current_domain="banking", requested_domain="retail",
                    reason="Concurrent duplicate", status="pending",
                ))
            with self.assertRaises(IntegrityError):
                db.commit()


if __name__ == "__main__":
    unittest.main()
