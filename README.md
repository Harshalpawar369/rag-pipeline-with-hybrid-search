# Legal RAG: Hybrid Search Assistant for Legal Documents

A retrieval-augmented system for asking questions about legal documents (PDF and DOCX) and getting answers with **pinpoint citations** (matter, document, page, clause path). It implements the design in [`docs/DESIGN.md`](docs/DESIGN.md) as a runnable local system: hybrid retrieval (pgvector + Postgres full-text, fused with Reciprocal Rank Fusion), structure-aware legal chunking, tracked-changes dual index, row-level-security matter access with ethical walls, streaming answers over WebSocket, and Redis short-term memory.

> **Disclaimer:** This is a research-assistance tool. Verify every answer against the source documents with a qualified professional. It does not provide legal advice.

---

## Quick start

Requires Python 3.12+ and Docker (or local Postgres 16 with pgvector and Redis).

```bash
# 1. Start Postgres (with pgvector) and Redis
docker compose up -d postgres redis

# 2. Install dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure. The defaults match the compose stack, and with no QWEN_ENDPOINT the app runs offline.
#    The app reads environment variables directly (it does not load .env). See .env.example for the full list.

# 4. Create the schema and the least-privilege app role
python -m scripts.migrate

# 5. Seed demo users and matters, and print a dev token for Alice
python -m scripts.seed --token alice@example.com

# 6. Run the API
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000, paste Alice's token, and paste the **Acme MSA Review** matter ID from the seed output. Upload a file first (see below), then ask.

To try the sample documents:

```bash
python -m scripts.samples data/samples
# Upload data/samples/sample_msa.docx (contains tracked changes) and sample_nda.pdf
curl -H "Authorization: Bearer $TOKEN" -F "file=@data/samples/sample_msa.docx" \
     http://localhost:8000/matters/$ACME_ID/documents
```

Or run everything in containers: `docker compose up --build` (the API runs migrations on start).

### Tests

The tests run against a real Postgres and Redis, because the RLS, pgvector, and tsvector behavior must be checked against the real database.

```bash
createdb legalrag_test                   # needs the pgvector extension available
pytest
```

Override the connection strings with `TEST_MIGRATE_DSN`, `TEST_PG_DSN`, and `TEST_REDIS_URL` if needed.

---

## What is built, mapped to the design

| Design element | Implementation |
|---|---|
| Hybrid retrieval, RRF | `app/retrieval/search.py`: HNSW cosine search and `tsvector` ranking run concurrently, fused by rank (`rrf_fuse`, k=60) |
| Structure-aware chunking | `app/ingestion/chunk.py`: articles, numbered clauses, lettered and roman sub-items, headings. Section path and page range stored per chunk |
| Tracked-changes dual index | `app/ingestion/parse.py`: DOCX yields an `accepted` view and an `original` view when revisions exist. Search defaults to `accepted` |
| Pinpoint citations | `app/prompts/answer.py`: `[S#]` markers map to passages. Citation records carry document, matter, pages, section path, and view |
| Matter ACL with RLS | `db/migrations/001_init.sql`: policies on `matters`, `documents`, `document_versions`, and `chunks` call `app_visible_matter_ids()` and `app_writable_matter_ids()`. The app role is non-superuser, so the policies apply |
| Ethical walls | `ethical_walls` table, enforced inside the same SQL helper functions, so a walled member sees nothing |
| Upload safety | `app/ingestion/validate.py`: magic-byte sniffing, 5 MB cap, page cap, DOCX zip-bomb and path-traversal checks, encrypted-file rejection |
| Scanned pages | Detected per page. Blank pages are skipped with a warning. A file with no text layer is rejected with `scanned_unsupported` |
| Async ingestion, retries, DLQ | `app/ingestion/pipeline.py`: explicit states (`pending`, `processing`, `indexed`, `rejected`, `failed`). Transient errors retry with backoff. After 3 attempts, `failed` (the DLQ analogue) keeps the error visible |
| Transactional index writes | Chunks, versions, and status commit in one transaction. Retries delete and rewrite, so re-processing is idempotent |
| Idempotency by content hash | Partial unique index on `(matter_id, content_hash)` for live documents. Re-uploading returns the existing document |
| WebSocket chat | `app/api/ws.py`, `app/chat.py`: ack, token stream, done, cancel, reconnect replay by `request_id`, frame size and schema validation, per-connection rate limit, origin check |
| Short-term memory | `app/memory/stm.py`: Redis list keyed by user, session, and matter scope, trimmed to `STM_TURNS`, with TTL. Degrades to no history if Redis is down |
| Long-term memory | `app/memory/ltm.py`: session summaries with embeddings (`POST /sessions/{id}/summarize`). Retrieved only when all of their matters are in the current scope |
| Audit | `audit_log`. The app role has INSERT and SELECT only, so entries cannot be rewritten |
| Injection resistance | Retrieved text sits inside delimited `<source>` blocks. The system prompt says to treat it as data. Query text is sanitized before it reaches the tsquery |

### Request lifecycle for a question

1. The socket is authenticated from the token. Identity comes only from the token.
2. The client sends `ask`. The server acks immediately.
3. Matter scope is checked (`require_visible`, backed by RLS). A forbidden matter is refused, not silently dropped.
4. If `request_id` already has a stored answer, that answer is replayed and no model call is made.
5. The query is embedded. Then hybrid retrieval, STM, and LTM run concurrently.
6. The prompt is built with the passages in delimited source blocks, and the model streams tokens.
7. The `done` frame carries citations, which are only the `[S#]` markers that match passages actually sent.
8. The turn is persisted, audited, and appended to STM. A turn that errors or is cancelled is not recorded as a success.

### WebSocket protocol

Connect to `/ws?token=<JWT>`. Client frames:

```json
{"type": "ask", "request_id": "r-123", "session_id": "s-1",
 "matter_ids": ["<uuid>"], "question": "What is the term?", "include_original": false}
{"type": "cancel", "request_id": "r-123"}
{"type": "ping"}
```

Server frames: `ack`, `token` (`text`), `done` (`citations`), `error` (`code`, `message`), `pong`. A replayed answer has `"replay": true`. Reconnect with the same `request_id` to recover a finished answer, or to get `in_progress` if it is still running.

---

## REST endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness and database check |
| GET | `/me/matters` | Matters visible to the caller, with role |
| POST | `/matters/{id}/documents` | Upload PDF or DOCX (multipart `file`). Returns 202 with a document id |
| GET | `/matters/{id}/documents` | Documents with status, warnings, and errors |
| GET | `/documents/{id}` | Single document status |
| DELETE | `/documents/{id}` | Removes the document, its chunks, and its file |
| POST | `/search` | Retrieval only (`mode`: `hybrid`, `vector`, or `keyword`), for debugging and evaluation |
| POST | `/sessions/{id}/summarize` | Saves a session summary as long-term memory |

---

## Configuration

See `.env.example`. The important ones:

| Variable | Purpose |
|---|---|
| `PG_DSN` | App connection (role `legalrag_app`, non-superuser) |
| `MIGRATE_DSN` | Owner connection, used only by `scripts.migrate` and `scripts.seed` |
| `QWEN_ENDPOINT`, `QWEN_KEY`, `QWEN_AUTH_STYLE` | OpenAI-compatible endpoint for chat and embeddings. Empty means offline mode |
| `QWEN_MODEL`, `EMBED_MODEL`, `EMBED_DIM` | Deployment names and vector size. `EMBED_DIM` must match the model |
| `JWT_SECRET` | Required for production. The app refuses to start in production with the dev default |
| `ALLOWED_ORIGINS` | WebSocket origin allow-list |
| `MAX_UPLOAD_MB`, `MAX_PAGES` | Upload limits |
| `STM_TURNS`, `STM_TTL_SECONDS` | Short-term memory window and expiry |
| `WS_ASKS_PER_MINUTE` | Per-connection question rate limit |
| `HNSW_EF_SEARCH`, `RETRIEVAL_CANDIDATES` | Recall/latency tuning for vector search |

**Offline mode.** With no `QWEN_ENDPOINT`, the app uses a deterministic feature-hashing embedder and a chat stub that quotes the top passage. This is for development and tests. Its retrieval quality is not representative, and it is not a substitute for the real model. Startup logs say which mode is active.

---

## Evaluation

`eval/run_eval.py` runs question-to-clause pairs against each retrieval mode and reports Recall@5, Recall@10, and MRR:

```bash
python -m eval.run_eval eval/datasets/your_set.jsonl --user-id <uuid>
```

Each line is `{"question", "matter_ids", "gold": {"document_title", "contains"}}`. A hit requires the right document and a passage that contains the gold text. The harness is tested, but **no retrieval numbers are reported here**. The design calls for building the dataset from CUAD's labeled clauses, and that data was not available in this environment. Fill the table from a real run:

| Mode | Recall@5 | Recall@10 | MRR |
|---|---|---|---|
| Vector only | not yet measured | not yet measured | not yet measured |
| Keyword only | not yet measured | not yet measured | not yet measured |
| Hybrid (RRF) | not yet measured | not yet measured | not yet measured |

---

## Security notes

- **Default deny.** With no `app.user_id` set, every row-level policy evaluates to false. There is a test for this.
- **Ethical walls** are checked in the same SQL functions as membership, so a walled member cannot see the matter by ID, by search, or through memory.
- **Viewers** can read but cannot write. Enforced by `WITH CHECK` on writes, and tested.
- **Audit log** is append-only for the app role.
- **Tracked changes.** The default search returns only the accepted view. Deleted language is not presented as current terms unless `include_original` is set.
- **Upload validation** uses content signatures, not extensions. DOCX archives are checked for zip bombs and unsafe paths before parsing.

---

## Deviations from the design (and why)

The design targets Azure. This version runs locally so it can be built and tested end to end. Each deviation is a single seam to swap:

| Design | Here | Swap point |
|---|---|---|
| Entra ID JWT validation | HS256 dev tokens | `app/security/auth.py: verify_token` |
| Direct-to-Blob signed upload | Upload through the API, stored on local disk | `app/api/http.py` (file write) and `BLOB_DIR` |
| Event Grid, Service Bus, DLQ | In-process `asyncio.Queue`; `failed` status as the DLQ | `app/ingestion/pipeline.py` |
| Qwen on Azure AI Foundry | OpenAI-compatible client, plus offline stubs | `app/llm.py`, `app/retrieval/embeddings.py` |
| Azure Cache for Redis | Redis container | `REDIS_URL` |
| Front Door, WAF, Web PubSub | Not included. Single process | Deployment layer |
| Malware scanning | Hook present, no scanner | `scan_for_malware` in `app/ingestion/validate.py` |
| OCR (Tesseract or Document Intelligence) | Not included. Scanned pages skipped or file rejected | Roadmap item 7 |
| Reranker | Not included. RRF only | Roadmap item 5 |
| Resolve cross-references (flow 3.7) | Not included | Roadmap |
| Memories linked to documents, purged on delete | Memories are not linked to documents. Deleting a document does not purge them | Needs a `document_id` link on memories |
| Query embedding cache | Not included | Keyed by matter scope, as the design says |

### Known limitations

- **Filtered vector search.** pgvector 0.6 (the version tested here) can return fewer than `candidates` rows when a selective matter filter is applied to the HNSW index. The code raises `hnsw.ef_search`, which helps but does not guarantee full recall. pgvector 0.8+ with iterative scans fixes this. Alternatively, partition by matter, as the design notes.
- **Keyword ranking** uses `ts_rank_cd` over OR-ed terms. It is not BM25.
- **Offline answers** are not generated by a model. Use them to test the pipeline only.
- **Cancel vs. reconnect.** A socket drop does not cancel the turn. The turn finishes and is stored. Explicit `cancel` frames do stop generation and store the partial text as `interrupted`.

---

## Roadmap (from the design)

1. Ingestion for digital PDF and DOCX with structure-aware chunking. *Done.*
2. Schema, hybrid retrieval, evaluation harness. *Done. Metrics not yet measured.*
3. WebSocket chat with streaming and citations. *Done.*
4. Matter-based access control (RLS), ethical walls, audit logging. *Done.*
5. Reranking, scanned-page detection. *Scanned-page detection done. Reranking pending.*
6. Azure deployment and observability. *Pending.*
7. OCR with Azure Document Intelligence, amendment and supersession tracking, contract clause extraction. *Pending.*

## License

Choose a license (for example MIT) before publishing.
