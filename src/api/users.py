"""Create dashboard users and reset passwords from the command line.

Usage:
    python -m src.api.users create --email a@b.com --role admin [--tenant-id 1] [--name "Ali"]
    python -m src.api.users set-password --email a@b.com
The password comes from NEW_USER_PASSWORD or is asked for; it is never an argument, so it
stays out of the shell history. A user without --tenant-id is platform staff.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from src.api import auth
from src.api.repository import ConflictError, NotFoundError, PostgresRepository
from src.common.db import connect
from src.config import require_database_url


def read_password() -> str:
    password = os.getenv("NEW_USER_PASSWORD")
    if password:
        return password
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat: "):
        sys.exit("passwords do not match")
    return password


def set_password(url: str, email: str, password_hash: str) -> bool:
    with connect(url) as conn:
        updated = conn.execute(
            "UPDATE users SET password_hash = %s WHERE email = %s", (password_hash, email)
        ).rowcount
        # A new password signs the user out everywhere.
        conn.execute(
            "DELETE FROM sessions WHERE user_id IN (SELECT user_id FROM users WHERE email = %s)",
            (email,),
        )
    return bool(updated)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--email", required=True)
    create.add_argument("--role", choices=auth.ROLES, required=True)
    create.add_argument("--tenant-id", type=int)
    create.add_argument("--name")
    reset = sub.add_parser("set-password")
    reset.add_argument("--email", required=True)
    args = parser.parse_args()

    url = require_database_url()
    email = args.email.lower()
    try:
        password_hash = auth.hash_password(read_password())
    except ValueError as exc:
        sys.exit(str(exc))
    if args.command == "create":
        try:
            user_id = PostgresRepository(url).create_user(
                args.tenant_id, email, args.name, args.role, password_hash
            )
        except (ConflictError, NotFoundError) as exc:
            sys.exit(str(exc))
        print(f"user {user_id} created: {email} ({args.role})")
    elif not set_password(url, email, password_hash):
        sys.exit(f"no user with e-mail {email}")
    else:
        print(f"password changed for {email}")


if __name__ == "__main__":
    main()
