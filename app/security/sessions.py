"""Opaque session tokens. The client gets a random string. The database keeps only its SHA-256,
so a leaked table cannot be replayed. Sessions expire, and logout deletes them."""
from __future__ import annotations

import hashlib
import secrets


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_session(db, user_id: str, ttl_hours: int) -> str:
    token = new_session_token()
    await db.execute(
        "INSERT INTO auth_sessions (user_id, token_hash, expires_at) "
        "VALUES ($1, $2, now() + make_interval(hours => $3::int))",
        str(user_id), hash_token(token), ttl_hours,
    )
    return token


async def resolve_session(db, token: str) -> str | None:
    """Return the user id for a live session, or None for unknown, expired, or empty tokens."""
    if not token:
        return None
    user_id = await db.fetchval(
        "SELECT user_id FROM auth_sessions WHERE token_hash = $1 AND expires_at > now()",
        hash_token(token),
    )
    return str(user_id) if user_id is not None else None


async def revoke_session(db, token: str) -> None:
    await db.execute("DELETE FROM auth_sessions WHERE token_hash = $1", hash_token(token))
