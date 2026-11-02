import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import ExpiredSignatureError

from app.auth import bearer, get_current_user
from app.models import AppUser


class _Query:
    def __init__(self, account: SimpleNamespace | None) -> None:
        self.account = account

    def filter(self, *_args: object) -> "_Query":
        return self

    def one_or_none(self) -> SimpleNamespace | None:
        return self.account

    def first(self) -> SimpleNamespace | None:
        return self.account


class _Database:
    def __init__(self, account: SimpleNamespace | None) -> None:
        self.account = account

    def query(self, model: object, *_args: object) -> _Query:
        # Only the account lookup finds a row; there are no domain grants or tenants.
        return _Query(self.account if model is AppUser else None)


class CurrentAccountRoleTests(unittest.TestCase):
    def test_missing_token_is_unauthorized(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            get_current_user(None, _Database(None))
        self.assertEqual(raised.exception.status_code, 401)

    def test_expired_token_is_unauthorized(self) -> None:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="expired-token")
        with patch("app.auth.jwt.decode", side_effect=ExpiredSignatureError("expired")):
            with self.assertRaises(HTTPException) as raised:
                get_current_user(credentials, _Database(None))
        self.assertEqual(raised.exception.status_code, 401)

    def test_role_claim_cannot_grant_admin_access_to_an_analyst_account(self) -> None:
        account = SimpleNamespace(username="analyst", domain="banking")
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="signed-token")
        with patch("app.auth.jwt.decode", return_value={
            "sub": "analyst",
            "domain": "banking",
            "role": "admin",
        }):
            user = get_current_user(credentials, _Database(account))
        self.assertEqual(user.role, "analyst")

    def test_removed_domain_access_is_rejected_for_an_existing_token(self) -> None:
        account = SimpleNamespace(username="analyst", domain="retail")
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="signed-token")
        with patch("app.auth.jwt.decode", return_value={
            "sub": "analyst",
            "domain": "banking",
            "role": "analyst",
        }):
            with self.assertRaises(HTTPException) as raised:
                get_current_user(credentials, _Database(account))
        self.assertEqual(raised.exception.status_code, 403)


class CookieSessionCredentialTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_only_session_cookie_is_accepted_as_bearer_fallback(self) -> None:
        from starlette.requests import Request

        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/",
                "raw_path": b"/",
                "query_string": b"",
                "headers": [(b"cookie", b"ryze_session=oauth-token")],
                "server": ("localhost", 80),
                "client": ("localhost", 80),
                "root_path": "",
            }
        )
        credentials = await bearer(request)
        self.assertEqual(credentials.credentials, "oauth-token")


if __name__ == "__main__":
    unittest.main()
