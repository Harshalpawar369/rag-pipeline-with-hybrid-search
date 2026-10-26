"""Application factory. Dependencies are injectable so tests can swap in fakes."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as redis_asyncio
from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.auth import router as auth_router
from app.api.http import router as http_router
from app.api.ws import router as ws_router
from app.chat import ChatService
from app.config import Settings, load_settings
from app.db import Database
from app.ingestion.pipeline import IngestionService
from app.llm import make_chat
from app.memory.stm import ShortTermMemory
from app.retrieval.embeddings import make_embedder
from app.security.throttle import LoginThrottle

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(settings: Settings | None = None, *, embedder=None, llm=None, redis_client=None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.pg_dsn)
        await db.connect()
        redis = redis_client or redis_asyncio.from_url(settings.redis_url, decode_responses=True)
        emb = embedder or make_embedder(settings)
        model = llm or make_chat(settings)
        stm = ShortTermMemory(redis, turns=settings.stm_turns, ttl_seconds=settings.stm_ttl_seconds)
        ingest = IngestionService(db, emb, settings)
        await ingest.start()
        chat = ChatService(db=db, embedder=emb, llm=model, stm=stm, settings=settings)

        app.state.settings = settings
        app.state.db = db
        app.state.embedder = emb
        app.state.llm = model
        app.state.stm = stm
        app.state.ingest = ingest
        app.state.chat = chat
        app.state.login_throttle = LoginThrottle(settings.login_max_failures, settings.login_window_seconds)
        try:
            yield
        finally:
            await chat.shutdown()
            await ingest.stop()
            await db.close()
            if redis_client is None:
                await redis.aclose()

    app = FastAPI(title="Legal RAG", version="0.1.0", lifespan=lifespan)
    app.include_router(auth_router)
    app.include_router(http_router)
    app.include_router(ws_router)

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
