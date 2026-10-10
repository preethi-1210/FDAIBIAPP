import unittest

from fastapi import HTTPException
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.auth import authenticate, register_user
from app.config import settings
from app.db import Base
from app.models import AppUser
from app.main import signup
from app.schemas import SignupRequest
from app.security import verify_password


class UserRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def test_registration_creates_domain_scoped_analyst_and_jwt(self) -> None:
        token, user = register_user(
            self.db,
            "  New.Analyst ",
            "a-long-and-unique-password",
            "banking",
            "New Analyst",
        )

        account = self.db.query(AppUser).one()
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        self.assertEqual(user.username, "new.analyst")
        self.assertEqual(user.domain, "banking")
        self.assertEqual(user.role, "analyst")
        self.assertEqual(claims["domain"], "banking")
        self.assertEqual(claims["role"], "analyst")
        self.assertTrue(verify_password("a-long-and-unique-password", account.password_hash))

    def test_registered_user_can_sign_in_case_insensitively(self) -> None:
        register_user(self.db, "new.analyst", "a-long-and-unique-password", "retail", "New Analyst")

        token, user = authenticate(self.db, "NEW.ANALYST", "a-long-and-unique-password", "retail")

        self.assertTrue(token)
        self.assertEqual(user.username, "new.analyst")
        self.assertEqual(user.domain, "retail")

    def test_signup_endpoint_returns_domain_scoped_jwt_response(self) -> None:
        response = signup(
            SignupRequest(
                username="new.analyst",
                display_name="New Analyst",
                password="a-long-and-unique-password",
                domain="insurance",
            ),
            self.db,
        )

        self.assertEqual(response.username, "new.analyst")
        self.assertEqual(response.domain, "insurance")
        self.assertEqual(response.role, "analyst")
        self.assertEqual(response.domains, ["insurance"])
        self.assertTrue(response.access_token)

    def test_registration_rejects_admin_domain_escalation(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            register_user(self.db, "not.admin", "a-long-and-unique-password", "all", "Not Admin")
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(self.db.query(AppUser).count(), 0)

    def test_registration_rejects_duplicate_username_without_case_bypass(self) -> None:
        register_user(self.db, "new.analyst", "a-long-and-unique-password", "retail", "New Analyst")

        with self.assertRaises(HTTPException) as raised:
            register_user(self.db, "NEW.ANALYST", "another-long-password", "banking", "Other Analyst")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.db.query(AppUser).count(), 1)

    def test_registration_rejects_invalid_username(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            register_user(self.db, "ab", "a-long-and-unique-password", "retail", "Short Name")
        self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
