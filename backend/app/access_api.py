"""Routes for domain switching, access requests and administrator access management."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .auth import AuthUser, create_access_token, get_current_user, has_domain_access, require_admin
from .config import settings
from .db import get_db
from .domains import DOMAIN_LABELS
from .models import AppUser, UserDomainTenant
from .schemas import (
    AccessDecision,
    DomainAccessRequestCreate,
    DomainGrantCreate,
    RoleUpdate,
    SwitchDomainRequest,
)
from .services import access

router = APIRouter()


@router.post("/auth/switch-domain")
def switch_domain(
    body: SwitchDomainRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    account = db.query(AppUser).filter(AppUser.username == user.username).one_or_none()
    if account is None:
        raise HTTPException(status_code=401, detail="Unknown user")
    if not has_domain_access(db, account, body.domain):
        raise HTTPException(status_code=403, detail="You do not have access to that business domain. Request access first.")
    if body.domain == "healthcare":
        tenant = (
            db.query(UserDomainTenant)
            .filter(UserDomainTenant.username == account.username, UserDomainTenant.domain == "healthcare")
            .one_or_none()
        )
        if tenant is None:
            raise HTTPException(status_code=403, detail="This user has no administrator-provisioned Healthcare tenant.")
    token = create_access_token(account.username, body.domain, user.role)
    payload = {
        "username": account.username,
        "domain": body.domain,
        "domain_label": DOMAIN_LABELS[body.domain],
        "role": user.role,
    }
    # Password sign-ins keep their bearer token in the browser, so they get the new token
    # back; Google sign-ins keep using only the HttpOnly cookie.
    if "authorization" in request.headers:
        payload["access_token"] = token
    response = JSONResponse(payload)
    response.set_cookie(
        "ryze_session",
        token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=not settings.demo_mode,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/access/domains")
def my_domains(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict:
    return access.domain_overview(db, user)


@router.get("/access/requests")
def my_requests(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> list[dict]:
    rows = access.list_requests(db, username=user.username)
    access.mark_decisions_seen(db, user.username)
    return rows


@router.post("/access/requests", status_code=201)
def submit_request(
    body: DomainAccessRequestCreate,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
) -> dict:
    return access.serialize_request(access.create_request(db, user, body.domain, body.reason))


@router.get("/admin/overview")
def admin_overview(db: Session = Depends(get_db), _admin: AuthUser = Depends(require_admin)) -> dict:
    return access.admin_overview(db)


@router.get("/admin/users")
def admin_users(db: Session = Depends(get_db), _admin: AuthUser = Depends(require_admin)) -> list[dict]:
    return access.list_users(db)


@router.post("/admin/users/{username}/domains", status_code=201)
def admin_grant_domain(
    username: str,
    body: DomainGrantCreate,
    db: Session = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> dict:
    access.grant_domain(db, admin, username, body.domain, body.note)
    return {"ok": True}


@router.delete("/admin/users/{username}/domains/{domain}")
def admin_revoke_domain(
    username: str,
    domain: str,
    db: Session = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> dict:
    access.revoke_domain(db, admin, username, domain)
    return {"ok": True}


@router.patch("/admin/users/{username}/role")
def admin_set_role(
    username: str,
    body: RoleUpdate,
    db: Session = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> dict:
    account = access.set_role(db, admin, username, body.role, body.primary_domain)
    return {"username": account.username, "role": "admin" if account.domain == "all" else "analyst"}


@router.get("/admin/access-requests")
def admin_requests(
    status: Literal["pending", "approved", "rejected"] | None = Query(default=None),
    db: Session = Depends(get_db),
    _admin: AuthUser = Depends(require_admin),
) -> list[dict]:
    return access.list_requests(db, request_status=status)


@router.post("/admin/access-requests/{request_id}/approve")
def admin_approve(
    request_id: int,
    body: AccessDecision | None = None,
    db: Session = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> dict:
    return access.serialize_request(access.decide_request(db, admin, request_id, True, body.note if body else None))


@router.post("/admin/access-requests/{request_id}/reject")
def admin_reject(
    request_id: int,
    body: AccessDecision | None = None,
    db: Session = Depends(get_db),
    admin: AuthUser = Depends(require_admin),
) -> dict:
    return access.serialize_request(access.decide_request(db, admin, request_id, False, body.note if body else None))


@router.get("/admin/audit")
def admin_audit(db: Session = Depends(get_db), _admin: AuthUser = Depends(require_admin)) -> list[dict]:
    return access.list_audit(db)
