"""Fixtures for tests that need a real Postgres and Redis."""
import asyncio
import dataclasses

import asyncpg
import pytest

from app.config import load_settings
from scripts.migrate import apply_migrations
from tests.support import APP_DSN, EMBED_DIM, MIGRATE_DSN, TEST_REDIS


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    asyncio.run(apply_migrations(MIGRATE_DSN, dim=EMBED_DIM))


@pytest.fixture
def clean_db(migrated_db):
    async def wipe():
        conn = await asyncpg.connect(MIGRATE_DSN)
        try:
            await conn.execute(
                "TRUNCATE audit_log, messages, memories, chunks, document_versions, documents, "
                "ethical_walls, matter_members, matters, auth_sessions, users RESTART IDENTITY CASCADE")
        finally:
            await conn.close()
    asyncio.run(wipe())
    yield


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # Clear OpenRouter credentials so integration tests can never reach the network.
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    return dataclasses.replace(
        load_settings(),
        pg_dsn=APP_DSN,
        migrate_dsn=MIGRATE_DSN,
        redis_url=TEST_REDIS,
        embed_dim=EMBED_DIM,
        blob_dir=tmp_path / "blobs",
        ingest_retry_backoff=0.01,
        ws_asks_per_minute=50,
        openrouter_api_key="",
        allow_registration=True,
        login_max_failures=5,
        login_window_seconds=900.0,
        session_ttl_hours=12,
    )
