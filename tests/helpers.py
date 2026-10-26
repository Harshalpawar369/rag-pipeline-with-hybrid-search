import asyncpg

from tests.conftest import MIGRATE_DSN


async def owner_conn():
    return await asyncpg.connect(MIGRATE_DSN)


async def create_user(email: str) -> str:
    conn = await owner_conn()
    try:
        return str(await conn.fetchval("INSERT INTO users (email) VALUES ($1) RETURNING id", email))
    finally:
        await conn.close()


async def create_matter(name: str, members: dict[str, str], walls: tuple[str, ...] = ()) -> str:
    """members maps user_id -> role. walls lists user_ids on the ethical wall."""
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
