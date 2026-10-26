"""Turn orchestration. Each turn runs as its own task, so it can finish after
the socket drops. The answer is stored, and a reconnect with the same request id
gets the stored answer back instead of a second model call.

Order per turn: idempotency check, matter scope check, query embedding, then
hybrid retrieval, STM, and LTM run concurrently, then the model stream, then
persistence and audit. A turn counts as successful only after the stream finishes."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

from app import audit
from app.memory.ltm import search_memories
from app.prompts.answer import NO_RESULTS_ANSWER, build_messages, extract_citations
from app.retrieval.search import Passage, retrieve
from app.security.scope import ForbiddenMatter, require_visible

log = logging.getLogger("chat")
TOP_N = 8  # passages sent to the model after fusion
Emit = Callable[[dict], Awaitable[None]]


class InProgress(Exception):
    pass


class ChatService:
    def __init__(self, *, db, embedder, llm, stm, settings):
        self.db = db
        self.embedder = embedder
        self.llm = llm
        self.stm = stm
        self.settings = settings
        self._inflight: dict[tuple[str, str], asyncio.Task] = {}

    def start_turn(self, *, user_id: str, session_id: str, request_id: str, matter_ids: list[str],
                   question: str, include_original: bool, emit: Emit) -> asyncio.Task:
        key = (user_id, request_id)
        existing = self._inflight.get(key)
        if existing is not None and not existing.done():
            raise InProgress()
        task = asyncio.create_task(self._run(
            user_id=user_id, session_id=session_id, request_id=request_id, matter_ids=matter_ids,
            question=question, include_original=include_original, emit=emit,
        ))
        self._inflight[key] = task
        task.add_done_callback(lambda _t: self._inflight.pop(key, None))
        return task

    def cancel(self, user_id: str, request_id: str) -> bool:
        task = self._inflight.get((user_id, request_id))
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, **kwargs) -> None:
        emit: Emit = kwargs["emit"]
        request_id = kwargs["request_id"]
        try:
            await self._turn(**kwargs)
        except asyncio.CancelledError:
            raise
        except ForbiddenMatter as exc:
            await emit({"type": "error", "request_id": request_id, "code": "forbidden_matter",
                        "message": "You do not have access to one or more selected matters.",
                        "matter_ids": exc.matter_ids})
        except Exception:
            log.exception("turn failed", extra={"request_id": request_id})
            await emit({"type": "error", "request_id": request_id, "code": "internal_error",
                        "message": "The request failed. Please try again."})

    async def _turn(self, *, user_id, session_id, request_id, matter_ids, question,
                    include_original, emit) -> None:
        s = self.settings
        await require_visible(self.db, user_id, matter_ids)

        stored = await self._stored_answer(user_id, request_id)
        if stored is not None:
            if stored["content"]:
                await emit({"type": "token", "request_id": request_id, "text": stored["content"], "replay": True})
            if stored["status"] == "complete":
                await emit({"type": "done", "request_id": request_id,
                            "citations": stored["citations"], "replay": True})
            else:
                await emit({"type": "error", "request_id": request_id, "code": "interrupted",
                            "message": "This answer was interrupted. Ask again to get a new one."})
            return

        qvec = (await self.embedder.embed([question]))[0]
        passages, history, memories = await asyncio.gather(
            retrieve(self.db, user_id=user_id, matter_ids=matter_ids, query=question, qvec=qvec,
                     mode="hybrid", top_n=TOP_N,
                     candidates=s.retrieval_candidates, include_original=include_original,
                     ef_search=s.hnsw_ef_search),
            self.stm.load(user_id, session_id, matter_ids),
            search_memories(self.db, user_id, qvec, matter_ids),
        )

        if not passages:
            await emit({"type": "token", "request_id": request_id, "text": NO_RESULTS_ANSWER})
            await emit({"type": "done", "request_id": request_id, "citations": []})
            await self._finish(user_id, session_id, request_id, matter_ids, question,
                               NO_RESULTS_ANSWER, [], "complete", [])
            return

        messages, included = build_messages(question, passages, history, memories, s.max_context_chars)
        parts: list[str] = []
        try:
            async for piece in self.llm.stream(messages):
                parts.append(piece)
                await emit({"type": "token", "request_id": request_id, "text": piece})
        except asyncio.CancelledError:
            await self._finish(user_id, session_id, request_id, matter_ids, question,
                               "".join(parts), [], "interrupted", included)
            raise
        except Exception:
            log.exception("model stream failed", extra={"request_id": request_id})
            await emit({"type": "error", "request_id": request_id, "code": "model_error",
                        "message": "The model is unavailable. Please try again."})
            return

        answer = "".join(parts)
        citations = extract_citations(answer, included)
        await emit({"type": "done", "request_id": request_id, "citations": citations})
        await self._finish(user_id, session_id, request_id, matter_ids, question,
                           answer, citations, "complete", included)

    async def _stored_answer(self, user_id: str, request_id: str) -> dict | None:
        async with self.db.as_user(user_id) as conn:
            row = await conn.fetchrow(
                "SELECT content, citations, status FROM messages "
                "WHERE user_id = $1 AND request_id = $2 AND role = 'assistant'",
                user_id, request_id,
            )
        if row is None:
            return None
        return {"content": row["content"], "citations": json.loads(row["citations"]), "status": row["status"]}

    async def _finish(self, user_id, session_id, request_id, matter_ids, question,
                      answer, citations, status, passages: list[Passage]) -> None:
        async with self.db.as_user(user_id) as conn:
            await conn.execute(
                "INSERT INTO messages (user_id, session_id, request_id, role, content) "
                "VALUES ($1, $2, $3, 'user', $4) ON CONFLICT DO NOTHING",
                user_id, session_id, request_id, question,
            )
            await conn.execute(
                "INSERT INTO messages (user_id, session_id, request_id, role, content, citations, status) "
                "VALUES ($1, $2, $3, 'assistant', $4, $5::jsonb, $6) ON CONFLICT DO NOTHING",
                user_id, session_id, request_id, answer, json.dumps(citations), status,
            )
        await audit.record(
            self.db, user_id=user_id, action="chat", request_id=request_id,
            matter_ids=matter_ids, document_ids={p.document_id for p in passages},
        )
        if status == "complete":
            await self.stm.append(user_id, session_id, matter_ids, question=question, answer=answer)
