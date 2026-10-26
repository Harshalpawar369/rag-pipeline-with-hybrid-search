"""Hybrid retrieval: pgvector cosine search and Postgres full-text search,
fused with Reciprocal Rank Fusion. Both queries run concurrently, each on its
own RLS-scoped transaction, and both are filtered to the requested matters.

By default only the 'accepted' view is searched, so deleted language is not
returned as current terms. Set include_original to search the original view too."""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from dataclasses import dataclass

from app.db import vec_literal

RRF_K = 60

_COLUMNS = (
    "c.id, c.document_id, d.title, c.matter_id, c.content, "
    "c.section_path, c.page_start, c.page_end, c.view_label"
)


@dataclass(frozen=True)
class Passage:
    chunk_id: int
    document_id: str
    title: str
    matter_id: str
    content: str
    section_path: str
    page_start: int | None
    page_end: int | None
    view_label: str


def _to_passage(r) -> Passage:
    return Passage(
        chunk_id=r["id"], document_id=str(r["document_id"]), title=r["title"],
        matter_id=str(r["matter_id"]), content=r["content"], section_path=r["section_path"],
        page_start=r["page_start"], page_end=r["page_end"], view_label=r["view_label"],
    )


def keyword_query(text: str) -> str:
    """Build an OR-of-terms tsquery from a natural-language question.
    Tokens are restricted to alphanumerics, so the query syntax cannot be injected.
    OR matches the way BM25-style ranking works: ts_rank_cd rewards chunks that cover more terms.
    Stopwords are dropped by the english configuration."""
    tokens = [t for t in re.findall(r"[A-Za-z0-9]+", text) if len(t) >= 2]
    return " | ".join(tokens)


def rrf_fuse(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion. Uses ranks only, so no score normalization is needed."""
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


async def vector_search(db, user_id: str, matter_ids: list[str], qvec: list[float],
                        limit: int, include_original: bool, ef_search: int) -> list[Passage]:
    async with db.as_user(user_id) as conn:
        # Filtered HNSW can return fewer rows than asked for. A larger ef_search helps.
        await conn.execute("SELECT set_config('hnsw.ef_search', $1, true)", str(ef_search))
        rows = await conn.fetch(
            f"""SELECT {_COLUMNS}
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.matter_id = ANY($1::uuid[])
                  AND (c.view_label = 'accepted' OR $2)
                ORDER BY c.embedding <=> $3::vector
                LIMIT $4""",
            matter_ids, include_original, vec_literal(qvec), limit,
        )
    return [_to_passage(r) for r in rows]


async def keyword_search(db, user_id: str, matter_ids: list[str], query: str,
                         limit: int, include_original: bool) -> list[Passage]:
    async with db.as_user(user_id) as conn:
        rows = await conn.fetch(
            f"""SELECT {_COLUMNS}
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.tsv @@ to_tsquery('english', $1)
                  AND c.matter_id = ANY($2::uuid[])
                  AND (c.view_label = 'accepted' OR $3)
                ORDER BY ts_rank_cd(c.tsv, to_tsquery('english', $1)) DESC
                LIMIT $4""",
            keyword_query(query), matter_ids, include_original, limit,
        )
    return [_to_passage(r) for r in rows]


async def retrieve(db, *, user_id: str, matter_ids: list[str], query: str,
                   qvec: list[float] | None, mode: str = "hybrid", top_n: int = 8,
                   candidates: int = 40, include_original: bool = False,
                   ef_search: int = 100) -> list[Passage]:
    if not matter_ids:
        return []
    if mode in ("vector", "hybrid") and qvec is None:
        raise ValueError("a query embedding is required for vector and hybrid search")

    coros = {}
    if mode in ("vector", "hybrid"):
        coros["vector"] = vector_search(db, user_id, matter_ids, qvec, candidates,
                                        include_original, ef_search)
    if mode in ("keyword", "hybrid"):
        coros["keyword"] = keyword_search(db, user_id, matter_ids, query, candidates, include_original)
    if not coros:
        raise ValueError(f"unknown retrieval mode: {mode}")

    results = dict(zip(coros.keys(), await asyncio.gather(*coros.values())))
    by_id: dict[int, Passage] = {}
    rankings: list[list[int]] = []
    for name in ("vector", "keyword"):
        if name in results:
            rankings.append([p.chunk_id for p in results[name]])
            for p in results[name]:
                by_id.setdefault(p.chunk_id, p)
    fused = rrf_fuse(rankings)
    return [by_id[chunk_id] for chunk_id, _ in fused[:top_n]]
