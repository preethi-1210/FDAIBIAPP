import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from authlib.integrations.base_client.errors import OAuthError
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import main
from app.auth import (
    ONBOARDING_COOKIE,
    ONBOARDING_COOKIE_PATH,
    create_access_token,
    create_onboarding_token,
    get_current_user,
    read_onboarding_token,
    register_oidc_onboarding_user,
    register_oidc_user,
)
from app.models import AppUser, Base, OAuthIdentity, UserDomainTenant
from app.schemas import OidcOnboardingRequest


class _Query:
    def __init__(self, result: object) -> None:
        self.result = result

    def filter(self, *_args: object) -> "_Query":
        return self

    def one_or_none(self) -> object:
        return self.result


class _OidcClient:
    def __init__(self, claims: dict | None = None, error: Exception | None = None) -> None:
        self.claims = claims
        self.error = error

    async def authorize_access_token(self, _request: object) -> dict:
        if self.error:
            raise self.error
        return {"userinfo": self.claims}


class OidcTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_configuration_is_an_explicit_service_error(self) -> None:
        with (
            patch.object(main.settings, "oidc_client_id", ""),
            patch.object(main.settings, "oidc_client_secret", ""),
            patch.object(main.settings, "oidc_redirect_uri", ""),
        ):
            with self.assertRaises(HTTPException) as raised:
                main._oidc_client()
        self.assertEqual(raised.exception.status_code, 503)

    def test_google_homepage_discovery_url_is_normalized(self) -> None:
        with (
            patch.object(main.settings, "oidc_client_id", "client"),
            patch.object(main.settings, "oidc_client_secret", "secret"),
            patch.object(main.settings, "oidc_redirect_uri", "http://localhost:8013/auth/oidc/callback"),
            patch.object(main.settings, "oidc_discovery_url", "https://accounts.google.com"),
            patch.object(main.settings, "demo_mode", True),
            patch("app.main.OAuth") as oauth,
        ):
            main._oidc_client()

        oauth.return_value.register.assert_called_once()
        self.assertEqual(
            oauth.return_value.register.call_args.kwargs["server_metadata_url"],
            "https://accounts.google.com/.well-known/openid-configuration",
        )

    async def test_callback_signs_in_only_a_prelinked_verified_account(self) -> None:
        identity = SimpleNamespace(username="ret.user")
        account = SimpleNamespace(username="ret.user", domain="retail")
        request = SimpleNamespace(session={"oidc_nonce": "nonce", "oidc_domain": "banking"})
        db = Mock()
        db.query.side_effect = [_Query(identity), _Query(account)]
        claims = {
            "iss": "https://accounts.google.com",
            "sub": "provider-subject",
            "email": "retail@example.org",
            "email_verified": True,
        }
        with (
            patch("app.main._oidc_client", return_value=_OidcClient(claims)),
            patch("app.main.create_access_token", return_value="signed-app-token"),
            patch.object(main.settings, "frontend_url", "http://localhost:3000"),
            patch.object(main.settings, "demo_mode", True),
        ):
            response = await main.oidc_callback(request, db)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "http://localhost:3000/dashboard")
        self.assertIn("ryze_session=signed-app-token", response.headers["set-cookie"])
        self.assertEqual(db.query.call_args_list[0].args[0], OAuthIdentity)
        self.assertEqual(db.query.call_args_list[1].args[0], AppUser)

    async def test_callback_sends_an_unmapped_identity_to_domain_selection_without_a_session(self) -> None:
        request = SimpleNamespace(session={"oidc_nonce": "nonce", "oidc_domain": "retail"})
        db = Mock()
        db.query.return_value = _Query(None)
        claims = {
            "iss": "https://accounts.google.com",
            "sub": "unknown-subject",
            "email": "unknown@example.org",
            "email_verified": True,
            "name": "New User",
        }
        with (
            patch("app.main._oidc_client", return_value=_OidcClient(claims)),
            patch.object(main.settings, "frontend_url", "http://localhost:3000"),
            patch.object(main.settings, "demo_mode", True),
        ):
            response = await main.oidc_callback(request, db)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "http://localhost:3000/?onboarding=select")
        cookies = response.headers.getlist("set-cookie")
        onboarding = next(c for c in cookies if c.startswith(f"{ONBOARDING_COOKIE}="))
        self.assertIn("HttpOnly", onboarding)
        self.assertIn(f"Path={ONBOARDING_COOKIE_PATH}", onboarding)
        self.assertFalse(any(c.startswith("ryze_session=") and "Max-Age=0" not in c for c in cookies))
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_onboarding_token_cannot_be_used_as_an_application_session(self) -> None:
        token = create_onboarding_token("https://accounts.google.com", "subject", "new@example.org", "New")
        with self.assertRaises(HTTPException) as raised:
            get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), Mock())
        self.assertEqual(raised.exception.status_code, 401)
        session_token = create_access_token("ret.user", "retail", "analyst")
        with self.assertRaises(HTTPException):
            read_onboarding_token(session_token)

    def _onboarding_request(self, token: str | None, origin: str = "http://localhost:3000") -> SimpleNamespace:
        return SimpleNamespace(
            cookies={ONBOARDING_COOKIE: token} if token else {},
            headers={"origin": origin},
        )

    def _session_db(self) -> tuple[object, Session]:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return engine, Session(engine)

    def test_onboarding_saves_the_selected_domain_and_signs_in_as_analyst(self) -> None:
        engine, db = self._session_db()
        try:
            db.add(AppUser(username="new.user", password_hash="x", domain="retail", display_name="Taken"))
            db.commit()
            token = create_onboarding_token("https://accounts.google.com", "subject-1", "New.User@example.org", "New User")
            with patch.object(main.settings, "demo_mode", True):
                response = main.oidc_onboarding_complete(
                    OidcOnboardingRequest(domain="insurance"), self._onboarding_request(token), db
                )
            account = db.query(AppUser).join(
                OAuthIdentity, OAuthIdentity.username == AppUser.username
            ).filter(OAuthIdentity.subject == "subject-1").one()
            self.assertEqual(account.domain, "insurance")
            self.assertTrue(account.username.startswith("new.user-"))
            self.assertEqual(account.display_name, "New User")
            cookies = response.headers.getlist("set-cookie")
            session_cookie = next(c for c in cookies if c.startswith("ryze_session="))
            session_token = session_cookie.split(";", 1)[0].split("=", 1)[1]
            user = get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=session_token), db)
            self.assertEqual((user.username, user.domain, user.role), (account.username, "insurance", "analyst"))
            self.assertTrue(any(c.startswith(f"{ONBOARDING_COOKIE}=") and "Max-Age=0" in c for c in cookies))

            with self.assertRaises(HTTPException) as replay:
                main.oidc_onboarding_complete(
                    OidcOnboardingRequest(domain="banking"), self._onboarding_request(token), db
                )
            self.assertEqual(replay.exception.status_code, 409)
            db.refresh(account)
            self.assertEqual(account.domain, "insurance")
        finally:
            db.close()
            engine.dispose()

    def test_onboarding_cannot_select_admin_or_the_all_domain(self) -> None:
        with self.assertRaises(ValidationError):
            OidcOnboardingRequest(domain="all")
        with self.assertRaises(ValidationError):
            OidcOnboardingRequest(domain="admin")
        engine, db = self._session_db()
        try:
            with self.assertRaises(HTTPException) as raised:
                register_oidc_onboarding_user(
                    db, "https://accounts.google.com", "subject-2", "x@example.org", "X", "all"
                )
            self.assertEqual(raised.exception.status_code, 422)
            self.assertEqual(db.query(AppUser).count(), 0)
        finally:
            db.close()
            engine.dispose()

    def test_onboarding_healthcare_is_saved_but_waits_for_tenant_provisioning(self) -> None:
        engine, db = self._session_db()
        try:
            token = create_onboarding_token("https://accounts.google.com", "subject-3", "hc.analyst@example.org", "HC")
            with patch.object(main.settings, "demo_mode", True):
                response = main.oidc_onboarding_complete(
                    OidcOnboardingRequest(domain="healthcare"), self._onboarding_request(token), db
                )
            self.assertEqual(db.query(AppUser).filter(AppUser.username == "hc.analyst").one().domain, "healthcare")
            self.assertEqual(db.query(UserDomainTenant).count(), 0)
            cookies = response.headers.getlist("set-cookie")
            self.assertFalse(any(c.startswith("ryze_session=") and "Max-Age=0" not in c for c in cookies))
        finally:
            db.close()
            engine.dispose()

    async def test_callback_routes_an_unprovisioned_healthcare_account_to_the_pending_page(self) -> None:
        identity = SimpleNamespace(username="hc")
        account = SimpleNamespace(username="hc", domain="healthcare")
        request = SimpleNamespace(session={"oidc_nonce": "nonce", "oidc_domain": None})
        db = Mock()
        db.query.side_effect = [_Query(identity), _Query(account), _Query(None)]
        claims = {
            "iss": "https://accounts.google.com",
            "sub": "subject-3",
            "email": "hc@example.org",
            "email_verified": True,
        }
        with (
            patch("app.main._oidc_client", return_value=_OidcClient(claims)),
            patch.object(main.settings, "frontend_url", "http://localhost:3000"),
        ):
            response = await main.oidc_callback(request, db)
        self.assertEqual(response.headers["location"], "http://localhost:3000/?onboarding=healthcare_pending")
        cookies = response.headers.getlist("set-cookie")
        self.assertFalse(any(c.startswith("ryze_session=") and "Max-Age=0" not in c for c in cookies))

    def test_onboarding_rejects_foreign_origins_and_missing_tokens(self) -> None:
        token = create_onboarding_token("https://accounts.google.com", "subject-4", "o@example.org", "O")
        db = Mock()
        with self.assertRaises(HTTPException) as foreign:
            main.oidc_onboarding_complete(
                OidcOnboardingRequest(domain="retail"), self._onboarding_request(token, "https://evil.example"), db
            )
        self.assertEqual(foreign.exception.status_code, 403)
        with self.assertRaises(HTTPException) as missing:
            main.oidc_onboarding_complete(OidcOnboardingRequest(domain="retail"), self._onboarding_request(None), db)
        self.assertEqual(missing.exception.status_code, 401)
        db.add.assert_not_called()

    async def test_google_signup_creates_a_new_domain_scoped_analyst(self) -> None:
        request = SimpleNamespace(session={
            "oidc_nonce": "nonce",
            "oidc_domain": "banking",
            "oidc_intent": "signup",
            "oidc_username": "preethi_12345",
        })
        db = Mock()
        db.query.return_value = _Query(None)
        claims = {
            "iss": "https://accounts.google.com",
            "sub": "provider-subject",
            "email": "verified@example.org",
            "email_verified": True,
            "name": "Verified User",
        }
        account = SimpleNamespace(username="preethi_12345", domain="banking")
        with (
            patch("app.main._oidc_client", return_value=_OidcClient(claims)),
            patch("app.main.register_oidc_user", return_value=account) as register,
            patch("app.main.create_access_token", return_value="signed-app-token"),
            patch.object(main.settings, "frontend_url", "http://localhost:3000"),
            patch.object(main.settings, "demo_mode", True),
        ):
            response = await main.oidc_callback(request, db)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "http://localhost:3000/dashboard")
        self.assertIn("ryze_session=signed-app-token", response.headers["set-cookie"])
        register.assert_called_once_with(
            db,
            "preethi_12345",
            "banking",
            "Verified User",
            "https://accounts.google.com",
            "provider-subject",
        )

    def test_oidc_signup_persists_an_analyst_identity_and_account_together(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                account = register_oidc_user(
                    db,
                    "preethi_12345",
                    "banking",
                    "Verified User",
                    "https://accounts.google.com",
                    "provider-subject",
                )
                identity = db.query(OAuthIdentity).filter(
                    OAuthIdentity.issuer == "https://accounts.google.com",
                    OAuthIdentity.subject == "provider-subject",
                ).one()
                account_username = account.username
                account_domain = account.domain
                identity_username = identity.username

            self.assertEqual(account_username, "preethi_12345")
            self.assertEqual(account_domain, "banking")
            self.assertEqual(identity_username, "preethi_12345")
        finally:
            engine.dispose()

    def test_oidc_signup_does_not_link_a_google_identity_to_an_existing_username(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        try:
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                existing = AppUser(
                    username="preethi_12345",
                    password_hash="existing-password-hash",
                    domain="banking",
                    display_name="Existing User",
                )
                db.add(existing)
                db.commit()
                with self.assertRaises(HTTPException) as raised:
                    register_oidc_user(
                        db,
                        "preethi_12345",
                        "banking",
                        "Google User",
                        "https://accounts.google.com",
                        "provider-subject",
                    )
                identity_count = db.query(OAuthIdentity).count()
                stored_name = db.query(AppUser).filter(AppUser.username == "preethi_12345").one().display_name

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(identity_count, 0)
            self.assertEqual(stored_name, "Existing User")
        finally:
            engine.dispose()

    async def test_oidc_signup_cannot_grant_admin_or_healthcare_access(self) -> None:
        for domain in ("all", "healthcare"):
            with self.subTest(domain=domain):
                with patch("app.main._oidc_client", return_value=object()):
                    with self.assertRaises(HTTPException) as raised:
                        await main.oidc_login(
                            SimpleNamespace(session={}),
                            domain=domain,
                            intent="signup",
                            username="preethi_12345",
                        )
                self.assertEqual(raised.exception.status_code, 422)

    async def test_healthcare_oidc_claim_can_only_confirm_the_provisioned_tenant(self) -> None:
        identity = SimpleNamespace(username="health.user")
        account = SimpleNamespace(username="health.user", domain="healthcare")
        assignment = SimpleNamespace(tenant_id="tenant-a")
        request = SimpleNamespace(session={"oidc_nonce": "nonce", "oidc_domain": None})
        claims = {
            "iss": "https://accounts.google.com",
            "sub": "health-subject",
            "email": "health@example.org",
            "email_verified": True,
            "tenant_id": "tenant-a",
        }
        db = Mock()
        db.query.side_effect = [_Query(identity), _Query(account), _Query(assignment)]
        with (
            patch("app.main._oidc_client", return_value=_OidcClient(claims)),
            patch("app.main.create_access_token", return_value="signed-app-token"),
            patch.object(main.settings, "oidc_tenant_claim", "tenant_id"),
            patch.object(main.settings, "frontend_url", "http://localhost:3000"),
            patch.object(main.settings, "demo_mode", True),
        ):
            response = await main.oidc_callback(request, db)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(db.query.call_args_list[2].args[0], UserDomainTenant)

        db = Mock()
        db.query.side_effect = [_Query(identity), _Query(account), _Query(assignment)]
        request = SimpleNamespace(session={"oidc_nonce": "nonce", "oidc_domain": None})
        claims["tenant_id"] = "tenant-b"
        with (
            patch("app.main._oidc_client", return_value=_OidcClient(claims)),
            patch.object(main.settings, "oidc_tenant_claim", "tenant_id"),
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.oidc_callback(request, db)
        self.assertEqual(raised.exception.status_code, 403)

    async def test_callback_rejects_provider_failures_and_missing_state(self) -> None:
        request = SimpleNamespace(session={"oidc_nonce": "nonce", "oidc_domain": None})
        with patch(
            "app.main._oidc_client",
            return_value=_OidcClient(error=OAuthError(error="invalid_grant")),
        ):
            with self.assertRaises(HTTPException) as failed:
                await main.oidc_callback(request, Mock())
        self.assertEqual(failed.exception.status_code, 401)

        request = SimpleNamespace(session={})
        with patch("app.main._oidc_client", return_value=_OidcClient()):
            with self.assertRaises(HTTPException) as expired:
                await main.oidc_callback(request, Mock())
        self.assertEqual(expired.exception.status_code, 401)

    async def test_a_blocked_identity_provider_returns_to_sign_in_instead_of_a_server_error(self) -> None:
        # Seen behind a security proxy: Authlib's discovery request (made with httpx2) gets 403.
        import httpx2

        request_url = "https://accounts.google.com/.well-known/openid-configuration"
        blocked = httpx2.HTTPStatusError(
            "Client error '403 Forbidden'",
            request=httpx2.Request("GET", request_url),
            response=httpx2.Response(403, request=httpx2.Request("GET", request_url)),
        )

        class BlockedClient:
            async def authorize_redirect(self, *_args, **_kwargs):
                raise blocked

        request = SimpleNamespace(session={})
        with (
            patch("app.main._oidc_client", return_value=BlockedClient()),
            patch.object(main.settings, "frontend_url", "http://localhost:3000"),
        ):
            response = await main.oidc_login(request, domain="retail")

        self.assertEqual(response.status_code, 303)
        location = response.headers["location"]
        self.assertTrue(location.startswith("http://localhost:3000/?login=1&sso_error="))
        self.assertIn("HTTP+403", location)
        self.assertEqual(request.session, {})

    def test_oidc_rejects_insecure_production_redirects(self) -> None:
        with (
            patch.object(main.settings, "oidc_client_id", "client"),
            patch.object(main.settings, "oidc_client_secret", "secret"),
            patch.object(main.settings, "oidc_redirect_uri", "http://example.com/auth/oidc/callback"),
            patch.object(main.settings, "demo_mode", False),
        ):
            with self.assertRaises(HTTPException) as raised:
                main._oidc_client()
        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
