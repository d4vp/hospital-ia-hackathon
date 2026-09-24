"""Create an admin user from the command line.

Usage (inside backend/):  python -m app.scripts.create_admin --email admin@hospital.local --name "Admin"
The password is asked interactively so it never ends up in the shell history.
"""
import argparse
import asyncio
import getpass

from app.db.mongo import get_async_db
from app.services import user_service


async def _main(email: str, name: str, role: str) -> None:
    password = getpass.getpass("Password (min 10 chars, letters + numbers/symbols): ")
    db = get_async_db()
    await user_service.ensure_indexes(db)
    user = await user_service.create_user(db, email, password, name, role)
    print(f"Created {user['role']} {user['email']} (id {user['id']})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="Administrator")
    parser.add_argument("--role", default="admin", choices=["admin", "user"])
    args = parser.parse_args()
    asyncio.run(_main(args.email, args.name, args.role))
