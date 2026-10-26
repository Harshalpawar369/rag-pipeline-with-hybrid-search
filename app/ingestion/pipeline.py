"""Ingestion worker.

Each job goes validate -> parse -> chunk -> embed -> transactional index write.
A document becomes searchable all at once or not at all. Transient failures are
retried with backoff. After MAX_ATTEMPTS the document is marked 'failed', which
plays the role of the dead-letter queue: it stays visible with its error and
can be re-uploaded. Permanent rejections (bad file, scanned PDF) are not retried.

The queue is in-process here. In Azure, the same job shape would arrive via
Event Grid and Service Bus, and the workers would run as Container Apps jobs.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from app.config import Settings
from app.db import Database, vec_literal
from app.ingestion.chunk import Chunk, chunk_blocks
from app.ingestion.parse import ParsedDocument, parse_docx, parse_pdf
from app.ingestion.validate import RejectedDocument, scan_for_malware

log = logging.getLogger("ingest")
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class IngestJob:
    document_id: str
    matter_id: str
    user_id: str
    blob_path: str
    doc_type: str


class IngestionService:
    def __init__(self, db: Database, embedder, settings: Settings):
        self.db = db
        self.embedder = embedder
        self.settings = settings
        self.queue: asyncio.Queue[IngestJob] = asyncio.Queue(maxsize=settings.ingest_queue_size)
        self._workers: list[asyncio.Task] = []

    async def start(self) -> None:
        self._workers = [
            asyncio.create_task(self._worker(), name=f"ingest-{i}")
            for i in range(self.settings.ingest_workers)
        ]

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    def enqueue(self, job: IngestJob) -> None:
        """Raises asyncio.QueueFull when the backlog is full."""
        self.queue.put_nowait(job)

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self.process(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ingestion crashed for document %s", job.document_id)
            finally:
                self.queue.task_done()

    async def process(self, job: IngestJob) -> str:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._set_status(job, "processing", attempts=attempt)
            try:
                parsed, chunks_by_view = await asyncio.to_thread(self._parse_and_chunk, job)
                texts = [c.content for chunks in chunks_by_view.values() for c in chunks]
                vectors = await self.embedder.embed(texts)
                await self._write_index(job, parsed, chunks_by_view, vectors)
                log.info("indexed document %s (%d chunks)", job.document_id, len(texts))
                return "indexed"
            except RejectedDocument as exc:
                log.info("rejected document %s: %s", job.document_id, exc.code)
                await self._set_status(job, "rejected", error_code=exc.code, error_message=exc.message)
                return "rejected"
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("attempt %d failed for document %s", attempt, job.document_id, exc_info=True)
                if attempt == MAX_ATTEMPTS:
                    await self._set_status(
                        job, "failed", error_code="processing_error",
                        error_message="Processing failed after several attempts.",
                    )
                    return "failed"
                await asyncio.sleep(self.settings.ingest_retry_backoff * attempt)
        return "failed"

    def _parse_and_chunk(self, job: IngestJob) -> tuple[ParsedDocument, dict[str, list[Chunk]]]:
        path = self.settings.blob_dir / job.blob_path
        scan_for_malware(path)
        if job.doc_type == "pdf":
            parsed = parse_pdf(path, self.settings.max_pages)
        else:
            parsed = parse_docx(path)
        chunks_by_view = {view: chunk_blocks(blocks, view) for view, blocks in parsed.views.items()}
        chunks_by_view = {view: chunks for view, chunks in chunks_by_view.items() if chunks}
        if not chunks_by_view:
            raise RejectedDocument("empty", "No text could be extracted from this document.")
        return parsed, chunks_by_view

    async def _set_status(self, job: IngestJob, status: str, *, attempts: int | None = None,
                          error_code: str | None = None, error_message: str | None = None) -> None:
        async with self.db.as_user(job.user_id) as conn:
            await conn.execute(
                """UPDATE documents
                   SET status = $2,
                       attempts = COALESCE($3, attempts),
                       error_code = $4,
                       error_message = $5,
                       updated_at = now()
                   WHERE id = $1""",
                job.document_id, status, attempts, error_code, error_message,
            )

    async def _write_index(self, job: IngestJob, parsed: ParsedDocument,
                           chunks_by_view: dict[str, list[Chunk]], vectors: list[list[float]]) -> None:
        async with self.db.as_user(job.user_id) as conn:
            # Idempotent retry: cascades to chunks.
            await conn.execute("DELETE FROM document_versions WHERE document_id = $1", job.document_id)
            version_ids: dict[str, str] = {}
            for view in chunks_by_view:
                version_ids[view] = str(await conn.fetchval(
                    "INSERT INTO document_versions (document_id, matter_id, view_label) "
                    "VALUES ($1, $2, $3) RETURNING id",
                    job.document_id, job.matter_id, view,
                ))
            rows = []
            i = 0
            for view, chunks in chunks_by_view.items():
                for ordinal, chunk in enumerate(chunks):
                    rows.append((
                        job.document_id, version_ids[view], job.matter_id, ordinal, view,
                        chunk.content, chunk.section_path, chunk.page_start, chunk.page_end,
                        vec_literal(vectors[i]),
                    ))
                    i += 1
            await conn.executemany(
                """INSERT INTO chunks (document_id, version_id, matter_id, ordinal, view_label,
                                       content, section_path, page_start, page_end, embedding)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::vector)""",
                rows,
            )
            await conn.execute(
                """UPDATE documents
                   SET status = 'indexed', page_count = $2, warnings = $3::jsonb,
                       error_code = NULL, error_message = NULL, updated_at = now()
                   WHERE id = $1""",
                job.document_id, parsed.page_count, json.dumps(parsed.warnings),
            )
