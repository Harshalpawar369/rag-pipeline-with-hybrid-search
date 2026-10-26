"""Embedding providers.

OpenRouterEmbedder calls OpenRouter's /embeddings endpoint with a Qwen embedding model.
The `dimensions` parameter (Matryoshka truncation) keeps vectors within the pgvector HNSW
limit of 2000 dimensions. Qwen3 embedding 4B produces 2560 natively, so 1024 is requested.
If a provider ignores `dimensions`, the size check below fails loudly instead of storing bad vectors.

HashEmbedder is offline and deterministic. It does lexical feature hashing, so it is for
development and tests only.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re

import httpx

log = logging.getLogger("embeddings")


class EmbeddingError(RuntimeError):
    pass


class HashEmbedder:
    def __init__(self, dim: int):
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        vec = [0.0] * self.dim
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class OpenRouterEmbedder:
    def __init__(self, *, api_key: str, model: str, dim: int,
                 base_url: str = "https://openrouter.ai/api/v1", site_url: str = "",
                 app_name: str = "Legal RAG", batch_size: int = 64,
                 client: httpx.AsyncClient | None = None, sleep=asyncio.sleep):
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self.url = base_url.rstrip("/") + "/embeddings"
        self.site_url = site_url
        self.app_name = app_name
        self.batch_size = batch_size
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
        self._sleep = sleep

    def headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-Title"] = self.app_name
        return headers

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            out.extend(await self._post(texts[start:start + self.batch_size]))
        return out

    async def _post(self, batch: list[str]) -> list[list[float]]:
        payload = {"model": self.model, "input": batch, "dimensions": self.dim}
        for attempt in range(4):
            resp = await self.client.post(self.url, json=payload, headers=self.headers())
            if resp.status_code in (429, 500, 502, 503, 504):
                await self._sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise EmbeddingError(f"OpenRouter embeddings returned HTTP {resp.status_code}")
            items = sorted(resp.json()["data"], key=lambda d: d["index"])
            vectors = [item["embedding"] for item in items]
            if len(vectors) != len(batch):
                raise EmbeddingError("embedding count does not match the request")
            if any(len(v) != self.dim for v in vectors):
                raise EmbeddingError(
                    f"embedding size is {len(vectors[0])}, expected EMBED_DIM={self.dim}; "
                    "the provider may ignore the dimensions parameter for this model")
            return vectors
        raise EmbeddingError("OpenRouter embeddings unavailable after retries")


def make_embedder(settings):
    if settings.openrouter_api_key:
        return OpenRouterEmbedder(
            api_key=settings.openrouter_api_key, model=settings.openrouter_embed_model,
            dim=settings.embed_dim, base_url=settings.openrouter_base_url,
            site_url=settings.openrouter_site_url, app_name=settings.openrouter_app_name,
        )
    log.warning("OPENROUTER_API_KEY is not set: using the offline hash embedder (development only)")
    return HashEmbedder(settings.embed_dim)
