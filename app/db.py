"""Connection pool plus per-user transactions.

Row-level security reads the caller from the transaction-local setting
`app.user_id`. Every user-facing query goes through `as_user`, so the
policies in the migrations decide what rows are visible or writable.
"""
from __future__ import annotations

import contextlib
from typing import Any, AsyncIterator

import asyncpg


class Database:
    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 10):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn, min_size=self.min_size, max_size=self.max_size)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()

    async def execute(self, sql: str, *args: Any) -> str:
        """Run a statement on tables without RLS (audit log, sessions, health checks)."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def fetchrow(self, sql: str, *args: Any):
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchval(sql, *args)

    @contextlib.asynccontextmanager
    async def as_user(self, user_id: str) -> AsyncIterator[asyncpg.Connection]:
        assert self.pool is not None, "Database.connect() was not called"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.user_id', $1, true)", str(user_id))
                yield conn


def vec_literal(values: list[float]) -> str:
    """Format a vector for pgvector: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"
