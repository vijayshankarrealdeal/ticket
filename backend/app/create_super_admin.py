"""Create or reset a platform super-user (and optionally bootstrap an org).

Usage (from `backend/`):

  # interactive — prompts for email + password
  python -m app.create_super_admin

  # non-interactive — pass everything on the CLI
  python -m app.create_super_admin --email cto@acme.com --password 's3cret!' --name 'Acme CTO'

  # also create an initial organization in one shot
  python -m app.create_super_admin --email cto@acme.com --password 's3cret!' --org 'Acme Corp'

  # idempotent reset — overwrites the password if the email already exists
  python -m app.create_super_admin --email cto@acme.com --password 'newpw' --reset

The script connects via the same DATABASE_URL the FastAPI app uses, so it
"just works" inside the docker-compose dev stack as well as locally.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import re
import secrets
import sys
from uuid import uuid4

from passlib.hash import argon2

from .db import close_db, get_db


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "org"
    return base[:40] + "-" + secrets.token_hex(3)


async def maybe_create_org(org_name: str | None) -> str | None:
    """Create a workspace with the given name and return its id. Returns None
    if no name was provided. Multiple orgs with the same name are allowed —
    the slug carries a random suffix to keep the unique constraint happy."""
    if not org_name or not org_name.strip():
        return None
    db = await get_db()
    ws_id = str(uuid4())
    await db.execute(
        "INSERT INTO workspaces (id, name, slug) VALUES (?,?,?)",
        (ws_id, org_name.strip(), _slugify(org_name)),
    )
    await db.commit()
    print(f"✓ Created organization {org_name!r} (id: {ws_id})")
    return ws_id


async def upsert(email: str, password: str, name: str, reset: bool) -> None:
    db = await get_db()
    cur = await db.execute(
        "SELECT id, email FROM platform_admins WHERE email = ?", (email.lower(),)
    )
    existing = await cur.fetchone()
    pw_hash = argon2.hash(password)
    if existing:
        if not reset:
            print(
                f"✗ A super-user with email {email!r} already exists. "
                f"Re-run with --reset to overwrite the password.",
                file=sys.stderr,
            )
            sys.exit(2)
        await db.execute(
            "UPDATE platform_admins SET password_hash = ?, name = ? WHERE email = ?",
            (pw_hash, name, email.lower()),
        )
        # Drop any open sessions — forces a re-login with the new password.
        admin_id = existing["id"] if isinstance(existing, dict) else existing[0]
        await db.execute(
            "DELETE FROM platform_sessions WHERE admin_id = ?", (admin_id,)
        )
        await db.commit()
        print(f"✓ Reset super-user {email}. Existing sessions invalidated.")
    else:
        await db.execute(
            "INSERT INTO platform_admins (id, name, email, password_hash) VALUES (?,?,?,?)",
            (str(uuid4()), name, email.lower(), pw_hash),
        )
        await db.commit()
        print(f"✓ Created super-user {email}. Sign in at /platform.")


def _prompt_password() -> str:
    pw = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm:  ")
    if pw != confirm:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    if len(pw) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)
    return pw


async def main() -> None:
    parser = argparse.ArgumentParser(description="Create or reset a WorkspaceX platform super-user.")
    parser.add_argument("--email", help="Super-user email")
    parser.add_argument("--password", help="Super-user password (omit to be prompted)")
    parser.add_argument("--name", default="Platform Admin", help="Display name (default: 'Platform Admin')")
    parser.add_argument("--reset", action="store_true",
                        help="Overwrite the password if a super-user with this email already exists")
    parser.add_argument("--org", help="Optionally create a workspace with this name in the same run")
    args = parser.parse_args()

    email = (args.email or input("Email: ")).strip()
    if not email or "@" not in email:
        print("A valid email is required.", file=sys.stderr)
        sys.exit(1)

    password = args.password or _prompt_password()
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    try:
        await upsert(email, password, args.name, args.reset)
        await maybe_create_org(args.org)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
