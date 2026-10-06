import argparse
from getpass import getpass

from app.auth import hash_password
from app.db import SessionLocal
from app.domains import DOMAINS
from app.models import AppUser


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a FD AIBI workspace user.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--domain", choices=(*DOMAINS, "all"), required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()

    password = getpass("Password (minimum 12 characters): ")
    confirmation = getpass("Confirm password: ")
    if len(password) < 12:
        parser.error("Password must contain at least 12 characters.")
    if password != confirmation:
        parser.error("Passwords do not match.")

    db = SessionLocal()
    try:
        if db.query(AppUser).filter(AppUser.username == args.username).first():
            parser.error(f"User '{args.username}' already exists.")
        db.add(
            AppUser(
                username=args.username,
                password_hash=hash_password(password),
                domain=args.domain,
                display_name=args.display_name,
            )
        )
        db.commit()
    finally:
        db.close()
    print(f"Created user '{args.username}' for domain '{args.domain}'.")


if __name__ == "__main__":
    main()
