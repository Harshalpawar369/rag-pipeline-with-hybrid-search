"""OpenRouter chat and embedding clients, exercised with httpx.MockTransport. No network."""
import asyncio
import dataclasses
import json

import httpx
import pytest

from app.config import load_settings
from app.llm import ModelError, OfflineChat, OpenRouterChat, make_chat
from app.retrieval.embeddings import EmbeddingError, HashEmbedder, OpenRouterEmbedder, make_embedder

SSE_BODY = (
    b": OPENROUTER PROCESSING\n\n"
    b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"lo [S1]"}}]}\n\n'
    b"data: [DONE]\n\n"
)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _collect(agen):
    return [piece async for piece in agen]


# ---- chat -------------------------------------------------------------------

def test_chat_streams_content_and_skips_sse_comments():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=SSE_BODY, headers={"content-type": "text/event-stream"})

    chat = OpenRouterChat(api_key="sk-test", model="qwen/qwen-2.5-72b-instruct", client=_client(handler))
    pieces = asyncio.run(_collect(chat.stream([{"role": "user", "content": "hi"}])))
    assert "".join(pieces) == "Hello [S1]"
    assert seen["path"] == "/api/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "qwen/qwen-2.5-72b-instruct"
    assert seen["body"]["stream"] is True


def test_chat_sends_attribution_headers_when_configured():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    chat = OpenRouterChat(api_key="k", model="m", site_url="https://app.example", app_name="Legal RAG",
                          client=_client(handler))
    asyncio.run(_collect(chat.stream([{"role": "user", "content": "x"}])))
    assert seen["http-referer"] == "https://app.example"
    assert seen["x-title"] == "Legal RAG"


def test_chat_http_error_raises_without_leaking_the_key():
    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    chat = OpenRouterChat(api_key="sk-secret-value", model="m", client=_client(handler))
    with pytest.raises(ModelError) as exc:
        asyncio.run(_collect(chat.stream([{"role": "user", "content": "x"}])))
    assert "401" in str(exc.value)
    assert "sk-secret-value" not in str(exc.value)


def test_chat_error_event_inside_the_stream_is_raised():
    body = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\ndata: {"error":{"message":"rate limited"}}\n\n'

    def handler(request):
        return httpx.Response(200, content=body)

    chat = OpenRouterChat(api_key="k", model="m", client=_client(handler))
    with pytest.raises(ModelError, match="rate limited"):
        asyncio.run(_collect(chat.stream([{"role": "user", "content": "x"}])))


def test_offline_chat_quotes_the_top_source_and_says_so():
    user = '<sources>\n<source id="S1" document="a.docx" clause="1.2" pages="n/a" view="accepted">\nThe term is three years.\n</source>\n</sources>\n\nQuestion: term?'
    pieces = asyncio.run(_collect(OfflineChat().stream([{"role": "user", "content": user}])))
    text = "".join(pieces)
    assert "[S1]" in text and "three years" in text and "Offline mode" in text


def test_offline_chat_without_sources_says_so():
    pieces = asyncio.run(_collect(OfflineChat().stream([{"role": "user", "content": "Question: x"}])))
    assert "no sources" in "".join(pieces)


# ---- embeddings -------------------------------------------------------------

def test_embedder_requests_dimensions_and_returns_vectors_in_input_order():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        # Providers may return items out of order; the client sorts them by index.
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [0.0, 1.0, 0.0, 0.0]},
            {"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]},
        ]})

    emb = OpenRouterEmbedder(api_key="k", model="qwen/qwen3-embedding-4b", dim=4, client=_client(handler))
    vectors = asyncio.run(emb.embed(["first", "second"]))
    assert vectors == [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    assert seen["path"] == "/api/v1/embeddings"
    assert seen["body"]["dimensions"] == 4
    assert seen["body"]["input"] == ["first", "second"]


def test_embedder_rejects_vectors_of_the_wrong_size():
    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 4096}]})

    emb = OpenRouterEmbedder(api_key="k", model="m", dim=1024, client=_client(handler))
    with pytest.raises(EmbeddingError, match="EMBED_DIM=1024"):
        asyncio.run(emb.embed(["text"]))


def test_embedder_retries_transient_errors_then_succeeds():
    calls = {"n": 0}
    delays = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    async def fake_sleep(seconds):
        delays.append(seconds)

    emb = OpenRouterEmbedder(api_key="k", model="m", dim=2, client=_client(handler), sleep=fake_sleep)
    assert asyncio.run(emb.embed(["x"])) == [[1.0, 0.0]]
    assert calls["n"] == 3 and delays == [1, 2]


def test_embedder_gives_up_after_bounded_retries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429)

    async def no_sleep(_):
        return None

    emb = OpenRouterEmbedder(api_key="k", model="m", dim=2, client=_client(handler), sleep=no_sleep)
    with pytest.raises(EmbeddingError, match="after retries"):
        asyncio.run(emb.embed(["x"]))
    assert calls["n"] == 4


def test_embedder_batches_requests():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        items = json.loads(request.content)["input"]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [0.0, 1.0]} for i in range(len(items))]})

    emb = OpenRouterEmbedder(api_key="k", model="m", dim=2, batch_size=2, client=_client(handler))
    assert len(asyncio.run(emb.embed(["a", "b", "c", "d", "e"]))) == 5
    assert calls["n"] == 3


def test_authorization_header_is_sent_on_embeddings():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    emb = OpenRouterEmbedder(api_key="sk-embed", model="m", dim=1, client=_client(handler))
    asyncio.run(emb.embed(["x"]))
    assert seen["auth"] == "Bearer sk-embed"


# ---- factories --------------------------------------------------------------

def test_factories_fall_back_to_offline_without_a_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    settings = dataclasses.replace(load_settings(), openrouter_api_key="", embed_dim=32)
    assert isinstance(make_chat(settings), OfflineChat)
    assert isinstance(make_embedder(settings), HashEmbedder)


def test_factories_select_openrouter_when_a_key_is_set():
    settings = dataclasses.replace(load_settings(), openrouter_api_key="sk-x", embed_dim=32)
    assert isinstance(make_chat(settings), OpenRouterChat)
    assert isinstance(make_embedder(settings), OpenRouterEmbedder)
