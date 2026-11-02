import argparse

from app.db import SessionLocal
from app.domains import DOMAINS
from app.models import AccessAuditEvent, AppUser, UserDomainGrant
from app.seed import ensure_schema


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Server-side provisioning of FD AI/BI roles. Use it to create the first administrator "
            "(including promoting a Google sign-in account); later changes can be made in the Admin UI."
        )
    )
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=("admin", "analyst"), required=True)
    parser.add_argument("--primary-domain", choices=DOMAINS, help="Required when setting the analyst role.")
    args = parser.parse_args()
    if args.role == "analyst" and not args.primary_domain:
        parser.error("--primary-domain is required for the analyst role.")
    if args.role == "admin" and args.primary_domain:
        parser.error("--primary-domain only applies to the analyst role.")

    ensure_schema()
    db = SessionLocal()
    username = args.username.strip().lower()
    try:
        account = db.query(AppUser).filter(AppUser.username == username).one_or_none()
        if account is None:
            parser.error(f"User '{username}' does not exist.")
        previous = "admin" if account.domain == "all" else f"analyst:{account.domain}"
        if args.role == "analyst" and account.domain == "all":
            admins = db.query(AppUser).filter(AppUser.domain == "all").count()
            if admins <= 1:
                parser.error("At least one administrator must remain.")
        account.domain = "all" if args.role == "admin" else args.primary_domain
        if args.role == "admin":
            db.query(UserDomainGrant).filter(UserDomainGrant.username == username).delete(synchronize_session=False)
        current = "admin" if args.role == "admin" else f"analyst:{args.primary_domain}"
        db.add(
            AccessAuditEvent(
                actor="cli:set_user_role",
                action="role_changed",
                target_username=username,
                domain=args.primary_domain,
                detail=f"{previous} -> {current}",
            )
        )
        db.commit()
    finally:
        db.close()
    print(f"User '{username}' is now {current}.")


if __name__ == "__main__":
    main()
