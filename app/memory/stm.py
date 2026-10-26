"""Short-term memory: recent turns in Redis, keyed by user, session, and the matter scope,
so a conversation never carries context from matters the user is not searching now.
A Redis outage degrades to no history instead of failing the turn."""
from __future__ import annotations

import hashlib
import json
import logging

log = logging.getLogger("stm")


class ShortTermMemory:
    def __init__(self, redis, *, turns: int, ttl_seconds: int):
        self.redis = redis
        self.turns = turns
        self.ttl = ttl_seconds

    @staticmethod
    def key(user_id: str, session_id: str, matter_ids: list[str]) -> str:
        scope = hashlib.sha256(",".join(sorted(matter_ids)).encode()).hexdigest()[:16]
        return f"stm:{user_id}:{session_id}:{scope}"

    async def load(self, user_id: str, session_id: str, matter_ids: list[str]) -> list[dict]:
        try:
            raw = await self.redis.lrange(self.key(user_id, session_id, matter_ids), -self.turns * 2, -1)
        except Exception:
            log.warning("STM read failed; continuing without history", exc_info=True)
            return []
        return [json.loads(item) for item in raw]

    async def append(self, user_id: str, session_id: str, matter_ids: list[str],
                     *, question: str, answer: str) -> None:
        key = self.key(user_id, session_id, matter_ids)
        try:
            pipe = self.redis.pipeline()
            pipe.rpush(key,
                       json.dumps({"role": "user", "content": question}),
                       json.dumps({"role": "assistant", "content": answer}))
            pipe.ltrim(key, -self.turns * 2, -1)
            pipe.expire(key, self.ttl)
            await pipe.execute()
        except Exception:
            log.warning("STM write failed", exc_info=True)
