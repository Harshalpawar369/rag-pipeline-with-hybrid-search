"""Create demo users and matters, and optionally issue a session token.

    python -m scripts.seed
    python -m scripts.seed --token alice@example.com

Demo passwords come from SEED_PASSWORD (default: demo-password-123). Re-running does not reset
an existing password. Demo layout:
  alice: lead on "Acme MSA Review"
  bob:   member on "Acme MSA Review" but on its ethical wall (cannot see it), lead on "Beta NDA"
"""
from __future__ import annotations

import argparse
import asyncio
import os

from app.config import load_settings
from app.db import Database
from app.security.passwords import hash_password
from app.security.sessions import create_session

USERS = {"alice@example.com": "Alice Chen", "bob@example.com": "Bob Okafor"}


async def seed(db: Database, password: str) -> dict[str, str]:
    ids: dict[str, str] = {}
    for email, name in USERS.items():
        ids[email] = str(await db.fetchval(
            "INSERT INTO users (email, display_name, password_hash) VALUES ($1, $2, $3) "
            "ON CONFLICT (email) DO UPDATE SET display_name = EXCLUDED.display_name, "
            "password_hash = COALESCE(users.password_hash, EXCLUDED.password_hash) RETURNING id",
            email, name, hash_password(password)))

    async def matter(name: str, jurisdiction: str) -> str:
        existing = await db.fetchval("SELECT id FROM matters WHERE name = $1", name)
        if existing:
            return str(existing)
        return str(await db.fetchval(
            "INSERT INTO matters (name, jurisdiction) VALUES ($1, $2) RETURNING id", name, jurisdiction))

    acme = await matter("Acme MSA Review", "England and Wales")
    beta = await matter("Beta NDA", "New York")

    async def member(email: str, matter_id: str, role: str) -> None:
        await db.execute(
            "INSERT INTO matter_members (user_id, matter_id, role) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id, matter_id) DO UPDATE SET role = EXCLUDED.role",
            ids[email], matter_id, role)

    await member("alice@example.com", acme, "lead")
    await member("bob@example.com", acme, "member")
    await member("bob@example.com", beta, "lead")
    await db.execute(
        "INSERT INTO ethical_walls (user_id, matter_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        ids["bob@example.com"], acme)
    ids["matter:Acme MSA Review"] = acme
    ids["matter:Beta NDA"] = beta
    return ids


async def _run(args) -> None:
    s = load_settings()
    db = Database(s.migrate_dsn)
    await db.connect()
    try:
        ids = await seed(db, os.getenv("SEED_PASSWORD", "demo-password-123"))
        for key, value in ids.items():
            print(f"{key}: {value}")
        if args.token:
            if args.token not in USERS:
                raise SystemExit(f"unknown demo user {args.token}")
            token = await create_session(db, ids[args.token], s.session_ttl_hours)
            print(f"\nsession token for {args.token}:\n{token}")
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", metavar="EMAIL", help="issue a session token for this demo user")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
