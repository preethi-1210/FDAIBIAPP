from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .domains import DOMAINS, normalize_domain
from .models import AppUser
from .security import hash_password, verify_password

bearer = HTTPBearer(auto_error=False)
_USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{3,40}$")


@dataclass
class AuthUser:
    username: str
    domain: str
    role: str


def create_access_token(username: str, domain: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": username, "domain": domain, "role": role, "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
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
    if user.domain != "all" and requested != user.domain:
        raise HTTPException(status_code=403, detail="This user cannot open that business domain")
    if requested == "all":
        requested = "retail"
    if requested not in DOMAINS:
        raise HTTPException(status_code=400, detail="Unknown business domain")
    role = "admin" if user.domain == "all" else "analyst"
    auth = AuthUser(username=user.username, domain=requested, role=role)
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
    if domain not in DOMAINS:
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
        account = db.query(AppUser).filter(AppUser.username == username).one_or_none()
        if account is None:
            raise HTTPException(status_code=401, detail="Unknown user")
        if account.domain != "all" and account.domain != domain:
            raise HTTPException(status_code=403, detail="This account no longer has access to that business domain")
        current_role = "admin" if account.domain == "all" else "analyst"
        return AuthUser(username=username, domain=domain, role=current_role)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
