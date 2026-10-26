"""Session tokens are random and only their hash is persisted. A fake database records
what reaches the SQL layer, so these tests need no Postgres."""
import asyncio

from app.security.sessions import create_session, hash_token, new_session_token, resolve_session, revoke_session


class FakeDB:
    def __init__(self, user_id=None):
        self.user_id = user_id
        self.executed: list[tuple] = []
        self.fetch_args: tuple | None = None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "OK"

    async def fetchval(self, sql, *args):
        self.fetch_args = args
        return self.user_id


def test_tokens_are_random_and_long_enough():
    tokens = {new_session_token() for _ in range(500)}
    assert len(tokens) == 500
    assert all(len(t) >= 40 for t in tokens)


def test_hash_is_stable_hex_and_does_not_contain_the_token():
    token = new_session_token()
    digest = hash_token(token)
    assert digest == hash_token(token)
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    assert token not in digest


def test_create_session_persists_only_the_hash():
    db = FakeDB()
    token = asyncio.run(create_session(db, "user-1", ttl_hours=2))
    sql, args = db.executed[0]
    assert "INSERT INTO auth_sessions" in sql
    assert token not in args
    assert hash_token(token) in args
    assert args[2] == 2


def test_resolve_queries_by_hash_and_skips_empty_tokens():
    db = FakeDB(user_id="11111111-1111-1111-1111-111111111111")
    assert asyncio.run(resolve_session(db, "")) is None
    assert db.fetch_args is None  # no query for an empty token

    token = "supplied-token-value"
    assert asyncio.run(resolve_session(db, token)) == "11111111-1111-1111-1111-111111111111"
    assert db.fetch_args == (hash_token(token),)
    assert token not in db.fetch_args


def test_revoke_deletes_by_hash():
    db = FakeDB()
    asyncio.run(revoke_session(db, "abc"))
    sql, args = db.executed[0]
    assert sql.startswith("DELETE FROM auth_sessions")
    assert args == (hash_token("abc"),)
