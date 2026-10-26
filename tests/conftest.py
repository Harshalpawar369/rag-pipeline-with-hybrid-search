import asyncio
import dataclasses
import os

import asyncpg
import pytest

from app.config import load_settings
from scripts.migrate import apply_migrations

EMBED_DIM = 384
MIGRATE_DSN = os.getenv("TEST_MIGRATE_DSN", "postgresql://postgres:postgres@localhost:5432/legalrag_test")
APP_DSN = os.getenv("TEST_PG_DSN", "postgresql://legalrag_app:legalrag_app@localhost:5432/legalrag_test")
TEST_REDIS = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")


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
                "ethical_walls, matter_members, matters, users RESTART IDENTITY CASCADE")
        finally:
            await conn.close()
    asyncio.run(wipe())
    yield


@pytest.fixture
def settings(tmp_path):
    base = load_settings()
    return dataclasses.replace(
        base,
        pg_dsn=APP_DSN,
        migrate_dsn=MIGRATE_DSN,
        redis_url=TEST_REDIS,
        embed_dim=EMBED_DIM,
        blob_dir=tmp_path / "blobs",
        ingest_retry_backoff=0.01,
        ws_asks_per_minute=50,
    )
