"""Chat model adapters.

OpenRouterChat streams from OpenRouter's OpenAI-compatible chat API, using a Qwen model
by default. OfflineChat is used when no API key is set. It quotes the top passage, so the
pipeline runs end to end without credentials.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import AsyncIterator

import httpx

log = logging.getLogger("llm")


class ModelError(RuntimeError):
    pass


class OpenRouterChat:
    def __init__(self, *, api_key: str, model: str, base_url: str = "https://openrouter.ai/api/v1",
                 site_url: str = "", app_name: str = "Legal RAG",
                 client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.site_url = site_url
        self.app_name = app_name
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0))

    def headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-Title"] = self.app_name
        return headers

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        body = {"model": self.model, "messages": messages, "stream": True, "temperature": 0.1}
        async with self.client.stream("POST", self.url, json=body, headers=self.headers()) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise ModelError(f"OpenRouter returned HTTP {resp.status_code}")
            async for line in resp.aiter_lines():
                # SSE comment lines (for example ": OPENROUTER PROCESSING") are skipped.
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                obj = json.loads(payload)
                if "error" in obj:
                    message = (obj.get("error") or {}).get("message", "unknown error")
                    raise ModelError(f"OpenRouter stream error: {message}")
                choices = obj.get("choices") or []
                if not choices:
                    continue
                piece = (choices[0].get("delta") or {}).get("content")
                if piece:
                    yield piece


class OfflineChat:
    _SOURCE = re.compile(r'<source id="(S\d+)"[^>]*>\n(.*?)\n</source>', re.S)

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        user_turn = messages[-1]["content"]
        match = self._SOURCE.search(user_turn)
        if match:
            snippet = " ".join(match.group(2).split())[:400]
            text = (f"Offline mode: no OpenRouter key is configured, so this shows the top-ranked "
                    f"passage instead. [{match.group(1)}] reads: \"{snippet}\". "
                    f"Check the source document before relying on it.")
        else:
            text = "Offline mode: no OpenRouter key is configured and no sources were found."
        for word in text.split(" "):
            yield word + " "
            await asyncio.sleep(0)


def make_chat(settings):
    if settings.openrouter_api_key:
        return OpenRouterChat(
            api_key=settings.openrouter_api_key, model=settings.openrouter_chat_model,
            base_url=settings.openrouter_base_url, site_url=settings.openrouter_site_url,
            app_name=settings.openrouter_app_name,
        )
    log.warning("OPENROUTER_API_KEY is not set: using the offline chat model (development only)")
    return OfflineChat()
