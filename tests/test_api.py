"""HTTP and WebSocket behavior through the real app, with workers running."""
import asyncio
import time

import pytest
import redis.asyncio as redis_asyncio
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.llm import OfflineChat
from app.main import create_app
from app.retrieval.embeddings import HashEmbedder
from app.security.auth import issue_token
from scripts.samples import build_sample_docx, build_sample_pdf
from tests.conftest import EMBED_DIM, TEST_REDIS
from tests.helpers import create_matter, create_user


@pytest.fixture
def world(clean_db, settings, tmp_path):
    async def build():
        alice = await create_user("alice@api.test")
        bob = await create_user("bob@api.test")
        carol = await create_user("carol@api.test")
        acme = await create_matter("Acme", {alice: "lead", bob: "viewer"})
        beta = await create_matter("Beta", {carol: "lead"})
        return {"alice": alice, "bob": bob, "carol": carol, "acme": acme, "beta": beta}
    ids = asyncio.run(build())
    app = create_app(settings, embedder=HashEmbedder(EMBED_DIM), llm=OfflineChat(),
                     redis_client=redis_asyncio.from_url(TEST_REDIS, decode_responses=True))
    return ids, app, settings


def _wait_for_status(client, token, doc_id, want, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/documents/{doc_id}", headers=_auth(token)).json()
        if body["status"] in (want, "rejected", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"document never reached {want}")


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_upload_index_and_chat_with_citations(world, tmp_path):
    ids, app, settings = world
    alice_token = issue_token(ids["alice"], settings)
    with TestClient(app) as client:
        with open(build_sample_docx(tmp_path / "msa.docx"), "rb") as fh:
            resp = client.post(f"/matters/{ids['acme']}/documents", headers=_auth(alice_token),
                               files={"file": ("msa.docx", fh, "application/octet-stream")})
        assert resp.status_code == 202
        doc = _wait_for_status(client, alice_token, resp.json()["id"], "indexed")
        assert doc["status"] == "indexed", doc

        with client.websocket_connect(f"/ws?token={alice_token}") as ws:
            ws.send_json({"type": "ask", "request_id": "r1", "session_id": "s1",
                          "matter_ids": [ids["acme"]], "question": "What is the term of the agreement?"})
            frames = []
            while True:
                frame = ws.receive_json()
                frames.append(frame)
                if frame["type"] in ("done", "error"):
                    break
        assert frames[0]["type"] == "ack"
        assert any(f["type"] == "token" for f in frames)
        done = frames[-1]
        assert done["type"] == "done"
        assert done["citations"] and done["citations"][0]["section_path"]

        time.sleep(0.3)  # let the turn finish persisting before replaying it
        with client.websocket_connect(f"/ws?token={alice_token}") as ws:
            ws.send_json({"type": "ask", "request_id": "r1", "session_id": "s1",
                          "matter_ids": [ids["acme"]], "question": "What is the term of the agreement?"})
            ws.receive_json()  # ack
            replayed = ws.receive_json()
            assert replayed.get("replay") is True


def test_forbidden_matter_is_refused_over_websocket(world):
    ids, app, settings = world
    carol_token = issue_token(ids["carol"], settings)
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={carol_token}") as ws:
            ws.send_json({"type": "ask", "request_id": "r2", "session_id": "s",
                          "matter_ids": [ids["acme"]], "question": "anything"})
            ws.receive_json()  # ack
            frame = ws.receive_json()
            assert frame["type"] == "error" and frame["code"] == "forbidden_matter"


def test_bad_token_and_bad_origin_are_refused(world):
    ids, app, settings = world
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?token=not-a-token"):
                pass
        token = issue_token(ids["alice"], settings)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws?token={token}", headers={"origin": "https://evil.example"}):
                pass


def test_viewer_cannot_upload_and_bad_types_are_rejected(world, tmp_path):
    ids, app, settings = world
    bob_token = issue_token(ids["bob"], settings)
    alice_token = issue_token(ids["alice"], settings)
    with TestClient(app) as client:
        pdf = build_sample_pdf(tmp_path / "nda.pdf").read_bytes()
        resp = client.post(f"/matters/{ids['acme']}/documents", headers=_auth(bob_token),
                           files={"file": ("nda.pdf", pdf, "application/pdf")})
        assert resp.status_code == 403

        resp = client.post(f"/matters/{ids['acme']}/documents", headers=_auth(alice_token),
                           files={"file": ("evil.exe", b"MZ\x90\x00 not a document", "application/octet-stream")})
        assert resp.status_code == 415

        resp = client.post(f"/matters/{ids['beta']}/documents", headers=_auth(alice_token),
                           files={"file": ("nda.pdf", pdf, "application/pdf")})
        assert resp.status_code == 404  # not visible to alice, so the matter is not revealed


def test_duplicate_upload_returns_existing_document(world, tmp_path):
    ids, app, settings = world
    token = issue_token(ids["alice"], settings)
    data = build_sample_docx(tmp_path / "msa.docx").read_bytes()
    with TestClient(app) as client:
        first = client.post(f"/matters/{ids['acme']}/documents", headers=_auth(token),
                            files={"file": ("msa.docx", data, "application/octet-stream")}).json()
        second = client.post(f"/matters/{ids['acme']}/documents", headers=_auth(token),
                             files={"file": ("copy.docx", data, "application/octet-stream")}).json()
        assert second["duplicate"] is True and second["id"] == first["id"]
