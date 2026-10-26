"""Retrieval evaluation harness.

Dataset: JSONL, one item per line:
  {"question": "...", "matter_ids": ["<uuid>"],
   "gold": {"document_title": "...", "contains": "clause text that must appear"}}

A retrieved passage counts as a hit when its document title matches and its
text contains the gold snippet (whitespace and case normalized). The harness
reports Recall@5, Recall@10, and MRR for each mode, so the README table can be
filled from real runs. Build the dataset from CUAD's labeled clauses, or from
other public data. Do not use client documents.

    python -m eval.run_eval eval/datasets/cuad.jsonl --user-id <uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def is_hit(passage, gold: dict) -> bool:
    return passage.title == gold["document_title"] and _norm(gold["contains"]) in _norm(passage.content)


def rank_of_first_hit(passages: list, gold: dict) -> int | None:
    for rank, passage in enumerate(passages, start=1):
        if is_hit(passage, gold):
            return rank
    return None


def score(ranks: list[int | None]) -> dict[str, float]:
    total = len(ranks) or 1
    return {
        "recall@5": sum(1 for r in ranks if r is not None and r <= 5) / total,
        "recall@10": sum(1 for r in ranks if r is not None and r <= 10) / total,
        "mrr": sum((1 / r) for r in ranks if r is not None) / total,
        "n": float(len(ranks)),
    }


async def evaluate(items: list[dict], retrieve_fn: Callable[[str, dict], Awaitable[list]],
                   modes: tuple[str, ...] = ("vector", "keyword", "hybrid")) -> dict[str, dict[str, float]]:
    results: dict[str, dict[str, float]] = {}
    for mode in modes:
        ranks = []
        for item in items:
            passages = await retrieve_fn(mode, item)
            ranks.append(rank_of_first_hit(passages, item["gold"]))
        results[mode] = score(ranks)
    return results


def to_markdown(results: dict[str, dict[str, float]]) -> str:
    lines = ["| Mode | Recall@5 | Recall@10 | MRR | Items |", "|---|---|---|---|---|"]
    for mode, m in results.items():
        lines.append(f"| {mode} | {m['recall@5']:.3f} | {m['recall@10']:.3f} | {m['mrr']:.3f} | {int(m['n'])} |")
    return "\n".join(lines)


def load_dataset(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def _main(args) -> None:
    from app.config import load_settings
    from app.db import Database
    from app.retrieval.embeddings import make_embedder
    from app.retrieval.search import retrieve

    s = load_settings()
    db = Database(s.pg_dsn)
    await db.connect()
    embedder = make_embedder(s)

    async def retrieve_fn(mode: str, item: dict):
        qvec = None if mode == "keyword" else (await embedder.embed([item["question"]]))[0]
        return await retrieve(
            db, user_id=args.user_id, matter_ids=item["matter_ids"], query=item["question"],
            qvec=qvec, mode=mode, top_n=10, candidates=args.candidates,
            ef_search=s.hnsw_ef_search,
        )

    try:
        results = await evaluate(load_dataset(Path(args.dataset)), retrieve_fn)
        print(to_markdown(results))
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset")
    parser.add_argument("--user-id", required=True, help="user whose matter access is used")
    parser.add_argument("--candidates", type=int, default=40)
    asyncio.run(_main(parser.parse_args()))
