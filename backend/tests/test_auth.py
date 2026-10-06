import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import get_current_user


class _Query:
    def __init__(self, account: SimpleNamespace | None) -> None:
        self.account = account

    def filter(self, *_args: object) -> "_Query":
        return self

    def one_or_none(self) -> SimpleNamespace | None:
        return self.account


class _Database:
    def __init__(self, account: SimpleNamespace | None) -> None:
        self.account = account

    def query(self, *_args: object) -> _Query:
        return _Query(self.account)


class CurrentAccountRoleTests(unittest.TestCase):
    def test_role_claim_cannot_grant_admin_access_to_an_analyst_account(self) -> None:
        account = SimpleNamespace(domain="banking")
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="signed-token")
        with patch("app.auth.jwt.decode", return_value={
            "sub": "analyst",
            "domain": "banking",
            "role": "admin",
        }):
            user = get_current_user(credentials, _Database(account))
        self.assertEqual(user.role, "analyst")

    def test_removed_domain_access_is_rejected_for_an_existing_token(self) -> None:
        account = SimpleNamespace(domain="retail")
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="signed-token")
        with patch("app.auth.jwt.decode", return_value={
            "sub": "analyst",
            "domain": "banking",
            "role": "analyst",
        }):
            with self.assertRaises(HTTPException) as raised:
                get_current_user(credentials, _Database(account))
        self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
