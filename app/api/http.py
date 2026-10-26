"""REST endpoints: matters, document upload and status, search, session summaries."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from app import audit
from app.api.models import SearchRequest, SummarizeRequest
from app.ingestion.pipeline import IngestJob
from app.ingestion.validate import sniff_type
from app.memory.ltm import save_memory
from app.prompts.answer import SUMMARY_PROMPT
from app.retrieval.search import retrieve
from app.security.auth import current_user
from app.security.scope import ForbiddenMatter, require_visible, visible_matters, writable_matters

router = APIRouter()


def _passage_dict(p) -> dict:
    return {
        "chunk_id": p.chunk_id, "document_id": p.document_id, "title": p.title,
        "matter_id": p.matter_id, "section_path": p.section_path, "page_start": p.page_start,
        "page_end": p.page_end, "view_label": p.view_label, "content": p.content,
    }


def _document_dict(r) -> dict:
    return {
        "id": str(r["id"]), "title": r["title"], "doc_type": r["doc_type"], "status": r["status"],
        "page_count": r["page_count"], "error_code": r["error_code"], "error_message": r["error_message"],
        "warnings": json.loads(r["warnings"]) if isinstance(r["warnings"], str) else r["warnings"],
        "created_at": r["created_at"].isoformat(),
    }


@router.get("/health")
async def health(request: Request):
    await request.app.state.db.execute("SELECT 1")
    return {"status": "ok"}


@router.get("/me/matters")
async def my_matters(request: Request, user: str = Depends(current_user)):
    async with request.app.state.db.as_user(user) as conn:
        rows = await conn.fetch(
            """SELECT m.id, m.name, m.jurisdiction, mm.role
               FROM matters m JOIN matter_members mm ON mm.matter_id = m.id AND mm.user_id = $1
               ORDER BY m.name""",
            user,
        )
    return [{"id": str(r["id"]), "name": r["name"], "jurisdiction": r["jurisdiction"], "role": r["role"]}
            for r in rows]


@router.post("/matters/{matter_id}/documents", status_code=202)
async def upload_document(matter_id: UUID, request: Request, file: UploadFile,
                          user: str = Depends(current_user)):
    st = request.app.state
    mid = str(matter_id)
    if mid not in await visible_matters(st.db, user):
        raise HTTPException(404, "Matter not found")
    if mid not in await writable_matters(st.db, user):
        raise HTTPException(403, "Viewers cannot upload to this matter")

    limit = st.settings.max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File exceeds {st.settings.max_upload_mb} MB")
    if not data:
        raise HTTPException(400, "Empty file")
    doc_type = sniff_type(data[:8])
    if doc_type is None:
        raise HTTPException(415, "Only PDF and DOCX files are accepted")

    content_hash = hashlib.sha256(data).hexdigest()
    async with st.db.as_user(user) as conn:
        existing = await conn.fetchrow(
            "SELECT id, status FROM documents WHERE matter_id = $1 AND content_hash = $2 "
            "AND status IN ('pending', 'processing', 'indexed')",
            mid, content_hash,
        )
    if existing is not None:
        return JSONResponse({"id": str(existing["id"]), "status": existing["status"], "duplicate": True})

    doc_id = str(uuid4())
    blob_rel = f"{mid}/{doc_id}.{doc_type}"
    path: Path = st.settings.blob_dir / blob_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    title = Path(file.filename or "untitled").name[:200] or "untitled"
    try:
        async with st.db.as_user(user) as conn:
            await conn.execute(
                "INSERT INTO documents (id, matter_id, uploaded_by, title, doc_type, content_hash, blob_path) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                doc_id, mid, user, title, doc_type, content_hash, blob_rel,
            )
    except asyncpg.UniqueViolationError:
        path.unlink(missing_ok=True)
        raise HTTPException(409, "This file is already being indexed in this matter")
    except Exception:
        path.unlink(missing_ok=True)
        raise

    try:
        st.ingest.enqueue(IngestJob(doc_id, mid, user, blob_rel, doc_type))
    except asyncio.QueueFull:
        async with st.db.as_user(user) as conn:
            await conn.execute("DELETE FROM documents WHERE id = $1", doc_id)
        path.unlink(missing_ok=True)
        raise HTTPException(503, "Ingestion is busy. Try again shortly.")
    return JSONResponse({"id": doc_id, "status": "pending"}, status_code=202)


@router.get("/matters/{matter_id}/documents")
async def list_documents(matter_id: UUID, request: Request, user: str = Depends(current_user)):
    async with request.app.state.db.as_user(user) as conn:
        rows = await conn.fetch(
            "SELECT * FROM documents WHERE matter_id = $1 ORDER BY created_at DESC", str(matter_id))
    return [_document_dict(r) for r in rows]


@router.get("/documents/{document_id}")
async def get_document(document_id: UUID, request: Request, user: str = Depends(current_user)):
    async with request.app.state.db.as_user(user) as conn:
        row = await conn.fetchrow("SELECT * FROM documents WHERE id = $1", str(document_id))
    if row is None:
        raise HTTPException(404, "Document not found")
    return _document_dict(row)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: UUID, request: Request, user: str = Depends(current_user)):
    st = request.app.state
    async with st.db.as_user(user) as conn:
        row = await conn.fetchrow("DELETE FROM documents WHERE id = $1 RETURNING blob_path", str(document_id))
    if row is None:
        raise HTTPException(404, "Document not found")
    # Chunks and versions cascade. Delete the original file too.
    (st.settings.blob_dir / row["blob_path"]).unlink(missing_ok=True)
    return Response(status_code=204)


@router.post("/search")
async def search(body: SearchRequest, request: Request, user: str = Depends(current_user)):
    st = request.app.state
    ids = [str(m) for m in body.matter_ids]
    try:
        await require_visible(st.db, user, ids)
    except ForbiddenMatter:
        raise HTTPException(403, "Access denied to one or more matters")
    qvec = (await st.embedder.embed([body.query]))[0] if body.mode != "keyword" else None
    passages = await retrieve(
        st.db, user_id=user, matter_ids=ids, query=body.query, qvec=qvec, mode=body.mode,
        top_n=body.top_n, candidates=st.settings.retrieval_candidates,
        include_original=body.include_original, ef_search=st.settings.hnsw_ef_search,
    )
    await audit.record(st.db, user_id=user, action="search", matter_ids=ids,
                       document_ids={p.document_id for p in passages})
    return [_passage_dict(p) for p in passages]


@router.post("/sessions/{session_id}/summarize")
async def summarize_session(session_id: str, body: SummarizeRequest, request: Request,
                            user: str = Depends(current_user)):
    st = request.app.state
    ids = [str(m) for m in body.matter_ids]
    try:
        await require_visible(st.db, user, ids)
    except ForbiddenMatter:
        raise HTTPException(403, "Access denied to one or more matters")
    turns = await st.stm.load(user, session_id, ids)
    if not turns:
        raise HTTPException(404, "No recent conversation to summarize for this scope")
    transcript = "\n".join(f"{t['role']}: {t['content']}" for t in turns)
    messages = [{"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": transcript}]
    summary = "".join([piece async for piece in st.llm.stream(messages)]).strip()
    vec = (await st.embedder.embed([summary]))[0]
    await save_memory(st.db, user, session_id, ids, summary, vec)
    return {"saved": True, "summary": summary}
