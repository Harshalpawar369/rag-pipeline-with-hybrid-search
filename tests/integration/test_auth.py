"""Built-in FastAPI authentication: registration, OAuth2 password login, sessions, throttling."""
import asyncio
import dataclasses

import asyncpg
import pytest
import redis.asyncio as redis_asyncio
from fastapi.testclient import TestClient

from app.main import create_app
from app.retrieval.embeddings import HashEmbedder
from app.llm import OfflineChat
from tests.support import DEMO_PASSWORD, EMBED_DIM, MIGRATE_DSN, TEST_REDIS, create_user

NEW_PASSWORD = "a-long-enough-password"


@pytest.fixture
def app_and_settings(clean_db, settings):
    app = create_app(settings, embedder=HashEmbedder(EMBED_DIM), llm=OfflineChat(),
                     redis_client=redis_asyncio.from_url(TEST_REDIS, decode_responses=True))
    return app, settings


def _login(client, email, password):
    return client.post("/auth/token", data={"username": email, "password": password})


def test_register_login_me_and_logout(app_and_settings):
    app, _ = app_and_settings
    with TestClient(app) as client:
        resp = client.post("/auth/register", json={"email": "New.User@Example.com", "password": NEW_PASSWORD})
        assert resp.status_code == 201
        assert resp.json()["email"] == "new.user@example.com"  # normalized

        dup = client.post("/auth/register", json={"email": "new.user@example.com", "password": NEW_PASSWORD})
        assert dup.status_code == 409

        token_resp = _login(client, "new.user@example.com", NEW_PASSWORD)
        assert token_resp.status_code == 200
        body = token_resp.json()
        assert body["token_type"] == "bearer" and body["expires_in"] == 12 * 3600
        token = body["access_token"]

        me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200 and me.json()["email"] == "new.user@example.com"

        assert client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 204
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_registration_validates_input(app_and_settings):
    app, _ = app_and_settings
    with TestClient(app) as client:
        assert client.post("/auth/register", json={"email": "not-an-email", "password": NEW_PASSWORD}).status_code == 422
        assert client.post("/auth/register", json={"email": "a@b.co", "password": "short"}).status_code == 422


def test_wrong_password_and_unknown_user_look_identical(clean_db, app_and_settings):
    asyncio.run(create_user("known@auth.test", NEW_PASSWORD))
    app, _ = app_and_settings
    with TestClient(app) as client:
        wrong = _login(client, "known@auth.test", "definitely-wrong")
        unknown = _login(client, "nobody@auth.test", "definitely-wrong")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()
        assert wrong.headers["www-authenticate"] == "Bearer"


def test_repeated_failures_lock_the_account_even_with_the_right_password(clean_db, app_and_settings):
    asyncio.run(create_user("locked@auth.test", NEW_PASSWORD))
    app, settings = app_and_settings
    with TestClient(app) as client:
        for _ in range(settings.login_max_failures):
            assert _login(client, "locked@auth.test", "wrong").status_code == 401
        blocked = _login(client, "locked@auth.test", NEW_PASSWORD)
        assert blocked.status_code == 429


def test_successful_login_resets_the_failure_count(clean_db, app_and_settings):
    asyncio.run(create_user("reset@auth.test", NEW_PASSWORD))
    app, settings = app_and_settings
    with TestClient(app) as client:
        for _ in range(settings.login_max_failures - 1):
            _login(client, "reset@auth.test", "wrong")
        assert _login(client, "reset@auth.test", NEW_PASSWORD).status_code == 200
        for _ in range(settings.login_max_failures - 1):
            assert _login(client, "reset@auth.test", "wrong").status_code == 401


def test_expired_session_is_refused(clean_db, app_and_settings):
    asyncio.run(create_user("expiry@auth.test"))
    app, _ = app_and_settings
    with TestClient(app) as client:
        token = _login(client, "expiry@auth.test", DEMO_PASSWORD).json()["access_token"]
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200

        async def expire():
            conn = await asyncpg.connect(MIGRATE_DSN)
            try:
                await conn.execute("UPDATE auth_sessions SET expires_at = now() - interval '1 minute'")
            finally:
                await conn.close()
        asyncio.run(expire())

        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_session_tokens_are_stored_hashed(clean_db, app_and_settings):
    asyncio.run(create_user("hash@auth.test"))
    app, _ = app_and_settings
    with TestClient(app) as client:
        token = _login(client, "hash@auth.test", DEMO_PASSWORD).json()["access_token"]

    async def stored_hashes():
        conn = await asyncpg.connect(MIGRATE_DSN)
        try:
            return [r["token_hash"] for r in await conn.fetch("SELECT token_hash FROM auth_sessions")]
        finally:
            await conn.close()
    hashes = asyncio.run(stored_hashes())
    assert hashes and token not in hashes and all(len(h) == 64 for h in hashes)


def test_registration_can_be_disabled(clean_db, settings):
    closed = dataclasses.replace(settings, allow_registration=False)
    app = create_app(closed, embedder=HashEmbedder(EMBED_DIM), llm=OfflineChat(),
                     redis_client=redis_asyncio.from_url(TEST_REDIS, decode_responses=True))
    with TestClient(app) as client:
        resp = client.post("/auth/register", json={"email": "x@y.co", "password": NEW_PASSWORD})
        assert resp.status_code == 403
