"""Runtime configuration, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class Settings:
    pg_dsn: str
    migrate_dsn: str
    redis_url: str
    openrouter_api_key: str
    openrouter_base_url: str
    openrouter_chat_model: str
    openrouter_embed_model: str
    openrouter_site_url: str
    openrouter_app_name: str
    embed_dim: int
    blob_dir: Path
    max_upload_bytes: int
    max_upload_mb: int
    max_pages: int
    ingest_queue_size: int
    ingest_workers: int
    ingest_retry_backoff: float
    stm_turns: int
    stm_ttl_seconds: int
    retrieval_candidates: int
    hnsw_ef_search: int
    max_context_chars: int
    ws_asks_per_minute: int
    session_ttl_hours: int
    allow_registration: bool
    login_max_failures: int
    login_window_seconds: float
    allowed_origins: tuple[str, ...]


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    origins = tuple(
        o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",") if o.strip()
    )
    max_mb = _int("MAX_UPLOAD_MB", 5)
    return Settings(
        pg_dsn=os.getenv("PG_DSN", "postgresql://legalrag_app:legalrag_app@localhost:5432/legalrag"),
        migrate_dsn=os.getenv("MIGRATE_DSN", "postgresql://postgres:postgres@localhost:5432/legalrag"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL).rstrip("/"),
        openrouter_chat_model=os.getenv("OPENROUTER_CHAT_MODEL", "qwen/qwen-2.5-72b-instruct"),
        openrouter_embed_model=os.getenv("OPENROUTER_EMBED_MODEL", "qwen/qwen3-embedding-4b"),
        openrouter_site_url=os.getenv("OPENROUTER_SITE_URL", ""),
        openrouter_app_name=os.getenv("OPENROUTER_APP_NAME", "Legal RAG"),
        embed_dim=_int("EMBED_DIM", 1024),
        blob_dir=Path(os.getenv("BLOB_DIR", "./data/blobs")),
        max_upload_bytes=max_mb * 1024 * 1024,
        max_upload_mb=max_mb,
        max_pages=_int("MAX_PAGES", 200),
        ingest_queue_size=_int("INGEST_QUEUE_SIZE", 200),
        ingest_workers=_int("INGEST_WORKERS", 2),
        ingest_retry_backoff=_float("INGEST_RETRY_BACKOFF", 2.0),
        stm_turns=_int("STM_TURNS", 6),
        stm_ttl_seconds=_int("STM_TTL_SECONDS", 3600),
        retrieval_candidates=_int("RETRIEVAL_CANDIDATES", 40),
        hnsw_ef_search=_int("HNSW_EF_SEARCH", 100),
        max_context_chars=_int("MAX_CONTEXT_CHARS", 12000),
        ws_asks_per_minute=_int("WS_ASKS_PER_MINUTE", 20),
        session_ttl_hours=_int("SESSION_TTL_HOURS", 12),
        allow_registration=_bool("ALLOW_REGISTRATION", True),
        login_max_failures=_int("LOGIN_MAX_FAILURES", 5),
        login_window_seconds=_float("LOGIN_WINDOW_SECONDS", 900.0),
        allowed_origins=origins,
    )
