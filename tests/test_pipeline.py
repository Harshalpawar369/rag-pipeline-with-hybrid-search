"""End-to-end ingestion and retrieval, with the offline embedder and the real database."""
import asyncio
import shutil
import uuid

import asyncpg
from reportlab.pdfgen import canvas

from app.db import Database
from app.ingestion.pipeline import IngestionService, IngestJob
from app.retrieval.embeddings import HashEmbedder
from app.retrieval.search import retrieve
from scripts.samples import build_sample_docx
from tests.conftest import APP_DSN, EMBED_DIM
from tests.helpers import create_matter, create_user


async def _create_doc(db, settings, user, matter, src_path, doc_type):
    doc_id = str(uuid.uuid4())
    rel = f"{matter}/{doc_id}.{doc_type}"
    dest = settings.blob_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src_path, dest)
    async with db.as_user(user) as c:
        await c.execute(
            "INSERT INTO documents (id, matter_id, uploaded_by, title, doc_type, content_hash, blob_path) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            doc_id, matter, user, src_path.name, doc_type, uuid.uuid4().hex, rel)
    return IngestJob(doc_id, matter, user, rel, doc_type)


def test_tracked_change_document_is_indexed_and_filtered(clean_db, settings, tmp_path):
    async def scenario():
        alice = await create_user("alice@p.test")
        acme = await create_matter("Acme", {alice: "lead"})
        db = Database(APP_DSN)
        await db.connect()
        try:
            ingest = IngestionService(db, HashEmbedder(EMBED_DIM), settings)
            job = await _create_doc(db, settings, alice, acme, build_sample_docx(tmp_path / "msa.docx"), "docx")
            assert await ingest.process(job) == "indexed"

            qvec = (await HashEmbedder(EMBED_DIM).embed(["term of the agreement"]))[0]
            hits = await retrieve(db, user_id=alice, matter_ids=[acme], query="term three years",
                                  qvec=qvec, mode="hybrid", top_n=5)
            assert any("three (3) years" in h.content and h.view_label == "accepted" for h in hits)

            # The default search must not return deleted language as current terms.
            hits = await retrieve(db, user_id=alice, matter_ids=[acme], query="two years",
                                  qvec=None, mode="keyword", top_n=5)
            assert hits and all("two (2) years" not in h.content for h in hits)

            hits = await retrieve(db, user_id=alice, matter_ids=[acme], query="two years",
                                  qvec=None, mode="keyword", top_n=5, include_original=True)
            assert any(h.view_label == "original" for h in hits)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_reprocessing_is_idempotent(clean_db, settings, tmp_path):
    async def scenario():
        alice = await create_user("alice@p.test")
        acme = await create_matter("Acme", {alice: "lead"})
        db = Database(APP_DSN)
        await db.connect()
        try:
            ingest = IngestionService(db, HashEmbedder(EMBED_DIM), settings)
            job = await _create_doc(db, settings, alice, acme, build_sample_docx(tmp_path / "msa.docx"), "docx")
            await ingest.process(job)
            first = await _count_chunks(job.document_id)
            await ingest.process(job)
            assert await _count_chunks(job.document_id) == first
        finally:
            await db.close()

    asyncio.run(scenario())


def test_blank_pdf_is_rejected_and_mixed_pdf_skips_blank_pages(clean_db, settings, tmp_path):
    async def scenario():
        alice = await create_user("alice@p.test")
        acme = await create_matter("Acme", {alice: "lead"})
        db = Database(APP_DSN)
        await db.connect()
        try:
            ingest = IngestionService(db, HashEmbedder(EMBED_DIM), settings)

            blank = tmp_path / "scan.pdf"
            c = canvas.Canvas(str(blank))
            c.showPage()
            c.save()
            rejected = await _create_doc(db, settings, alice, acme, blank, "pdf")
            assert await ingest.process(rejected) == "rejected"
            assert await _status(rejected.document_id) == ("rejected", "scanned_unsupported")

            mixed = tmp_path / "mixed.pdf"
            c = canvas.Canvas(str(mixed))
            c.drawString(72, 780, "1.1 The receiving party shall keep all information strictly confidential.")
            c.showPage()
            c.showPage()
            c.save()
            job = await _create_doc(db, settings, alice, acme, mixed, "pdf")
            assert await ingest.process(job) == "indexed"
            async with db.as_user(alice) as conn:
                warnings = await conn.fetchval("SELECT warnings::text FROM documents WHERE id = $1", job.document_id)
            assert "skipped" in warnings
        finally:
            await db.close()

    asyncio.run(scenario())


async def _count_chunks(document_id: str) -> int:
    conn = await asyncpg.connect(APP_DSN.replace("legalrag_app:legalrag_app", "postgres:postgres"))
    try:
        return await conn.fetchval("SELECT count(*) FROM chunks WHERE document_id = $1", document_id)
    finally:
        await conn.close()


async def _status(document_id: str):
    conn = await asyncpg.connect(APP_DSN.replace("legalrag_app:legalrag_app", "postgres:postgres"))
    try:
        row = await conn.fetchrow("SELECT status, error_code FROM documents WHERE id = $1", document_id)
        return row["status"], row["error_code"]
    finally:
        await conn.close()
