"""WebSocket chat endpoint.

Handshake: the session token goes in the query string (browsers cannot set headers on a
WebSocket). It is the same opaque token the REST endpoints accept. The Origin header is checked against ALLOWED_ORIGINS. Identity comes
only from the token, never from message fields. Frame size, schema, and asks per
minute are all limited.

Server frames: ack, token, done, error, pong. An ask gets an ack first, then tokens,
then done. A cancel stops generation. If the socket drops, the turn keeps running
and stores its result, so a reconnect with the same request_id gets the answer."""
from __future__ import annotations

import asyncio
import collections
import time

from fastapi import APIRouter, WebSocket
from pydantic import ValidationError

from app.api.models import client_frame_adapter
from app.chat import InProgress
from app.security.sessions import resolve_session

router = APIRouter()
MAX_FRAME_BYTES = 16_384


class _RateLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self.hits: collections.deque[float] = collections.deque()

    def allow(self) -> bool:
        now = time.monotonic()
        while self.hits and now - self.hits[0] > self.window:
            self.hits.popleft()
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(now)
        return True


@router.websocket("/ws")
async def chat_socket(ws: WebSocket) -> None:
    settings = ws.app.state.settings
    chat = ws.app.state.chat

    origin = ws.headers.get("origin")
    if origin is not None and origin not in settings.allowed_origins:
        await ws.close(code=4403)
        return
    user_id = await resolve_session(ws.app.state.db, ws.query_params.get("token", ""))
    if user_id is None:
        await ws.close(code=4401)
        return

    await ws.accept()
    lock = asyncio.Lock()

    async def emit(frame: dict) -> None:
        async with lock:
            try:
                await ws.send_json(frame)
            except Exception:
                # Socket is gone. The turn still stores its answer for replay on reconnect.
                pass

    limiter = _RateLimiter(settings.ws_asks_per_minute, 60.0)
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return
        raw = message.get("text")
        if raw is None:
            await emit({"type": "error", "code": "invalid_frame", "message": "Text frames only"})
            continue
        if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
            await emit({"type": "error", "code": "frame_too_large", "message": "Message too large"})
            await ws.close(code=1009)
            return
        try:
            frame = client_frame_adapter.validate_json(raw)
        except ValidationError:
            await emit({"type": "error", "code": "invalid_frame", "message": "Invalid message"})
            continue

        if frame.type == "ping":
            await emit({"type": "pong"})
            continue
        if frame.type == "cancel":
            chat.cancel(user_id, frame.request_id)
            continue

        if not limiter.allow():
            await emit({"type": "error", "request_id": frame.request_id, "code": "rate_limited",
                        "message": "Too many questions. Wait a moment and try again."})
            continue
        await emit({"type": "ack", "request_id": frame.request_id})
        try:
            chat.start_turn(
                user_id=user_id, session_id=frame.session_id, request_id=frame.request_id,
                matter_ids=[str(m) for m in frame.matter_ids], question=frame.question,
                include_original=frame.include_original, emit=emit,
            )
        except InProgress:
            await emit({"type": "error", "request_id": frame.request_id, "code": "in_progress",
                        "message": "This request is still running."})
