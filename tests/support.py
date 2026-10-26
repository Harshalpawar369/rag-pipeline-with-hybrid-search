"""Shared constants and database helpers for the test suites."""
from __future__ import annotations

import os

import asyncpg

from app.security.passwords import hash_password

EMBED_DIM = 384
MIGRATE_DSN = os.getenv("TEST_MIGRATE_DSN", "postgresql://postgres:postgres@localhost:5432/legalrag_test")
APP_DSN = os.getenv("TEST_PG_DSN", "postgresql://legalrag_app:legalrag_app@localhost:5432/legalrag_test")
TEST_REDIS = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")
DEMO_PASSWORD = "correct-horse-battery"


async def owner_conn() -> asyncpg.Connection:
    return await asyncpg.connect(MIGRATE_DSN)


async def create_user(email: str, password: str = DEMO_PASSWORD) -> str:
    conn = await owner_conn()
    try:
        return str(await conn.fetchval(
            "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING id",
            email, hash_password(password)))
    finally:
        await conn.close()


async def create_matter(name: str, members: dict[str, str], walls: tuple[str, ...] = ()) -> str:
    """members maps user_id to role. walls lists user_ids on the matter's ethical wall."""
    conn = await owner_conn()
    try:
        matter_id = str(await conn.fetchval("INSERT INTO matters (name) VALUES ($1) RETURNING id", name))
        for user_id, role in members.items():
            await conn.execute(
                "INSERT INTO matter_members (user_id, matter_id, role) VALUES ($1, $2, $3)",
                user_id, matter_id, role)
        for user_id in walls:
            await conn.execute("INSERT INTO ethical_walls (user_id, matter_id) VALUES ($1, $2)", user_id, matter_id)
        return matter_id
    finally:
        await conn.close()
