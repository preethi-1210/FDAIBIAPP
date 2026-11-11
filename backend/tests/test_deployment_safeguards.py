"""ENVIRONMENT decides what is allowed; DEMO_MODE can no longer switch security off in production."""

import unittest
from contextlib import ExitStack
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import deployment, main
from app.auth import create_access_token, hash_password
from app.config import settings
from app.db import Base, get_db
from app.domains import DEMO_USERS
from app.models import AppUser

STRONG_SECRET = "x" * 48
SAFE_PRODUCTION = {
    "environment": "production",
    "demo_mode": False,
    "jwt_secret": STRONG_SECRET,
    "frontend_url": "https://bi.example.com",
    "oidc_redirect_uri": "https://api.example.com/auth/oidc/callback",
    "cors_origins": "https://bi.example.com",
    "payment_gateway": "",
    "database_url": "postgresql+psycopg://app@db.example.com/ryze",
    "allow_insecure_development": False,
}
LOCAL_DEVELOPMENT = {
    "environment": "development",
    "demo_mode": True,
    "jwt_secret": STRONG_SECRET,
    "frontend_url": "http://localhost:3000",
    "oidc_redirect_uri": "http://localhost:8000/auth/oidc/callback",
    "cors_origins": "http://localhost:3000,http://127.0.0.1:3000",
    "payment_gateway": "",
    "database_url": "sqlite:///./ryze_bi.db",
    "allow_insecure_development": False,
}


def configured(values: dict, **overrides):
    stack = ExitStack()
    for name, value in {**values, **overrides}.items():
        stack.enter_context(patch.object(settings, name, value))
    return stack


def sqlite_engine():
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


class ConfigurationTests(unittest.TestCase):
    def test_demo_mode_is_off_unless_configured(self) -> None:
        self.assertFalse(type(settings).model_fields["demo_mode"].default)
        self.assertEqual(type(settings).model_fields["environment"].default, "development")

    def test_safe_production_settings_pass(self) -> None:
        with configured(SAFE_PRODUCTION):
            self.assertEqual(deployment.configuration_problems(), [])

    def test_production_lists_every_problem_at_once(self) -> None:
        unsafe = {
            "demo_mode": True,
            "jwt_secret": deployment.DEFAULT_JWT_SECRET,
            "frontend_url": "http://bi.example.com",
            "oidc_redirect_uri": "http://api.example.com/auth/oidc/callback",
            "cors_origins": "http://bi.example.com",
            "payment_gateway": "mock",
            "database_url": "sqlite:///./ryze_bi.db",
        }
        for environment in ("staging", "production"):
            with self.subTest(environment=environment), configured(SAFE_PRODUCTION, environment=environment, **unsafe):
                problems = "\n".join(deployment.configuration_problems())
                for setting in ("DEMO_MODE", "JWT_SECRET", "FRONTEND_URL", "OIDC_REDIRECT_URI", "CORS_ORIGINS", "PAYMENT_GATEWAY", "DATABASE_URL"):
                    self.assertIn(setting, problems)

    def test_each_production_problem_is_caught_on_its_own(self) -> None:
        for name, value, setting in (
            ("demo_mode", True, "DEMO_MODE"),
            ("jwt_secret", "short-secret", "JWT_SECRET"),
            ("frontend_url", "http://bi.example.com", "FRONTEND_URL"),
            ("oidc_redirect_uri", "http://api.example.com/auth/oidc/callback", "OIDC_REDIRECT_URI"),
            ("cors_origins", "*", "CORS_ORIGINS"),
            ("cors_origins", "https://bi.example.com,http://other.example.com", "CORS_ORIGINS"),
            ("payment_gateway", "Mock", "PAYMENT_GATEWAY"),
            ("database_url", "sqlite:///./x.db", "DATABASE_URL"),
        ):
            with self.subTest(name=name, value=value), configured(SAFE_PRODUCTION, **{name: value}):
                problems = deployment.configuration_problems()
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(setting, problems[0])

    def test_an_unknown_environment_is_refused(self) -> None:
        with configured(LOCAL_DEVELOPMENT, environment="prod"):
            self.assertIn("ENVIRONMENT", deployment.configuration_problems()[0])

    def test_local_development_is_unchanged(self) -> None:
        with configured(LOCAL_DEVELOPMENT):
            self.assertEqual(deployment.configuration_problems(), [])
            self.assertEqual(deployment.development_exposure(), [])
            deployment.enforce_startup_policy(sqlite_engine())

    def test_development_without_demo_mode_still_needs_a_strong_secret(self) -> None:
        with configured(LOCAL_DEVELOPMENT, demo_mode=False, jwt_secret=deployment.DEFAULT_JWT_SECRET):
            self.assertIn("JWT_SECRET", deployment.configuration_problems()[0])

    def test_development_settings_that_reach_other_machines_are_refused(self) -> None:
        for name, value in (
            ("frontend_url", "http://192.168.1.20:3000"),
            ("oidc_redirect_uri", "https://bi.example.com/auth/oidc/callback"),
            ("cors_origins", "http://localhost:3000,https://bi.example.com"),
            ("cors_origins", "*"),
        ):
            with self.subTest(name=name), configured(LOCAL_DEVELOPMENT, **{name: value}):
                with self.assertRaises(deployment.UnsafeConfigurationError) as raised:
                    deployment.enforce_startup_policy(sqlite_engine())
                self.assertIn(name.upper(), str(raised.exception))
                self.assertIn("ALLOW_INSECURE_DEVELOPMENT", str(raised.exception))

    def test_the_development_override_is_logged_on_every_start(self) -> None:
        with configured(LOCAL_DEVELOPMENT, frontend_url="http://192.168.1.20:3000", allow_insecure_development=True):
            with self.assertLogs("app.deployment", level="WARNING") as logs:
                deployment.enforce_startup_policy(sqlite_engine())
        self.assertIn("FRONTEND_URL", logs.output[0])

    def test_the_override_has_no_effect_outside_development(self) -> None:
        with configured(SAFE_PRODUCTION, allow_insecure_development=True, demo_mode=True):
            with self.assertRaises(deployment.UnsafeConfigurationError):
                deployment.enforce_startup_policy(sqlite_engine())

    def test_refusal_names_settings_never_values(self) -> None:
        with configured(SAFE_PRODUCTION, jwt_secret="tiny-but-secret-value", frontend_url="http://secret-host.example.com"):
            with self.assertRaises(deployment.UnsafeConfigurationError) as raised:
                deployment.enforce_startup_policy(sqlite_engine())
        message = str(raised.exception)
        self.assertNotIn("tiny-but-secret-value", message)
        self.assertNotIn("secret-host", message)


class DatabaseCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = sqlite_engine()
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)

    def _add(self, username: str, password: str) -> None:
        with sessionmaker(bind=self.engine)() as db:
            db.add(AppUser(username=username, password_hash=hash_password(password), domain="all", display_name=username))
            db.commit()

    def test_an_account_on_its_published_password_blocks_production(self) -> None:
        admin = next(user for user in DEMO_USERS if user["username"] == "admin")
        self._add("admin", admin["password"])
        with configured(SAFE_PRODUCTION):
            problems = deployment.database_problems(self.engine)
            with self.assertRaises(deployment.UnsafeConfigurationError) as raised:
                deployment.enforce_startup_policy(self.engine)
        self.assertEqual(len(problems), 1)
        self.assertIn("admin", problems[0])
        self.assertNotIn(admin["password"], str(raised.exception))

    def test_changed_passwords_pass(self) -> None:
        self._add("admin", "a-new-password-nobody-published")
        with configured(SAFE_PRODUCTION):
            self.assertEqual(deployment.database_problems(self.engine), [])

    def test_a_missing_accounts_table_is_not_an_error(self) -> None:
        with configured(SAFE_PRODUCTION):
            self.assertEqual(deployment.database_problems(sqlite_engine()), [])

    def test_development_does_not_check_the_database(self) -> None:
        admin = next(user for user in DEMO_USERS if user["username"] == "admin")
        self._add("admin", admin["password"])
        with configured(LOCAL_DEVELOPMENT):
            self.assertEqual(deployment.database_problems(self.engine), [])

    def test_a_postgresql_superuser_blocks_production(self) -> None:
        class Result:
            def __init__(self, value):
                self.value = value

            def scalar(self):
                return self.value

            def all(self):
                return []

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, statement, *args):
                return Result(True if "rolsuper" in str(statement) else None)

        class Engine:
            class dialect:
                name = "postgresql"

            def connect(self):
                return Connection()

        with configured(SAFE_PRODUCTION):
            problems = deployment.database_problems(Engine())
        self.assertEqual(len(problems), 1)
        self.assertIn("superuser", problems[0])


class CookieTests(unittest.TestCase):
    def test_cookie_security_follows_environment_not_demo_mode(self) -> None:
        cases = (
            (LOCAL_DEVELOPMENT, {}, False),
            (LOCAL_DEVELOPMENT, {"frontend_url": "https://localhost:3000"}, True),
            (LOCAL_DEVELOPMENT, {"demo_mode": False}, False),
            (SAFE_PRODUCTION, {}, True),
            # Even if production were started with DEMO_MODE on, cookies stay Secure.
            (SAFE_PRODUCTION, {"demo_mode": True, "frontend_url": "http://bi.example.com"}, True),
            (SAFE_PRODUCTION, {"environment": "staging"}, True),
        )
        for base, overrides, expected in cases:
            with self.subTest(overrides=overrides, environment=base["environment"]), configured(base, **overrides):
                self.assertIs(deployment.cookie_secure(), expected)

    def test_session_cookie_from_domain_switch_is_secure_in_production(self) -> None:
        engine = sqlite_engine()
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as db:
            db.add(AppUser(username="admin", password_hash=hash_password("pw-123456"), domain="all", display_name="Admin"))
            db.commit()

        def override_db():
            with Session() as db:
                yield db

        main.fastapi_app.dependency_overrides[get_db] = override_db
        self.addCleanup(main.fastapi_app.dependency_overrides.clear)
        client = TestClient(main.fastapi_app)
        for environment, secure in (("production", True), ("development", False)):
            base = SAFE_PRODUCTION if environment == "production" else LOCAL_DEVELOPMENT
            with self.subTest(environment=environment), configured(base, demo_mode=True):
                headers = {"Authorization": f"Bearer {create_access_token('admin', 'banking', 'admin')}"}
                response = client.post("/auth/switch-domain", json={"domain": "retail"}, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                cookie = response.headers["set-cookie"].lower()
                self.assertIn("httponly", cookie)
                self.assertEqual("secure" in cookie.replace("samesite", ""), secure)

    def test_plain_http_oidc_redirect_is_refused_outside_development_even_on_loopback(self) -> None:
        with configured(
            SAFE_PRODUCTION,
            oidc_client_id="client",
            oidc_client_secret="secret",
            oidc_redirect_uri="http://localhost:8000/auth/oidc/callback",
            demo_mode=True,
        ):
            with self.assertRaises(HTTPException) as raised:
                main._oidc_client()
        self.assertEqual(raised.exception.status_code, 503)


class StartupOrderTests(unittest.TestCase):
    def test_a_refused_configuration_changes_no_schema_or_data(self) -> None:
        with (
            configured(SAFE_PRODUCTION, demo_mode=True),
            patch("app.main.ensure_schema") as ensure_schema,
            patch("app.main.seed_if_empty") as seed,
        ):
            with self.assertRaises(deployment.UnsafeConfigurationError):
                main.startup()
        ensure_schema.assert_not_called()
        seed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
