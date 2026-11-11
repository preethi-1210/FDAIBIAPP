"""Deployment safety. ENVIRONMENT, not DEMO_MODE, decides which shortcuts are allowed.

Startup refuses to run in staging or production while any shortcut that only suits a developer's
machine is active, and lists every problem at once. In development it refuses addresses that are
not on this machine, unless ALLOW_INSECURE_DEVELOPMENT is set, which is logged on every start.
Messages name settings only, never their values.
"""

import logging
from urllib.parse import urlsplit

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .config import settings

logger = logging.getLogger(__name__)

ENVIRONMENTS = ("development", "staging", "production")
DEFAULT_JWT_SECRET = "ryze-dev-secret-change-me"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UnsafeConfigurationError(RuntimeError):
    pass


def environment() -> str:
    return settings.environment.strip().lower()


def is_development() -> bool:
    return environment() == "development"


def allows_http_urls() -> bool:
    """Plain HTTP sign-in addresses are allowed in development only, and still only on loopback."""
    return is_development()


def cookie_secure() -> bool:
    """Every cookie is Secure, except in development when the frontend itself is served over HTTP."""
    if not is_development():
        return True
    return urlsplit(settings.frontend_url).scheme == "https"


def _origins() -> list[str]:
    return [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]


def _is_loopback(url: str) -> bool:
    return urlsplit(url).hostname in _LOOPBACK_HOSTS


def _weak_jwt_secret() -> bool:
    return settings.jwt_secret == DEFAULT_JWT_SECRET or len(settings.jwt_secret) < 32


def configuration_problems() -> list[str]:
    """Problems found in settings alone. Empty means startup may continue."""
    name = environment()
    if name not in ENVIRONMENTS:
        return [f"ENVIRONMENT must be one of {', '.join(ENVIRONMENTS)}."]
    problems: list[str] = []
    if name == "development":
        if not settings.demo_mode and _weak_jwt_secret():
            problems.append("Set a unique JWT_SECRET with at least 32 characters before disabling DEMO_MODE.")
        return problems

    if settings.demo_mode:
        problems.append("DEMO_MODE must be false: it seeds accounts with published passwords and shows them at sign-in.")
    if _weak_jwt_secret():
        problems.append("JWT_SECRET must be a unique value of at least 32 characters.")
    if urlsplit(settings.frontend_url).scheme != "https":
        problems.append("FRONTEND_URL must use https.")
    if settings.oidc_redirect_uri and urlsplit(settings.oidc_redirect_uri).scheme != "https":
        problems.append("OIDC_REDIRECT_URI must use https.")
    origins = _origins()
    if any(origin == "*" for origin in origins):
        problems.append("CORS_ORIGINS must list origins, not '*'.")
    elif any(urlsplit(origin).scheme != "https" for origin in origins):
        problems.append("Every CORS_ORIGINS entry must use https.")
    if settings.payment_gateway.strip().lower() == "mock":
        problems.append("PAYMENT_GATEWAY=mock moves no money and is for development only.")
    if settings.database_url.startswith("sqlite"):
        problems.append("DATABASE_URL must point to PostgreSQL, not SQLite.")
    return problems


def development_exposure() -> list[str]:
    """Development settings that send sign-in or browser traffic beyond this machine."""
    if not is_development():
        return []
    exposed = []
    if not _is_loopback(settings.frontend_url):
        exposed.append("FRONTEND_URL")
    if settings.oidc_redirect_uri and not _is_loopback(settings.oidc_redirect_uri):
        exposed.append("OIDC_REDIRECT_URI")
    if any(origin == "*" or not _is_loopback(origin) for origin in _origins()):
        exposed.append("CORS_ORIGINS")
    return exposed


def database_problems(engine: Engine) -> list[str]:
    """Checks that need the database. Staging and production only; read-only queries."""
    if is_development() or environment() not in ENVIRONMENTS:
        return []
    from .domains import DEMO_USERS
    from .security import verify_password

    problems: list[str] = []
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            superuser = connection.execute(
                text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            ).scalar()
            if superuser:
                problems.append("DATABASE_URL connects as a PostgreSQL superuser; use the restricted application role.")
        query = text("SELECT username, password_hash FROM app_users WHERE username IN :names").bindparams(
            bindparam("names", expanding=True)
        )
        try:
            stored = dict(connection.execute(query, {"names": [user["username"] for user in DEMO_USERS]}).all())
        except SQLAlchemyError:
            # No accounts table yet means no account can hold a published password.
            stored = {}
    published = sorted(
        user["username"]
        for user in DEMO_USERS
        if stored.get(user["username"]) and verify_password(user["password"], stored[user["username"]])
    )
    if published:
        problems.append(f"These accounts still use their published demo password: {', '.join(published)}.")
    return problems


def enforce_startup_policy(engine: Engine) -> None:
    """Raise UnsafeConfigurationError listing every problem, or return if startup may continue."""
    problems = configuration_problems()
    if not problems:
        exposed = development_exposure()
        if exposed and settings.allow_insecure_development:
            logger.warning(
                "ALLOW_INSECURE_DEVELOPMENT is set: %s point beyond this machine while ENVIRONMENT=development, "
                "so cookies and sign-in redirects are not secured. Never use this setting for real users.",
                ", ".join(exposed),
            )
        elif exposed:
            problems.append(
                f"ENVIRONMENT=development, but {', '.join(exposed)} point beyond this machine. Set ENVIRONMENT to "
                "staging or production, or set ALLOW_INSECURE_DEVELOPMENT=true for a trusted local network demo."
            )
    if not problems:
        problems = database_problems(engine)
    if problems:
        raise UnsafeConfigurationError(
            f"Refusing to start with ENVIRONMENT={environment() or '(empty)'}:\n- " + "\n- ".join(problems)
        )
