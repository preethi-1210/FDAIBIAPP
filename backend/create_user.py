import argparse
from getpass import getpass

from app.auth import hash_password
from app.db import SessionLocal
from app.domains import DOMAINS
from app.models import AppUser, UserDomainTenant


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a FD AI/BI workspace user.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--domain", choices=(*DOMAINS, "all"), required=True)
    parser.add_argument("--tenant-id", help="Required for an administrator-provisioned Healthcare user.")
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    if args.domain == "healthcare" and not args.tenant_id:
        parser.error("--tenant-id is required for Healthcare users.")
    if args.domain != "healthcare" and args.tenant_id:
        parser.error("--tenant-id can only be used with the Healthcare domain.")

    password = getpass("Password (minimum 12 characters): ")
    confirmation = getpass("Confirm password: ")
    if len(password) < 12:
        parser.error("Password must contain at least 12 characters.")
    if password != confirmation:
        parser.error("Passwords do not match.")

    db = SessionLocal()
    username = args.username.strip().lower()
    try:
        if db.query(AppUser).filter(AppUser.username == username).first():
            parser.error(f"User '{username}' already exists.")
        db.add(
            AppUser(
                username=username,
                password_hash=hash_password(password),
                domain=args.domain,
                display_name=args.display_name,
            )
        )
        if args.domain == "healthcare":
            db.flush()
            db.add(
                UserDomainTenant(
                    username=username,
                    domain="healthcare",
                    tenant_id=args.tenant_id,
                )
            )
        db.commit()
    finally:
        db.close()
    print(f"Created user '{username}' for domain '{args.domain}'.")


if __name__ == "__main__":
    main()
