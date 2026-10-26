"""Built-in FastAPI authentication.

OAuth2PasswordBearer reads the bearer token and drives the "Authorize" button in /docs.
OAuth2PasswordRequestForm parses the username and password form at POST /auth/token.
Sessions are opaque tokens (see app/security/sessions.py), not JWTs.
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from app.security.auth import current_user, oauth2_scheme
from app.security.passwords import DUMMY_HASH, hash_password, verify_password
from app.security.sessions import create_session, revoke_session

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegisterRequest(BaseModel):
    email: str = Field(pattern=_EMAIL, max_length=254)
    password: str = Field(min_length=10, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)


@router.post("/register", status_code=201)
async def register(body: RegisterRequest, request: Request):
    st = request.app.state
    if not st.settings.allow_registration:
        raise HTTPException(403, "Registration is disabled")
    email = body.email.strip().lower()
    try:
        row = await st.db.fetchrow(
            "INSERT INTO users (email, display_name, password_hash) VALUES ($1, $2, $3) RETURNING id",
            email, body.display_name, hash_password(body.password),
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "An account with this email already exists")
    return {"id": str(row["id"]), "email": email}


@router.post("/token")
async def login(request: Request, form: OAuth2PasswordRequestForm = Depends()):
    st = request.app.state
    email = form.username.strip().lower()
    if st.login_throttle.is_blocked(email):
        raise HTTPException(429, "Too many failed sign-in attempts. Try again later.")

    row = await st.db.fetchrow("SELECT id, password_hash FROM users WHERE email = $1", email)
    has_password = row is not None and row["password_hash"] is not None
    # Always run one verification so response timing does not reveal which emails exist.
    matched = verify_password(form.password, row["password_hash"] if has_password else DUMMY_HASH)
    if not (has_password and matched):
        st.login_throttle.record_failure(email)
        raise HTTPException(401, "Incorrect email or password",
                            headers={"WWW-Authenticate": "Bearer"})

    st.login_throttle.reset(email)
    ttl = st.settings.session_ttl_hours
    token = await create_session(st.db, str(row["id"]), ttl)
    return {"access_token": token, "token_type": "bearer", "expires_in": ttl * 3600}


@router.post("/logout", status_code=204)
async def logout(request: Request, token: str = Depends(oauth2_scheme)):
    await revoke_session(request.app.state.db, token)
    return None


@router.get("/me")
async def me(request: Request, user_id: str = Depends(current_user)):
    row = await request.app.state.db.fetchrow(
        "SELECT id, email, display_name FROM users WHERE id = $1", user_id)
    if row is None:
        raise HTTPException(401, "Account no longer exists")
    return {"id": str(row["id"]), "email": row["email"], "display_name": row["display_name"]}

