from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .domains import DOMAINS, normalize_domain
from .models import AppUser, OAuthIdentity, UserDomainGrant, UserDomainTenant
from .security import hash_password, verify_password

_USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{3,40}$")


@dataclass
class AuthUser:
    username: str
    domain: str
    role: str
    tenant_id: str | None = None


class SessionOrBearer(HTTPBearer):
    async def __call__(self, request: Request) -> HTTPAuthorizationCredentials | None:
        credentials = await super().__call__(request)
        if credentials is not None:
            return credentials
        token = request.cookies.get("ryze_session")
        if token:
            return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        return None


bearer = SessionOrBearer(auto_error=False)


def create_access_token(username: str, domain: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": username, "domain": domain, "role": role, "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _tenant_assignment(db: Session, username: str, domain: str) -> str | None:
    assignment = (
        db.query(UserDomainTenant)
        .filter(UserDomainTenant.username == username, UserDomainTenant.domain == domain)
        .one_or_none()
    )
    return assignment.tenant_id if assignment else None


def is_admin_account(account: AppUser) -> bool:
    # Administrators are provisioned server-side (create_user.py / set_user_role.py or an
    # existing administrator) by setting app_users.domain to "all"; never from token claims.
    return account.domain == "all"


def authorized_domains(db: Session, account: AppUser) -> list[str]:
    """Every business domain the account may open, in canonical order."""
    if is_admin_account(account):
        return list(DOMAINS)
    granted = {
        row.domain
        for row in db.query(UserDomainGrant).filter(UserDomainGrant.username == account.username).all()
    }
    return [domain for domain in DOMAINS if domain == account.domain or domain in granted]


def has_domain_access(db: Session, account: AppUser, domain: str) -> bool:
    if domain not in DOMAINS:
        return False
    if is_admin_account(account) or domain == account.domain:
        return True
    return (
        db.query(UserDomainGrant)
        .filter(UserDomainGrant.username == account.username, UserDomainGrant.domain == domain)
        .first()
        is not None
    )


def authenticate(db: Session, username: str, password: str, domain: str | None) -> tuple[str, AuthUser]:
    username = username.strip().lower()
    user = db.query(AppUser).filter(AppUser.username == username).one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    if not user.password_hash.startswith("pbkdf2_sha256$"):
        user.password_hash = hash_password(password)
        db.commit()
    requested = normalize_domain(domain or (user.domain if user.domain != "all" else "retail"))
    if requested == "all":
        requested = "retail"
    if requested not in DOMAINS:
        raise HTTPException(status_code=400, detail="Unknown business domain")
    if not has_domain_access(db, user, requested):
        raise HTTPException(status_code=403, detail="This user cannot open that business domain")
    tenant_id = _tenant_assignment(db, user.username, requested) if requested == "healthcare" else None
    if requested == "healthcare" and not tenant_id:
        raise HTTPException(status_code=403, detail="This user has no administrator-provisioned Healthcare tenant.")
    role = "admin" if user.domain == "all" else "analyst"
    auth = AuthUser(username=user.username, domain=requested, role=role, tenant_id=tenant_id)
    return create_access_token(user.username, requested, role), auth


def register_user(
    db: Session,
    username: str,
    password: str,
    domain: str,
    display_name: str,
) -> tuple[str, AuthUser]:
    username = username.strip().lower()
    display_name = display_name.strip()
    if not _USERNAME_PATTERN.fullmatch(username):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Username must be 3-40 characters and use only letters, numbers, dots, underscores, or hyphens.",
        )
    if domain not in DOMAINS or domain == "healthcare":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose a valid business domain.")
    if not display_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Display name cannot be blank.")
    if db.query(AppUser).filter(AppUser.username == username).one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That username is already registered.")

    account = AppUser(
        username=username,
        password_hash=hash_password(password),
        domain=domain,
        display_name=display_name,
    )
    db.add(account)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That username is already registered.") from exc

    auth = AuthUser(username=username, domain=domain, role="analyst")
    return create_access_token(username, domain, auth.role), auth


def register_oidc_user(
    db: Session,
    username: str,
    domain: str,
    display_name: str,
    issuer: str,
    subject: str,
) -> AppUser:
    username = username.strip().lower()
    display_name = display_name.strip() or username
    if not _USERNAME_PATTERN.fullmatch(username):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Username must be 3-40 characters and use only letters, numbers, dots, underscores, or hyphens.",
        )
    if domain not in DOMAINS or domain == "healthcare":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose a valid business domain.")
    if db.query(AppUser).filter(AppUser.username == username).one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That username is already registered.")

    account = AppUser(
        username=username,
        password_hash=hash_password(secrets.token_urlsafe(48)),
        domain=domain,
        display_name=display_name[:120],
    )
    db.add(account)
    db.add(OAuthIdentity(issuer=issuer, subject=subject, username=username))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Google identity or username is already linked to another account.",
        ) from exc
    return account


# A verified Google identity that has no application account yet is parked in a
# short-lived, single-purpose token until the user picks a business domain. The
# audience claim and the absence of "sub"/"domain" make get_current_user reject it.
ONBOARDING_COOKIE = "ryze_oidc_onboarding"
ONBOARDING_COOKIE_PATH = "/auth/oidc/onboarding"
ONBOARDING_TOKEN_MINUTES = 15
_ONBOARDING_AUDIENCE = "ryze-oidc-onboarding"


def create_onboarding_token(issuer: str, subject: str, email: str, display_name: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ONBOARDING_TOKEN_MINUTES)
    return jwt.encode(
        {
            "aud": _ONBOARDING_AUDIENCE,
            "idp_iss": issuer,
            "idp_sub": subject,
            "email": email,
            "name": display_name,
            "exp": expire,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def read_onboarding_token(token: str | None) -> dict[str, str]:
    expired = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Your Google sign-in has expired. Please sign in with Google again.",
    )
    if not token:
        raise expired
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=_ONBOARDING_AUDIENCE,
        )
    except JWTError as exc:
        raise expired from exc
    fields = ("idp_iss", "idp_sub", "email", "name")
    if not all(isinstance(payload.get(field), str) for field in fields) or not payload["idp_sub"]:
        raise expired
    return {field: payload[field] for field in fields}


def _oidc_username_candidate(email: str) -> str:
    local_part = email.split("@", 1)[0].strip().lower()
    base = re.sub(r"[^a-z0-9_.-]", "", local_part)[:32]
    return base if len(base) >= 3 else "user"


def register_oidc_onboarding_user(
    db: Session,
    issuer: str,
    subject: str,
    email: str,
    display_name: str,
    domain: str,
) -> AppUser:
    # Self-service selection is limited to the business domains. "all" (administrator)
    # is never selectable, and choosing Healthcare only records the request: data access
    # still requires an administrator-provisioned UserDomainTenant row.
    if domain not in DOMAINS or domain == "all":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose a valid business domain.")
    linked = (
        db.query(OAuthIdentity)
        .filter(OAuthIdentity.issuer == issuer, OAuthIdentity.subject == subject)
        .one_or_none()
    )
    if linked is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Google identity already has an account. Please sign in with Google again.",
        )

    base = _oidc_username_candidate(email)
    username = base
    for _attempt in range(5):
        if db.query(AppUser).filter(AppUser.username == username).one_or_none() is None:
            break
        username = f"{base}-{secrets.token_hex(3)}"
    else:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Could not allocate a username. Please try again.")

    account = AppUser(
        username=username,
        password_hash=hash_password(secrets.token_urlsafe(48)),
        domain=domain,
        display_name=(display_name.strip() or username)[:120],
    )
    db.add(account)
    db.add(OAuthIdentity(issuer=issuer, subject=subject, username=username))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Google identity or username is already linked to another account.",
        ) from exc
    return account


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> AuthUser:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        username = payload.get("sub")
        domain = payload.get("domain")
        if not username or not domain:
            raise HTTPException(status_code=401, detail="Invalid token")
        if domain not in DOMAINS:
            raise HTTPException(status_code=403, detail="This token has an invalid business domain")
        account = db.query(AppUser).filter(AppUser.username == username).one_or_none()
        if account is None:
            raise HTTPException(status_code=401, detail="Unknown user")
        # Re-checked on every request, so a revoked grant stops working immediately.
        if not has_domain_access(db, account, domain):
            raise HTTPException(status_code=403, detail="This account no longer has access to that business domain")
        tenant_id = _tenant_assignment(db, username, domain) if domain == "healthcare" else None
        if domain == "healthcare" and not tenant_id:
            raise HTTPException(status_code=403, detail="This user has no administrator-provisioned Healthcare tenant.")
        current_role = "admin" if account.domain == "all" else "analyst"
        return AuthUser(username=username, domain=domain, role=current_role, tenant_id=tenant_id)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    # get_current_user derives the role from the database, so a forged role claim is ignored.
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access is required.")
    return user
