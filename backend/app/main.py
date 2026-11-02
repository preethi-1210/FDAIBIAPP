import logging
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import httpx

try:  # Authlib's HTTP client may use httpx2, whose errors are separate classes.
    import httpx2

    _HTTP_ERRORS: tuple[type[Exception], ...] = (httpx.HTTPError, httpx2.HTTPError)
except ImportError:
    _HTTP_ERRORS = (httpx.HTTPError,)
from authlib.integrations.starlette_client import OAuth
from authlib.common.errors import AuthlibBaseError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from joserfc.errors import JoseError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    ONBOARDING_COOKIE,
    ONBOARDING_COOKIE_PATH,
    ONBOARDING_TOKEN_MINUTES,
    AuthUser,
    authenticate,
    create_access_token,
    create_onboarding_token,
    get_current_user,
    read_onboarding_token,
    register_oidc_onboarding_user,
    register_oidc_user,
    register_user,
)
from .cache import get_json as cache_get_json
from .cache import health as cache_health
from .cache import LocalTTLCache, scoped_key, set_json as cache_set_json
from .config import settings
from .db import SessionLocal, engine, get_db
from .domains import DEMO_USERS, DOMAINS, DOMAIN_LABELS, PROMPTS
from .llm import provider_status
from .models import (
    AppUser,
    Conversation,
    ConversationMessage,
    Glossary,
    HitlReview,
    OAuthIdentity,
    QueryHistory,
    UserDomainTenant,
)
from .schemas import (
    AccessDecision,
    LoginRequest,
    OidcOnboardingRequest,
    QueryRequest,
    QueryResponse,
    ReportResponse,
    SignupRequest,
    TokenResponse,
)
from .sensitive_data import (
    has_cvv_columns,
    has_restricted_columns,
    is_sensitive_column,
    require_sensitive_request_access,
    requested_sensitive_type,
    sanitize_sensitive_rows,
)
from .seed import apply_postgres_routines, ensure_schema, seed_if_empty
from .services.guardrail import GuardrailError
from .services.orchestrator import Orchestrator
from .services.reports import get_report
from .access_api import router as access_router
from .services.access import domains_for, notification_count
from .warehouse import (
    close_warehouse_sessions,
    databricks_domain_catalog,
    test_warehouse_connection,
    verify_warehouse_domain,
    warehouse_status,
)

def _configure_logging() -> None:
    # Without a handler the app's latency logs never reach the console (Python's fallback
    # handler only prints warnings), so slow requests were invisible.
    app_logger = logging.getLogger("app")
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        app_logger.addHandler(handler)
    app_logger.setLevel(settings.log_level.upper())


_configure_logging()
app = FastAPI(title="RYZE AI/BI Platform", version="0.2.0")
app.include_router(access_router)
allowed_origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]

orchestrator = Orchestrator()
logger = logging.getLogger(__name__)
_dashboard_cache: dict[tuple[str, str, str, str | None], tuple[float, dict]] = {}
_dashboard_cache_lock = Lock()
_report_cache = LocalTTLCache(max_entries=256)
_request_id_pattern = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _get_conversation_context(
    db: Session,
    user: AuthUser,
    conversation_id: UUID,
    title: str,
) -> tuple[Conversation, list[dict[str, str]]]:
    conversation = db.get(Conversation, str(conversation_id))
    if conversation is not None and (
        conversation.username != user.username
        or conversation.domain != user.domain
        or conversation.tenant_id != user.tenant_id
    ):
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation is None:
        conversation = Conversation(
            id=str(conversation_id),
            username=user.username,
            domain=user.domain,
            tenant_id=user.tenant_id,
            title=title[:160],
        )
        db.add(conversation)

    prior_messages = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == str(conversation_id))
        .order_by(ConversationMessage.id.desc())
        .limit(12)
        .all()
    )
    context = [
        {"role": message.role, "content": message.content}
        for message in reversed(prior_messages)
    ]
    return conversation, context


@app.on_event("startup")
def startup() -> None:
    if not settings.demo_mode and (
        settings.jwt_secret == "ryze-dev-secret-change-me" or len(settings.jwt_secret) < 32
    ):
        raise RuntimeError("Set a unique JWT_SECRET with at least 32 characters before disabling DEMO_MODE.")
    ensure_schema()
    db = SessionLocal()
    try:
        if settings.demo_mode:
            seed_if_empty(db)
        apply_postgres_routines(db)
        try:
            orchestrator.warmup(db)
        except GuardrailError as exc:
            if settings.warehouse not in {"databricks", "snowflake"}:
                raise
            logger.warning(
                "API startup continuing without %s schema metadata; authentication remains available, "
                "but analytics requires a working warehouse connection: %s",
                settings.warehouse,
                exc,
            )
    finally:
        db.close()


@app.on_event("shutdown")
def shutdown() -> None:
    close_warehouse_sessions()


@app.middleware("http")
async def request_metrics(request: Request, call_next):
    supplied_id = request.headers.get("x-request-id", "")
    request_id = supplied_id if _request_id_pattern.fullmatch(supplied_id) else str(uuid4())
    started = time.perf_counter()
    try:
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.cookies.get("ryze_session")
            and "authorization" not in request.headers
            and request.headers.get("origin") not in allowed_origins
        ):
            response = JSONResponse({"detail": "Invalid request origin."}, status_code=403)
        else:
            response = await call_next(request)
    except Exception:
        logger.exception(
            "Request failed",
            extra={"request_id": request_id, "http_method": request.method, "http_path": request.url.path},
        )
        raise
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms}"
    log = logger.warning if elapsed_ms >= settings.slow_request_ms else logger.info
    log(
        "%s %s -> %s in %.0f ms%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        " (slow request)" if elapsed_ms >= settings.slow_request_ms else "",
        extra={
            "request_id": request_id,
            "http_method": request.method,
            "http_path": request.url.path,
            "http_status": response.status_code,
            "request_latency_ms": elapsed_ms,
        },
    )
    return response


def _database_health() -> dict[str, str | float | None]:
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        logger.exception("Database health check failed", extra={"database_latency_ms": elapsed})
        return {"status": "unavailable", "latency_ms": elapsed}
    return {"status": "ok", "latency_ms": round((time.perf_counter() - started) * 1000, 2)}


def _oidc_client():
    if not all((settings.oidc_client_id, settings.oidc_client_secret, settings.oidc_redirect_uri)):
        raise HTTPException(
            status_code=503,
            detail="OIDC sign-in is not configured. Set OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, and OIDC_REDIRECT_URI.",
        )
    redirect = urlsplit(settings.oidc_redirect_uri)
    frontend = urlsplit(settings.frontend_url)
    discovery_url = settings.oidc_discovery_url.rstrip("/")
    if discovery_url == "https://accounts.google.com":
        discovery_url = f"{discovery_url}/.well-known/openid-configuration"
    discovery = urlsplit(discovery_url)
    local_hosts = {"localhost", "127.0.0.1"}
    if (
        redirect.scheme not in ({"https", "http"} if settings.demo_mode else {"https"})
        or not redirect.hostname
        or (redirect.scheme == "http" and redirect.hostname not in local_hosts)
        or redirect.username
        or redirect.password
        or redirect.fragment
        or redirect.path != "/auth/oidc/callback"
        or redirect.query
        or discovery.scheme != "https"
        or not discovery.hostname
        or frontend.scheme not in ({"https", "http"} if settings.demo_mode else {"https"})
        or not frontend.hostname
        or (frontend.scheme == "http" and frontend.hostname not in local_hosts)
        or frontend.path not in {"", "/"}
        or frontend.username
        or frontend.password
        or frontend.query
        or frontend.fragment
    ):
        raise HTTPException(status_code=503, detail="OIDC redirect URLs are not safely configured.")
    return OAuth().register(
        name="oidc",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=discovery_url,
        client_kwargs={"scope": "openid email profile"},
    )


@app.get("/health")
def health() -> dict:
    database = _database_health()
    cache = cache_health()
    return {
        "status": "ok" if database["status"] == "ok" else "degraded",
        "platform": settings.app_name,
        "database": database,
        "cache": cache,
        "genie": False,
        "warehouse": warehouse_status(),
        "llm": provider_status(),
        "tabpfn": orchestrator.tabpfn.package_status(),
        "rag": orchestrator.graph.rag.status(),
        "demo_mode": settings.demo_mode,
        "domains": list(DOMAINS),
    }


@app.get("/auth/domains")
def list_domains() -> dict:
    return {
        "domains": [{"id": d, "label": DOMAIN_LABELS[d]} for d in DOMAINS],
        "users": (
            [
                {"username": u["username"], "domain": u["domain"], "password_hint": u["password"]}
                for u in DEMO_USERS
            ]
            if settings.demo_mode
            else []
        ),
        "oidc_enabled": bool(
            settings.oidc_client_id and settings.oidc_client_secret and settings.oidc_redirect_uri
        ),
    }


@app.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    token, user = authenticate(db, body.username, body.password, body.domain)
    domains = domains_for(db, user)
    return TokenResponse(
        access_token=token,
        username=user.username,
        domain=user.domain,
        role=user.role,
        domains=domains,
    )


@app.post("/auth/signup", response_model=TokenResponse, status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    token, user = register_user(
        db,
        body.username,
        body.password,
        body.domain,
        body.display_name,
    )
    return TokenResponse(
        access_token=token,
        username=user.username,
        domain=user.domain,
        role=user.role,
        domains=[user.domain],
    )


def _identity_provider_unavailable_message(exc: Exception) -> str:
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    reason = f" (it answered HTTP {status_code})" if status_code else ""
    return (
        f"Google sign-in is unavailable: this server could not reach the identity provider{reason}. "
        "A network or security proxy may be blocking accounts.google.com. Sign in with your username and "
        "password, or ask your IT team to allow Google sign-in."
    )


@app.get("/auth/oidc/login")
async def oidc_login(
    request: Request,
    domain: str | None = None,
    intent: str | None = None,
    username: str | None = None,
):
    client = _oidc_client()
    if domain and domain not in DOMAINS:
        raise HTTPException(status_code=422, detail="Choose a valid business domain.")
    if intent not in {None, "signup"}:
        raise HTTPException(status_code=422, detail="Choose a valid Google sign-in action.")
    if intent == "signup":
        if not username or not re.fullmatch(r"[a-zA-Z0-9_.-]{3,40}", username):
            raise HTTPException(status_code=422, detail="Choose a valid 3-40 character username for your account.")
        if domain not in DOMAINS or domain == "healthcare":
            raise HTTPException(status_code=422, detail="Choose a valid business domain for your analyst account.")
        username = username.strip().lower()
    elif username is not None:
        raise HTTPException(status_code=422, detail="A username is only allowed when creating an account.")
    nonce = secrets.token_urlsafe(32)
    request.session["oidc_nonce"] = nonce
    request.session["oidc_domain"] = domain
    request.session["oidc_intent"] = intent
    request.session["oidc_username"] = username
    try:
        return await client.authorize_redirect(
            request,
            redirect_uri=settings.oidc_redirect_uri,
            nonce=nonce,
        )
    except (AuthlibBaseError, *_HTTP_ERRORS, OSError, ValueError) as exc:
        logger.warning("Could not start OIDC authorization: %s: %s", type(exc).__name__, str(exc)[:200])
        request.session.clear()
        # The browser navigated here directly, so send it back to the sign-in dialog with an
        # explanation instead of leaving it on a raw error page.
        message = _identity_provider_unavailable_message(exc)
        return RedirectResponse(
            f"{settings.frontend_url.rstrip('/')}/?{urlencode({'login': '1', 'sso_error': message})}",
            status_code=303,
        )


@app.get("/auth/oidc/callback")
async def oidc_callback(request: Request, db: Session = Depends(get_db)):
    client = _oidc_client()
    nonce = request.session.pop("oidc_nonce", None)
    requested_domain = request.session.pop("oidc_domain", None)
    intent = request.session.pop("oidc_intent", None)
    requested_username = request.session.pop("oidc_username", None)
    if not nonce:
        raise HTTPException(status_code=401, detail="OIDC sign-in state expired. Please try again.")
    try:
        token = await client.authorize_access_token(request)
        claims = token.get("userinfo")
    except (AuthlibBaseError, JoseError, *_HTTP_ERRORS, OSError, KeyError, ValueError) as exc:
        logger.warning("OIDC callback validation failed", exc_info=True)
        raise HTTPException(status_code=401, detail="Identity provider sign-in could not be validated.") from exc
    if claims is None:
        raise HTTPException(status_code=401, detail="Identity provider did not return a valid ID token.")

    issuer = claims.get("iss")
    subject = claims.get("sub")
    if (
        not isinstance(issuer, str)
        or not isinstance(subject, str)
        or not subject
        or not isinstance(claims.get("email"), str)
        or not claims["email"]
        or claims.get("email_verified") is not True
    ):
        raise HTTPException(status_code=401, detail="Identity provider did not return a verified account.")
    identity = (
        db.query(OAuthIdentity)
        .filter(OAuthIdentity.issuer == issuer, OAuthIdentity.subject == subject)
        .one_or_none()
    )
    if identity is None:
        if intent != "signup":
            # First Google sign-in: no account or session yet. Hand the verified identity
            # to the domain-selection page through a short-lived onboarding cookie.
            display_name = claims.get("name")
            onboarding_token = create_onboarding_token(
                issuer,
                subject,
                claims["email"],
                display_name if isinstance(display_name, str) else "",
            )
            response = RedirectResponse(f"{settings.frontend_url.rstrip('/')}/?onboarding=select", status_code=303)
            response.set_cookie(
                ONBOARDING_COOKIE,
                onboarding_token,
                max_age=ONBOARDING_TOKEN_MINUTES * 60,
                httponly=True,
                secure=not settings.demo_mode,
                samesite="lax",
                path=ONBOARDING_COOKIE_PATH,
            )
            response.delete_cookie("ryze_session", path="/", httponly=True, samesite="lax")
            request.session.clear()
            return response
        if not requested_username or not requested_domain:
            raise HTTPException(
                status_code=403,
                detail="This identity is not linked to an application account. Choose Create an account to sign up with Google.",
            )
        display_name = claims.get("name")
        account = register_oidc_user(
            db,
            requested_username,
            requested_domain,
            display_name if isinstance(display_name, str) else "",
            issuer,
            subject,
        )
    else:
        if intent == "signup" and requested_username != identity.username:
            raise HTTPException(
                status_code=409,
                detail="This Google identity is already linked to another application account.",
            )
        account = db.query(AppUser).filter(AppUser.username == identity.username).one_or_none()
    if account is None:
        raise HTTPException(status_code=403, detail="The linked application account is unavailable.")
    if account.domain == "all":
        selected_domain = requested_domain or "retail"
    else:
        selected_domain = account.domain
    if selected_domain not in DOMAINS:
        raise HTTPException(status_code=403, detail="The linked account has no valid business-domain access.")

    role = "admin" if account.domain == "all" else "analyst"
    tenant_id = None
    if selected_domain == "healthcare":
        assignment = (
            db.query(UserDomainTenant)
            .filter(
                UserDomainTenant.username == account.username,
                UserDomainTenant.domain == "healthcare",
            )
            .one_or_none()
        )
        if assignment is None:
            if account.domain == "healthcare":
                # Self-selected Healthcare account still waiting for an administrator to
                # provision its tenant: show the pending page and issue no session.
                request.session.clear()
                pending_response = RedirectResponse(
                    f"{settings.frontend_url.rstrip('/')}/?onboarding=healthcare_pending",
                    status_code=303,
                )
                pending_response.delete_cookie("ryze_session", path="/", httponly=True, samesite="lax")
                return pending_response
            raise HTTPException(status_code=403, detail="This user has no administrator-provisioned Healthcare tenant.")
        tenant_id = assignment.tenant_id
        claim_name = settings.oidc_tenant_claim.strip()
        if claim_name:
            claimed_tenant = claims.get(claim_name)
            if not isinstance(claimed_tenant, str) or claimed_tenant != tenant_id:
                raise HTTPException(status_code=403, detail="The identity provider tenant claim does not match the provisioned Healthcare tenant.")
    app_token = create_access_token(account.username, selected_domain, role)
    response = RedirectResponse(f"{settings.frontend_url.rstrip('/')}/dashboard", status_code=303)
    response.set_cookie(
        "ryze_session",
        app_token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=not settings.demo_mode,
        samesite="lax",
        path="/",
    )
    request.session.clear()
    return response


@app.get("/auth/oidc/onboarding")
def oidc_onboarding_status(request: Request) -> dict:
    pending = read_onboarding_token(request.cookies.get(ONBOARDING_COOKIE))
    return {
        "email": pending["email"],
        "display_name": pending["name"],
        "domains": [
            {"id": d, "label": DOMAIN_LABELS[d], "requires_provisioning": d == "healthcare"}
            for d in DOMAINS
        ],
    }


@app.post("/auth/oidc/onboarding")
def oidc_onboarding_complete(
    body: OidcOnboardingRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    # The cookie-session origin check in request_metrics only covers ryze_session, so
    # apply the same check to this cookie-authenticated write.
    if request.headers.get("origin") not in allowed_origins:
        raise HTTPException(status_code=403, detail="Invalid request origin.")
    pending = read_onboarding_token(request.cookies.get(ONBOARDING_COOKIE))
    account = register_oidc_onboarding_user(
        db,
        pending["idp_iss"],
        pending["idp_sub"],
        pending["email"],
        pending["name"],
        body.domain,
    )
    if account.domain == "healthcare":
        # A brand-new account cannot have a tenant yet, so no session is issued.
        response = JSONResponse({
            "status": "healthcare_pending",
            "username": account.username,
            "domain": account.domain,
        })
        response.delete_cookie("ryze_session", path="/", httponly=True, samesite="lax")
    else:
        response = JSONResponse({
            "status": "active",
            "username": account.username,
            "domain": account.domain,
            "role": "analyst",
            "domains": [account.domain],
        })
        response.set_cookie(
            "ryze_session",
            create_access_token(account.username, account.domain, "analyst"),
            max_age=settings.access_token_expire_minutes * 60,
            httponly=True,
            secure=not settings.demo_mode,
            samesite="lax",
            path="/",
        )
    response.delete_cookie(
        ONBOARDING_COOKIE,
        path=ONBOARDING_COOKIE_PATH,
        httponly=True,
        secure=not settings.demo_mode,
        samesite="lax",
    )
    return response


@app.post("/auth/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie("ryze_session", path="/", httponly=True, samesite="lax")
    return {"ok": True}


@app.post("/query", response_model=QueryResponse)
def query(
    body: QueryRequest,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> QueryResponse:
    conversation_id = body.conversation_id or uuid4()
    conversation, conversation_context = _get_conversation_context(
        db,
        user,
        conversation_id,
        body.question,
    )
    inference_started = time.perf_counter()
    try:
        response = orchestrator.run(
            db,
            body.question,
            user,
            body.llm_provider,
            conversation_context=conversation_context,
            conversation_id=conversation_id,
        )
    finally:
        logger.info(
            "AI analytics inference completed",
            extra={
                "domain": user.domain,
                "llm_inference_latency_ms": round((time.perf_counter() - inference_started) * 1000, 2),
            },
        )
    conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add_all(
        [
            ConversationMessage(
                conversation_id=str(conversation_id),
                role="user",
                content=body.question,
            ),
            ConversationMessage(
                conversation_id=str(conversation_id),
                role="assistant",
                content=response.answer,
            ),
        ]
    )
    db.commit()
    return response


@app.get("/dashboard")
def dashboard(
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    refresh: bool = False,
) -> dict:
    # refresh=true comes from the explicit refresh button and skips cached results;
    # automatic polling reuses them.
    try:
        cache_key = (user.username, user.domain, user.role, user.tenant_id)
        now = time.monotonic()
        cache_ttl = max(0, min(settings.dashboard_cache_seconds, settings.cache_ttl_seconds))
        persistent_cache_key = scoped_key(
            "dashboard",
            {
                "username": user.username,
                "domain": user.domain,
                "role": user.role,
                "tenant_id": user.tenant_id,
                "warehouse": settings.warehouse,
                "simulate": settings.live_simulate,
            },
        )
        with _dashboard_cache_lock:
            cached = None if refresh else _dashboard_cache.get(cache_key)
        remote_cached = (
            cache_get_json(persistent_cache_key)
            if not refresh and cache_ttl > 0 and settings.warehouse in {"databricks", "snowflake"}
            and user.domain != "healthcare"
            else None
        )
        if (
            settings.warehouse in {"databricks", "snowflake"}
            and cache_ttl > 0
            and user.domain != "healthcare"
            and isinstance(remote_cached, dict)
        ):
            metrics = dict(remote_cached)
        elif (
            settings.warehouse in {"databricks", "snowflake"}
            and cache_ttl > 0
            and user.domain != "healthcare"
            and cached
            and now - cached[0] < cache_ttl
        ):
            metrics = dict(cached[1])
        else:
            metrics = orchestrator.dashboard_metrics(
                db,
                user.domain,
                simulate=True,
                tenant_id=user.tenant_id,
            )
            if (
                settings.warehouse in {"databricks", "snowflake"}
                and cache_ttl > 0
                and user.domain != "healthcare"
            ):
                cache_set_json(persistent_cache_key, metrics, cache_ttl)
                with _dashboard_cache_lock:
                    expired = [
                        key
                        for key, (created_at, _value) in _dashboard_cache.items()
                        if now - created_at >= cache_ttl
                    ]
                    for key in expired:
                        _dashboard_cache.pop(key, None)
                    _dashboard_cache[cache_key] = (time.monotonic(), dict(metrics))
    except GuardrailError as exc:
        logger.warning("Dashboard query failed for %s warehouse: %s", settings.warehouse, exc)
        status = warehouse_status()
        if not status["ready"]:
            detail = (
                f"The configured {settings.warehouse} warehouse is not ready. "
                f"{status['detail']}. Set these values in backend/.env and restart the API. "
                "The app will not substitute sample data for the configured live source. "
                "Use WAREHOUSE=local only if you intentionally want demo data."
            )
        else:
            detail = (
                f"The configured {settings.warehouse} warehouse query failed. "
                "Check warehouse availability, SELECT permissions, and backend logs. "
                "Set WAREHOUSE=local only if you intentionally want sample data."
            )
        raise HTTPException(
            status_code=503,
            detail=detail,
        ) from exc
    metrics["user"] = user.username
    metrics["role"] = user.role
    return metrics


@app.get("/reports", response_model=ReportResponse)
def reports(
    start_date: date,
    end_date: date,
    interval: Literal["daily", "weekly", "monthly", "quarterly"] = "monthly",
    comparison: Literal[
        "previous_period", "previous_month", "previous_quarter", "previous_year", "custom"
    ] = "previous_period",
    comparison_start: date | None = None,
    comparison_end: date | None = None,
    category: str | None = Query(default=None, max_length=100),
    region: str | None = Query(default=None, max_length=100),
    user: AuthUser = Depends(get_current_user),
) -> dict:
    filters = {
        "start": start_date,
        "end": end_date,
        "interval": interval,
        "comparison": comparison,
        "comparison_start": comparison_start,
        "comparison_end": comparison_end,
        "category": category,
        "region": region,
    }
    # Report results are cached per domain and filter set (never across domains). Healthcare
    # tenant data is not cached, matching the dashboard.
    cache_key = scoped_key("report", {"domain": user.domain, "warehouse": settings.warehouse, **filters})
    cacheable = user.domain != "healthcare" and settings.report_cache_seconds > 0
    if cacheable:
        cached = _report_cache.get(cache_key)
        if cached is not None:
            return cached
    try:
        result = get_report(domain=user.domain, tenant_id=user.tenant_id, **filters)
    except GuardrailError as exc:
        logger.warning("Report query rejected for %s: %s", user.domain, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if cacheable:
        _report_cache.set(cache_key, result, settings.report_cache_seconds)
    return result


@app.get("/monitor")
def monitor(
    days: int = 30,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> dict:
    if days < 1 or days > 365:
        raise HTTPException(status_code=422, detail="Days must be between 1 and 365.")
    cutoff = datetime.utcnow() - timedelta(days=days)
    scoped_history = db.query(QueryHistory).filter(
        QueryHistory.username == user.username,
        QueryHistory.domain == user.domain,
        QueryHistory.created_at >= cutoff,
    )
    if user.domain == "healthcare":
        scoped_history = scoped_history.filter(QueryHistory.tenant_id == user.tenant_id)
    rows = scoped_history.order_by(QueryHistory.created_at.desc()).limit(100).all()
    total = scoped_history.count()
    pending_reviews = db.query(HitlReview).filter(
        HitlReview.username == user.username,
        HitlReview.domain == user.domain,
        HitlReview.status == "pending",
    )
    if user.domain == "healthcare":
        pending_reviews = pending_reviews.filter(HitlReview.tenant_id == user.tenant_id)
    return {
        "domain": user.domain,
        "days": days,
        "total_questions": total,
        "unique_users": 1 if total else 0,
        "guardrail_blocks": scoped_history.filter(QueryHistory.guardrail_ok == 0).count(),
        "pending_reviews": pending_reviews.count(),
        "ratings": None,
        "activities": [
            {
                "audit_id": row.audit_id or f"AU-{row.id}",
                "question": row.question,
                "status": "passed" if row.guardrail_ok else "blocked",
                "rows": row.row_count,
                "created_at": str(row.created_at),
            }
            for row in rows
        ],
    }


@app.get("/prompts")
def prompts(user: AuthUser = Depends(get_current_user)) -> dict:
    return {"domain": user.domain, "prompts": PROMPTS.get(user.domain, [])}


@app.get("/history")
def history(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> list[dict]:
    query = db.query(QueryHistory).filter(
        QueryHistory.username == user.username,
        QueryHistory.domain == user.domain,
    )
    if user.domain == "healthcare":
        query = query.filter(QueryHistory.tenant_id == user.tenant_id)
    rows = query.order_by(QueryHistory.id.desc()).limit(20).all()
    items = []
    for row in rows:
        requested_type = requested_sensitive_type(row.question)
        if requested_type == "never_disclose" or (requested_type == "admin_only" and user.role != "admin"):
            continue
        result = QueryResponse.model_validate_json(row.result_json) if row.result_json else None
        if result:
            if has_cvv_columns(result.table):
                continue
            restricted_output = has_restricted_columns(result.table)
            result.table = sanitize_sensitive_rows(result.table, user.role)
        else:
            restricted_output = False
        items.append({
            "id": row.id,
            "audit_id": row.audit_id,
            "question": row.question,
            "answer": "Sensitive fields are restricted for your account." if restricted_output and user.role != "admin" else row.answer,
            "sql": row.sql,
            "tables_used": row.tables_used.split(",") if row.tables_used else [],
            "row_count": row.row_count,
            "guardrail_ok": bool(row.guardrail_ok),
            "result": result,
            "conversation_id": str(result.conversation_id) if result and result.conversation_id else None,
            "created_at": str(row.created_at),
        })
    return items


@app.post("/history/{history_id}/replay", response_model=QueryResponse)
def replay_history(
    history_id: int,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> QueryResponse:
    history_query = db.query(QueryHistory).filter(
        QueryHistory.id == history_id,
        QueryHistory.username == user.username,
        QueryHistory.domain == user.domain,
    )
    if user.domain == "healthcare":
        history_query = history_query.filter(QueryHistory.tenant_id == user.tenant_id)
    row = history_query.first()
    if row is None:
        raise HTTPException(status_code=404, detail="History item not found")
    require_sensitive_request_access(row.question, user.role)
    if row.result_json:
        response = QueryResponse.model_validate_json(row.result_json)
        if has_cvv_columns(response.table):
            raise HTTPException(status_code=403, detail="This saved result contains prohibited payment data and cannot be replayed.")
        restricted_output = has_restricted_columns(response.table)
        response.table = sanitize_sensitive_rows(response.table, user.role)
        if restricted_output and user.role != "admin":
            response.answer = "Sensitive fields are restricted for your account."
        return response
    response = orchestrator.run(db, row.question, user, persist_history=False)
    row.answer = response.answer
    row.sql = response.sql
    row.result_json = response.model_dump_json()
    db.commit()
    return response


def _serialize_review(row: HitlReview) -> dict:
    return {
        "id": row.id,
        "audit_id": row.audit_id,
        "question": row.question,
        "status": row.status,
        "reason": row.reason,
        "user": row.username,
        "domain": row.domain,
        "conversation_id": row.conversation_id,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": str(row.reviewed_at) if row.reviewed_at else None,
        "decision_note": row.decision_note,
        "created_at": str(row.created_at),
    }


@app.get("/hitl")
def hitl_queue(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> list[dict]:
    # Administrators review every domain's queue; business users only see their own requests.
    if user.role == "admin":
        queue_query = db.query(HitlReview)
    else:
        queue_query = db.query(HitlReview).filter(HitlReview.username == user.username)
        if user.domain == "healthcare":
            queue_query = queue_query.filter(HitlReview.tenant_id == user.tenant_id)
    rows = queue_query.order_by(HitlReview.id.desc()).limit(50).all()
    return [_serialize_review(r) for r in rows]


@app.post("/hitl/{item_id}/resume", response_model=QueryResponse)
def hitl_resume(
    item_id: int,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> QueryResponse:
    """Continue an approved prediction request in the requester's original conversation."""
    row = (
        db.query(HitlReview)
        .filter(HitlReview.id == item_id, HitlReview.username == user.username)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Review request not found")
    if row.status == "pending":
        raise HTTPException(status_code=409, detail="This request is still awaiting an administrator's review.")
    if row.status != "approved":
        raise HTTPException(status_code=409, detail=f"This request was {row.status} and cannot be run.")
    if row.domain != user.domain or row.tenant_id != user.tenant_id:
        raise HTTPException(
            status_code=409,
            detail=f"Switch to the {DOMAIN_LABELS.get(row.domain, row.domain)} domain to run this approved request.",
        )
    conversation_id = UUID(row.conversation_id) if row.conversation_id else uuid4()
    conversation, conversation_context = _get_conversation_context(db, user, conversation_id, row.question)
    response = orchestrator.run(
        db,
        row.question,
        user,
        conversation_context=conversation_context,
        conversation_id=conversation_id,
        approved_review=row,
    )
    row.status = "completed"
    row.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    conversation.updated_at = row.completed_at
    db.add_all([
        ConversationMessage(conversation_id=str(conversation_id), role="user", content=row.question),
        ConversationMessage(conversation_id=str(conversation_id), role="assistant", content=response.answer),
    ])
    db.commit()
    return response


@app.post("/hitl/{item_id}/{action}")
def hitl_action(
    item_id: int,
    action: str,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
    body: AccessDecision | None = None,
) -> dict:
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="Action must be 'approve' or 'reject'")
    review_query = db.query(HitlReview).filter(HitlReview.id == item_id)
    if user.role != "admin":
        # Reviews outside what the caller may see (another user or tenant) are not disclosed.
        review_query = review_query.filter(HitlReview.username == user.username)
        if user.domain == "healthcare":
            review_query = review_query.filter(HitlReview.tenant_id == user.tenant_id)
    row = review_query.with_for_update().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Review request not found")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can approve or reject review requests.")
    if row.username == user.username:
        raise HTTPException(status_code=403, detail="You cannot review your own request.")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail=f"This request was already {row.status}.")
    row.status = "approved" if action == "approve" else "rejected"
    row.reviewed_by = user.username
    row.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    row.decision_note = (body.note or "").strip()[:500] or None if body else None
    db.commit()
    return {"ok": True, "status": row.status, "reviewed_by": row.reviewed_by}


@app.get("/me")
def me(user: AuthUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    warehouse = warehouse_status()
    return {
        "username": user.username,
        "domain": user.domain,
        "role": user.role,
        # Read from the database on every call, so newly approved or revoked domains show up.
        "authorized_domains": domains_for(db, user),
        "notifications": notification_count(db, user),
        "warehouse": warehouse,
        "data_mode": "live" if warehouse["mode"] in {"databricks", "snowflake"} else "sample",
        "demo_mode": settings.demo_mode,
        "llm": provider_status(),
        "tabpfn": orchestrator.tabpfn.package_status(),
        "rag": orchestrator.graph.rag.status(),
    }


@app.get("/warehouse/check")
def warehouse_check(user: AuthUser = Depends(get_current_user)) -> dict:
    return test_warehouse_connection(user.domain)


@app.post("/warehouse/verify")
def warehouse_verify(user: AuthUser = Depends(get_current_user)) -> dict:
    try:
        return verify_warehouse_domain(user.domain)
    except GuardrailError as exc:
        logger.warning("Databricks verification failed for %s: %s", user.domain, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/context")
def user_context(
    domain: str | None = None,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> dict:
    permitted_domains = domains_for(db, user)
    selected_domain = domain or user.domain
    if selected_domain not in permitted_domains:
        raise HTTPException(status_code=403, detail="This user cannot view context for that business domain.")
    if selected_domain == "healthcare" and user.domain != "healthcare":
        assignment = (
            db.query(UserDomainTenant)
            .filter(
                UserDomainTenant.username == user.username,
                UserDomainTenant.domain == "healthcare",
            )
            .one_or_none()
        )
        if assignment is None:
            raise HTTPException(status_code=403, detail="This user has no administrator-provisioned Healthcare tenant.")

    graph = orchestrator.graph
    domain_glossary = db.query(Glossary).filter(Glossary.domain == selected_domain).all()
    if settings.warehouse in {"databricks", "snowflake"}:
        tables = graph.domain_tables.get(selected_domain, {})
    else:
        mapped_tables = {term.table_name for term in domain_glossary}
        tables = {table: columns for table, columns in graph.tables.items() if table in mapped_tables}
    source_tables = {
        table: [
            column
            for column in columns
            if not is_sensitive_column(column["name"])
        ]
        for table, columns in tables.items()
        if table not in {"glossary", "query_history", "live_events", "hitl_reviews"}
    }
    visible_relationships = []
    for source, target, metadata in graph.graph.edges(data=True):
        if source in source_tables and target in source_tables:
            visible_relationships.append(
                {
                    "source": source,
                    "target": target,
                    "keys": metadata.get("columns") or [],
                    "evidence": "database foreign-key metadata",
                }
            )

    glossary = []
    for term in domain_glossary:
        if is_sensitive_column(term.column_name):
            continue
        available_columns = source_tables.get(term.table_name, [])
        mapped = any(column["name"] == term.column_name for column in available_columns)
        glossary.append(
            {
                "term": term.business_term,
                "definition": term.description,
                "table": term.table_name,
                "column": term.column_name if mapped else None,
                "mapping_status": "mapped" if mapped else "source mapping unavailable",
            }
        )

    source = {
        "warehouse": settings.warehouse,
        "configured": warehouse_status()["ready"],
        "schema": None,
        "catalog": None,
        "metadata_available": bool(source_tables),
    }
    if settings.warehouse == "databricks":
        source["schema"] = settings.databricks_domain_schema
        try:
            source["catalog"] = databricks_domain_catalog(selected_domain)
        except GuardrailError:
            source["configured"] = False
    elif settings.warehouse == "snowflake":
        source["catalog"] = settings.snowflake_database or None
        source["schema"] = settings.snowflake_schema

    return {
        "user": {
            "username": user.username,
            "role": user.role,
            "domain": selected_domain,
            "domain_label": DOMAIN_LABELS[selected_domain],
            "permitted_domains": permitted_domains,
            "organization": None,
        },
        "source": source,
        "tables": [
            {
                "name": table,
                "columns": [column["name"] for column in columns],
            }
            for table, columns in sorted(source_tables.items())
        ],
        "relationships": visible_relationships,
        "glossary": glossary,
        "metrics": [],
        "rules": [],
        "synchronized_at": None,
    }


@app.post("/tabpfn/diagnostics")
def tabpfn_diagnostics(user: AuthUser = Depends(get_current_user)) -> dict:
    if not orchestrator.tabpfn.package_status()["installed"]:
        raise HTTPException(
            status_code=503,
            detail="TabPFN is not installed in the backend environment. Install backend/requirements.txt and restart the API.",
        )
    try:
        return orchestrator.tabpfn.diagnostic_test()
    except Exception as exc:
        logger.exception("TabPFN diagnostic prediction failed")
        raise HTTPException(
            status_code=503,
            detail=f"TabPFN is installed, but the diagnostic prediction failed: {exc}",
        ) from exc


# Keep CORS outside FastAPI's error middleware so even unexpected 500 responses
# carry the headers needed by the browser to show the actual API error.
fastapi_app = app
app = CORSMiddleware(
    app=fastapi_app,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
fastapi_app.add_middleware(
    SessionMiddleware,
    secret_key=settings.jwt_secret,
    max_age=settings.access_token_expire_minutes * 60,
    same_site="lax",
    https_only=not settings.demo_mode,
)
