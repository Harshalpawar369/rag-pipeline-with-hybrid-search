"""Long-term memory: saved session summaries with embeddings, in Postgres.
A memory is retrieved only when all of its matters are inside the current scope,
so a summary of matter A never surfaces while the user is searching only matter B."""
from __future__ import annotations

import logging

from app.db import vec_literal

log = logging.getLogger("ltm")


async def search_memories(db, user_id: str, qvec: list[float], scope_ids: list[str], k: int = 3) -> list[str]:
    try:
        async with db.as_user(user_id) as conn:
            rows = await conn.fetch(
                """SELECT content FROM memories
                   WHERE matter_ids <@ $1::uuid[]
                   ORDER BY embedding <=> $2::vector
                   LIMIT $3""",
                scope_ids, vec_literal(qvec), k,
            )
        return [r["content"] for r in rows]
    except Exception:
        log.warning("memory lookup failed; continuing without memories", exc_info=True)
        return []


async def save_memory(db, user_id: str, session_id: str, scope_ids: list[str],
                      content: str, vec: list[float]) -> None:
    async with db.as_user(user_id) as conn:
        await conn.execute(
            """INSERT INTO memories (user_id, matter_ids, session_id, content, embedding)
               VALUES ($1, $2::uuid[], $3, $4, $5::vector)""",
            user_id, scope_ids, session_id, content, vec_literal(vec),
        )
