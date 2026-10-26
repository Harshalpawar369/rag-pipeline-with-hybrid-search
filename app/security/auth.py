"""FastAPI built-in OAuth2 password flow with opaque session tokens.

Endpoints that need a user depend on current_user. It reads the bearer token with
OAuth2PasswordBearer, then checks the session in the database.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer

from app.security.sessions import resolve_session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


async def current_user(request: Request, token: str = Depends(oauth2_scheme)) -> str:
    user_id = await resolve_session(request.app.state.db, token)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id
