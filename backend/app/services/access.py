"""Domain access requests, grants, role changes and their audit trail.

Every mutation here is invoked from an endpoint that has already authenticated the caller;
administrator-only operations are additionally guarded by require_admin at the route.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import AuthUser, authorized_domains, is_admin_account
from ..domains import DOMAINS, DOMAIN_LABELS
from ..models import (
    AccessAuditEvent,
    AppUser,
    DomainAccessRequest,
    OAuthIdentity,
    UserDomainGrant,
    UserDomainTenant,
)

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
REQUEST_STATUSES = (PENDING, APPROVED, REJECTED)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def _label(domain: str) -> str:
    return DOMAIN_LABELS.get(domain, domain)


def _audit(
    db: Session,
    actor: str,
    action: str,
    target_username: str,
    domain: str | None = None,
    request_id: int | None = None,
    detail: str | None = None,
) -> None:
    db.add(
        AccessAuditEvent(
            actor=actor,
            action=action,
            target_username=target_username,
            domain=domain,
            request_id=request_id,
            detail=detail,
        )
    )


def _account(db: Session, username: str) -> AppUser:
    account = db.query(AppUser).filter(AppUser.username == username).one_or_none()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return account


def _has_healthcare_tenant(db: Session, username: str) -> bool:
    return (
        db.query(UserDomainTenant)
        .filter(UserDomainTenant.username == username, UserDomainTenant.domain == "healthcare")
        .first()
        is not None
    )


def authorize_context_domain(db: Session, user: AuthUser, domain: str | None) -> tuple[str, str | None]:
    """The domain a user may view context for, and its Healthcare tenant; raises 403 otherwise.

    Shared by GET /context and GET /context/foresight so both apply the same checks.
    """
    selected = domain or user.domain
    if selected not in domains_for(db, user):
        raise HTTPException(status_code=403, detail="This user cannot view context for that business domain.")
    tenant_id = user.tenant_id if selected == user.domain else None
    if selected == "healthcare" and user.domain != "healthcare":
        assignment = (
            db.query(UserDomainTenant)
            .filter(UserDomainTenant.username == user.username, UserDomainTenant.domain == "healthcare")
            .one_or_none()
        )
        if assignment is None:
            raise HTTPException(status_code=403, detail="This user has no administrator-provisioned Healthcare tenant.")
        tenant_id = assignment.tenant_id
    return selected, tenant_id


def domains_for(db: Session, user: AuthUser) -> list[str]:
    """Authorized domains for an authenticated user, read from the database."""
    if user.role == "admin":
        return list(DOMAINS)
    account = db.query(AppUser).filter(AppUser.username == user.username).one_or_none()
    return authorized_domains(db, account) if account is not None else [user.domain]


def serialize_request(row: DomainAccessRequest, display_names: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "username": row.username,
        "display_name": (display_names or {}).get(row.username, row.username),
        "current_domain": row.current_domain,
        "current_domain_label": _label(row.current_domain),
        "requested_domain": row.requested_domain,
        "requested_domain_label": _label(row.requested_domain),
        "reason": row.reason,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "decided_at": _iso(row.decided_at),
        "decided_by": row.decided_by,
        "decision_note": row.decision_note,
    }


def domain_overview(db: Session, user: AuthUser) -> dict[str, Any]:
    """What the signed-in user can open and what they could request."""
    authorized = domains_for(db, user)
    pending = {
        row.requested_domain
        for row in db.query(DomainAccessRequest).filter(
            DomainAccessRequest.username == user.username,
            DomainAccessRequest.status == PENDING,
        )
    }
    account = db.query(AppUser).filter(AppUser.username == user.username).one_or_none()
    has_tenant = _has_healthcare_tenant(db, user.username)
    return {
        "username": user.username,
        "role": user.role,
        "active_domain": user.domain,
        "primary_domain": None if account is None or is_admin_account(account) else account.domain,
        "domains": [
            {
                "id": domain,
                "label": _label(domain),
                "authorized": domain in authorized,
                "pending_request": domain in pending,
                # Healthcare data is tenant-scoped; access also needs a provisioned tenant.
                "requires_tenant": domain == "healthcare" and not has_tenant,
            }
            for domain in DOMAINS
        ],
    }


def create_request(db: Session, user: AuthUser, domain: str, reason: str) -> DomainAccessRequest:
    account = _account(db, user.username)
    if is_admin_account(account):
        raise HTTPException(status_code=400, detail="Administrators already have access to every domain.")
    if domain not in DOMAINS:
        raise HTTPException(status_code=422, detail="Choose a valid business domain.")
    if domain in authorized_domains(db, account):
        raise HTTPException(status_code=409, detail=f"You already have access to {_label(domain)}.")
    reason = reason.strip()
    if len(reason) < 10:
        raise HTTPException(status_code=422, detail="Explain why you need access (at least 10 characters).")
    duplicate = (
        db.query(DomainAccessRequest)
        .filter(
            DomainAccessRequest.username == account.username,
            DomainAccessRequest.requested_domain == domain,
            DomainAccessRequest.status == PENDING,
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail=f"A request for {_label(domain)} is already pending.")
    request = DomainAccessRequest(
        username=account.username,
        current_domain=user.domain,
        requested_domain=domain,
        reason=reason[:500],
        status=PENDING,
    )
    db.add(request)
    try:
        db.flush()
        _audit(db, account.username, "request_submitted", account.username, domain, request.id, reason[:500])
        db.commit()
    except IntegrityError as exc:
        # The partial unique index catches a concurrent duplicate submission.
        db.rollback()
        raise HTTPException(status_code=409, detail=f"A request for {_label(domain)} is already pending.") from exc
    db.refresh(request)
    return request


def list_requests(
    db: Session,
    username: str | None = None,
    request_status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    query = db.query(DomainAccessRequest)
    if username is not None:
        query = query.filter(DomainAccessRequest.username == username)
    if request_status is not None:
        query = query.filter(DomainAccessRequest.status == request_status)
    rows = query.order_by(DomainAccessRequest.created_at.desc(), DomainAccessRequest.id.desc()).limit(limit).all()
    names = {
        account.username: account.display_name
        for account in db.query(AppUser).filter(AppUser.username.in_({row.username for row in rows}))
    } if rows else {}
    return [serialize_request(row, names) for row in rows]


def mark_decisions_seen(db: Session, username: str) -> None:
    db.query(DomainAccessRequest).filter(
        DomainAccessRequest.username == username,
        DomainAccessRequest.status != PENDING,
        DomainAccessRequest.decision_seen == 0,
    ).update({DomainAccessRequest.decision_seen: 1}, synchronize_session=False)
    db.commit()


def notification_count(db: Session, user: AuthUser) -> int:
    """Admins: pending requests to review. Business users: decisions not yet viewed."""
    query = db.query(func.count(DomainAccessRequest.id))
    if user.role == "admin":
        return int(query.filter(DomainAccessRequest.status == PENDING).scalar() or 0)
    return int(
        query.filter(
            DomainAccessRequest.username == user.username,
            DomainAccessRequest.status != PENDING,
            DomainAccessRequest.decision_seen == 0,
        ).scalar()
        or 0
    )


def _add_grant(db: Session, admin: str, account: AppUser, domain: str, request_id: int | None) -> bool:
    if is_admin_account(account) or domain == account.domain:
        return False
    exists = (
        db.query(UserDomainGrant)
        .filter(UserDomainGrant.username == account.username, UserDomainGrant.domain == domain)
        .first()
    )
    if exists is not None:
        return False
    db.add(UserDomainGrant(username=account.username, domain=domain, granted_by=admin, request_id=request_id))
    return True


def decide_request(
    db: Session,
    admin: AuthUser,
    request_id: int,
    approve: bool,
    note: str | None,
) -> DomainAccessRequest:
    request = (
        db.query(DomainAccessRequest)
        .filter(DomainAccessRequest.id == request_id)
        .with_for_update()
        .one_or_none()
    )
    if request is None:
        raise HTTPException(status_code=404, detail="Access request not found.")
    if request.status != PENDING:
        raise HTTPException(status_code=409, detail=f"This request was already {request.status}.")
    account = _account(db, request.username)
    request.status = APPROVED if approve else REJECTED
    request.decided_at = _now()
    request.decided_by = admin.username
    request.decision_note = (note or "").strip()[:500] or None
    request.decision_seen = 0
    if approve:
        _add_grant(db, admin.username, account, request.requested_domain, request.id)
    _audit(
        db,
        admin.username,
        "request_approved" if approve else "request_rejected",
        account.username,
        request.requested_domain,
        request.id,
        request.decision_note,
    )
    db.commit()
    db.refresh(request)
    return request


def grant_domain(db: Session, admin: AuthUser, username: str, domain: str, note: str | None) -> None:
    account = _account(db, username)
    if is_admin_account(account):
        raise HTTPException(status_code=400, detail="Administrators already have access to every domain.")
    if domain not in DOMAINS:
        raise HTTPException(status_code=422, detail="Choose a valid business domain.")
    if not _add_grant(db, admin.username, account, domain, None):
        raise HTTPException(status_code=409, detail=f"{account.username} already has access to {_label(domain)}.")
    # A direct grant also settles any open request for the same domain.
    for pending in db.query(DomainAccessRequest).filter(
        DomainAccessRequest.username == account.username,
        DomainAccessRequest.requested_domain == domain,
        DomainAccessRequest.status == PENDING,
    ):
        pending.status = APPROVED
        pending.decided_at = _now()
        pending.decided_by = admin.username
        pending.decision_note = "Access granted directly by an administrator."
        pending.decision_seen = 0
    _audit(db, admin.username, "domain_granted", account.username, domain, detail=(note or "").strip()[:500] or None)
    db.commit()


def revoke_domain(db: Session, admin: AuthUser, username: str, domain: str) -> None:
    account = _account(db, username)
    if is_admin_account(account):
        raise HTTPException(status_code=400, detail="Change the administrator's role to restrict their domains.")
    if domain == account.domain:
        raise HTTPException(
            status_code=400,
            detail="The primary domain cannot be revoked. Assign a different primary domain instead.",
        )
    grant = (
        db.query(UserDomainGrant)
        .filter(UserDomainGrant.username == account.username, UserDomainGrant.domain == domain)
        .one_or_none()
    )
    if grant is None:
        raise HTTPException(status_code=404, detail=f"{account.username} has no granted access to {_label(domain)}.")
    db.delete(grant)
    _audit(db, admin.username, "domain_revoked", account.username, domain)
    db.commit()


def set_role(
    db: Session,
    admin: AuthUser,
    username: str,
    role: str,
    primary_domain: str | None,
) -> AppUser:
    if username == admin.username:
        raise HTTPException(status_code=400, detail="You cannot change your own role or primary domain.")
    account = _account(db, username)
    previous = "admin" if is_admin_account(account) else f"analyst:{account.domain}"
    if role == "admin":
        if is_admin_account(account):
            raise HTTPException(status_code=409, detail=f"{account.username} is already an administrator.")
        account.domain = "all"
        # Administrators implicitly have every domain, so grants and open requests lapse.
        db.query(UserDomainGrant).filter(UserDomainGrant.username == account.username).delete(
            synchronize_session=False
        )
        for pending in db.query(DomainAccessRequest).filter(
            DomainAccessRequest.username == account.username,
            DomainAccessRequest.status == PENDING,
        ):
            pending.status = REJECTED
            pending.decided_at = _now()
            pending.decided_by = admin.username
            pending.decision_note = "Closed: the user was promoted to administrator."
    elif role == "analyst":
        if primary_domain not in DOMAINS:
            raise HTTPException(status_code=422, detail="Choose the business user's primary domain.")
        if is_admin_account(account):
            remaining = db.query(func.count(AppUser.id)).filter(AppUser.domain == "all").scalar() or 0
            if remaining <= 1:
                raise HTTPException(status_code=400, detail="At least one administrator must remain.")
        account.domain = primary_domain
        # The new primary domain no longer needs a separate grant.
        db.query(UserDomainGrant).filter(
            UserDomainGrant.username == account.username,
            UserDomainGrant.domain == primary_domain,
        ).delete(synchronize_session=False)
    else:
        raise HTTPException(status_code=422, detail="Role must be 'admin' or 'analyst'.")
    current = "admin" if role == "admin" else f"analyst:{primary_domain}"
    _audit(db, admin.username, "role_changed", account.username, primary_domain, detail=f"{previous} -> {current}")
    db.commit()
    db.refresh(account)
    return account


def list_users(db: Session) -> list[dict[str, Any]]:
    accounts = db.query(AppUser).order_by(AppUser.username).all()
    grants: dict[str, list[UserDomainGrant]] = {}
    for grant in db.query(UserDomainGrant).order_by(UserDomainGrant.granted_at):
        grants.setdefault(grant.username, []).append(grant)
    tenants = {
        row.username
        for row in db.query(UserDomainTenant).filter(UserDomainTenant.domain == "healthcare")
    }
    google_linked = {row.username for row in db.query(OAuthIdentity.username)}
    pending_counts = dict(
        db.query(DomainAccessRequest.username, func.count(DomainAccessRequest.id))
        .filter(DomainAccessRequest.status == PENDING)
        .group_by(DomainAccessRequest.username)
        .all()
    )
    users = []
    for account in accounts:
        admin = is_admin_account(account)
        user_grants = grants.get(account.username, [])
        granted_domains = {grant.domain for grant in user_grants}
        users.append(
            {
                "username": account.username,
                "display_name": account.display_name,
                "role": "admin" if admin else "analyst",
                "primary_domain": None if admin else account.domain,
                "authorized_domains": list(DOMAINS) if admin else [
                    domain for domain in DOMAINS if domain == account.domain or domain in granted_domains
                ],
                "grants": [
                    {
                        "domain": grant.domain,
                        "label": _label(grant.domain),
                        "granted_by": grant.granted_by,
                        "granted_at": _iso(grant.granted_at),
                    }
                    for grant in user_grants
                ],
                "healthcare_tenant": account.username in tenants,
                "google_linked": account.username in google_linked,
                "pending_requests": int(pending_counts.get(account.username, 0)),
            }
        )
    return users


def list_audit(db: Session, limit: int = 200) -> list[dict[str, Any]]:
    rows = (
        db.query(AccessAuditEvent)
        .order_by(AccessAuditEvent.created_at.desc(), AccessAuditEvent.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "actor": row.actor,
            "action": row.action,
            "target_username": row.target_username,
            "domain": row.domain,
            "domain_label": _label(row.domain) if row.domain else None,
            "request_id": row.request_id,
            "detail": row.detail,
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


def admin_overview(db: Session) -> dict[str, Any]:
    users = list_users(db)
    return {
        "users": len(users),
        "admins": sum(1 for user in users if user["role"] == "admin"),
        "business_users": sum(1 for user in users if user["role"] != "admin"),
        "pending_requests": int(
            db.query(func.count(DomainAccessRequest.id)).filter(DomainAccessRequest.status == PENDING).scalar() or 0
        ),
        "domains": [
            {
                "id": domain,
                "label": _label(domain),
                "business_users": sum(
                    1 for user in users if user["role"] != "admin" and domain in user["authorized_domains"]
                ),
            }
            for domain in DOMAINS
        ],
        "recent_activity": list_audit(db, limit=8),
    }
