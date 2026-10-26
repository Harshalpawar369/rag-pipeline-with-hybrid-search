"""Row-level security. These tests confirm that the database enforces matter scoping,
not just the application code."""
import asyncio

import asyncpg
import pytest

from app.db import Database, vec_literal
from app.security.scope import visible_matters, writable_matters
from tests.support import APP_DSN
from tests.support import create_matter, create_user, owner_conn


async def _seed_chunk(conn, matter_id: str, user_id: str, title: str, text: str) -> None:
    doc = await conn.fetchval(
        "INSERT INTO documents (matter_id, uploaded_by, title, doc_type, content_hash, blob_path, status) "
        "VALUES ($1, $2, $3, 'docx', md5($3), 'x', 'indexed') RETURNING id", matter_id, user_id, title)
    ver = await conn.fetchval(
        "INSERT INTO document_versions (document_id, matter_id, view_label) VALUES ($1, $2, 'accepted') RETURNING id",
        doc, matter_id)
    await conn.execute(
        "INSERT INTO chunks (document_id, version_id, matter_id, ordinal, view_label, content, embedding) "
        "VALUES ($1, $2, $3, 0, 'accepted', $4, $5::vector)",
        doc, ver, matter_id, text, vec_literal([0.0] * 383 + [1.0]))


def test_membership_scopes_reads_and_default_denies(clean_db):
    async def scenario():
        alice = await create_user("alice@t.test")
        carol = await create_user("carol@t.test")
        acme = await create_matter("Acme", {alice: "lead"})
        beta = await create_matter("Beta", {carol: "lead"})
        conn = await owner_conn()
        await _seed_chunk(conn, acme, alice, "acme.docx", "acme confidential clause")
        await _seed_chunk(conn, beta, carol, "beta.docx", "beta confidential clause")
        await conn.close()

        db = Database(APP_DSN)
        await db.connect()
        try:
            assert await visible_matters(db, alice) == {acme}
            async with db.as_user(alice) as c:
                titles = {r["title"] for r in await c.fetch(
                    "SELECT d.title FROM chunks ch JOIN documents d ON d.id = ch.document_id")}
            assert titles == {"acme.docx"}

            raw = await asyncpg.connect(APP_DSN)  # no app.user_id set
            try:
                assert await raw.fetchval("SELECT count(*) FROM chunks") == 0
                assert await raw.fetchval("SELECT count(*) FROM matters") == 0
            finally:
                await raw.close()
        finally:
            await db.close()

    asyncio.run(scenario())


def test_ethical_wall_hides_matter_despite_membership(clean_db):
    async def scenario():
        bob = await create_user("bob@t.test")
        acme = await create_matter("Acme", {bob: "member"}, walls=(bob,))
        db = Database(APP_DSN)
        await db.connect()
        try:
            assert await visible_matters(db, bob) == set()
            assert await writable_matters(db, bob) == set()
            # An accidental lookup by ID also finds nothing.
            async with db.as_user(bob) as c:
                assert await c.fetchval("SELECT count(*) FROM matters WHERE id = $1", acme) == 0
        finally:
            await db.close()

    asyncio.run(scenario())


def test_viewer_cannot_write_and_matter_id_is_checked(clean_db):
    async def scenario():
        viewer = await create_user("viewer@t.test")
        acme = await create_matter("Acme", {viewer: "viewer"})
        db = Database(APP_DSN)
        await db.connect()
        try:
            assert await visible_matters(db, viewer) == {acme}
            assert await writable_matters(db, viewer) == set()
            with pytest.raises(asyncpg.PostgresError):
                async with db.as_user(viewer) as c:
                    await c.execute(
                        "INSERT INTO documents (matter_id, uploaded_by, title, doc_type, content_hash, blob_path) "
                        "VALUES ($1, $2, 'x', 'pdf', 'h', 'p')", acme, viewer)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_app_role_cannot_rewrite_audit_log(clean_db):
    async def scenario():
        user = await create_user("audit@t.test")
        raw = await asyncpg.connect(APP_DSN)
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await raw.execute("UPDATE audit_log SET action = 'x' WHERE user_id = $1", user)
        finally:
            await raw.close()

    asyncio.run(scenario())
