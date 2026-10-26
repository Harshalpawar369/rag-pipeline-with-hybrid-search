# Legal RAG: Hybrid Search Assistant for Legal Documents

A retrieval-augmented generation (RAG) system for searching and questioning legal documents (PDF and DOCX) with **pinpoint citations**. It combines **hybrid retrieval** (pgvector semantic search + Postgres full-text search, fused with Reciprocal Rank Fusion), **structure-aware legal chunking**, **multi-matter access control**, and **streaming answers over WebSocket**, powered by **Qwen** on Azure.

> **Disclaimer:** This is a research-assistance tool. Output must be verified against the source documents by a qualified professional. It does not provide legal advice.

---

## Table of Contents

1. [Features](#features)
2. [Scope and Assumptions](#scope-and-assumptions)
3. [Tech Stack](#tech-stack)
4. [System Design Diagram](#system-design-diagram)
5. [Data Flow Diagrams](#data-flow-diagrams)
6. [Chat Sequence](#chat-sequence)
7. [Data Model](#data-model)
8. [Key Design Decisions](#key-design-decisions)
9. [Security](#security)
10. [Scalability and Responsiveness](#scalability-and-responsiveness)
11. [Evaluation](#evaluation)
12. [Project Structure](#project-structure)
13. [Configuration](#configuration)
14. [Roadmap](#roadmap)

---

## Features

- **Hybrid search**: dense vectors (pgvector, HNSW) plus keyword search (`tsvector`, GIN), fused with RRF, followed by a reranker.
- **Legal-aware ingestion**: preserves clause numbering (1.1, 1.1(a), (i)), headings, tables, footnotes, headers and footers.
- **Tracked-changes dual index**: DOCX files with tracked changes are indexed as an *accepted* view (default) and an *original* view, each labeled.
- **Pinpoint citations**: every claim links to matter, document, page, and clause path.
- **Multi-matter search**: a user can query several matters at once, enforced by Postgres row-level security.
- **WebSocket chat**: token streaming, acknowledgements, cancellation, and reconnect handling.
- **Memory**: short-term memory in Redis (recent turns, TTL); long-term memory in Postgres (summaries and facts with embeddings).
- **Async ingestion**: direct-to-blob upload, queue-driven workers, explicit processing states, retries, and a dead-letter queue.
- **Auditability**: logs who searched which matters and which documents were retrieved.

## Scope and Assumptions

| Item | Decision |
|---|---|
| Accepted formats | `.pdf` and `.docx` only, validated by content signature, not extension |
| Max file size | 5 MB per file, plus a page-count cap and ZIP decompression limits for DOCX |
| Scanned PDFs | Detected per page. Minimal OCR (Tesseract) or rejected with a clear message in v1 |
| Tracked changes | Both *accepted* and *original* versions indexed, labeled per chunk |
| Matter access | Multi-matter queries, restricted to matters the user is a member of |
| Design scale (stated assumption) | About 50 matters, 5,000 documents, about 20 pages average, roughly 10% scanned |
| Corpus | Public data only (for example CUAD contracts, SEC EDGAR exhibits, open templates). No real client data |

The scale figures are a stated design target for testing, not measurements from real usage.

## Tech Stack

| Layer | Technology |
|---|---|
| API | Python, FastAPI (REST + WebSocket) |
| LTM and search | Azure Database for PostgreSQL Flexible Server, pgvector, tsvector |
| STM | Redis (Azure Cache for Redis in production; container locally) |
| LLM and embeddings | Qwen via Azure AI Foundry (verify model and region availability) |
| File storage | Azure Blob Storage |
| Async pipeline | Event Grid, Service Bus (with DLQ), Container Apps jobs scaled by KEDA |
| Auth | Microsoft Entra ID (JWT validated on WebSocket handshake) |
| Secrets and identity | Managed identities, Key Vault |
| Observability | Azure Monitor / Application Insights, trace IDs end to end |

---

## System Design Diagram

```mermaid
flowchart TB
    User([Lawyer / User])
    IDP[Microsoft Entra ID]

    subgraph Edge["Public edge"]
        FD["Azure Front Door + WAF<br/>TLS termination"]
    end

    subgraph VNet["Azure Virtual Network - private endpoints only"]
        subgraph CA["Azure Container Apps"]
            API["FastAPI API<br/>REST + WebSocket<br/>stateless replicas"]
            WK["Ingestion Workers<br/>scaled by queue depth"]
        end

        PG[("PostgreSQL Flexible Server<br/>pgvector + tsvector + RLS<br/>chunks, memories, audit")]
        RD[("Redis<br/>short-term memory<br/>TTL per session")]
        BL[("Blob Storage<br/>original files")]
        EG[Event Grid]
        SB[["Service Bus<br/>queue + dead-letter"]]
        KV[Key Vault]
        AI["Azure AI Foundry<br/>Qwen chat + Qwen embedding"]
    end

    MON[Azure Monitor / App Insights]

    User -->|"HTTPS / WSS"| FD
    FD --> API
    API -->|"validate token"| IDP
    API -->|"STM read/write"| RD
    API -->|"hybrid search, memory, audit"| PG
    API -->|"prompts, query embeddings"| AI
    API -->|"issue write-only upload URL"| BL
    User -.->|"direct upload via signed URL"| BL

    BL -->|"blob created"| EG
    EG --> SB
    SB --> WK
    WK -->|"read file"| BL
    WK -->|"embed chunks, separate quota"| AI
    WK -->|"transactional index write"| PG
    WK -->|"status event"| API

    API -.->|"secrets"| KV
    WK -.->|"secrets"| KV
    API -.-> MON
    WK -.-> MON
```

> **MVP simplification:** For a personal deployment, skip Front Door, Web PubSub, and Azure Cache for Redis (run Redis as a container) and use burstable or scale-to-zero SKUs. The diagram shows the production target.

---

## Data Flow Diagrams

Notation: rectangles are external entities, rounded shapes are processes, cylinders are data stores.

### DFD Level 0: Context Diagram

```mermaid
flowchart LR
    U[User / Lawyer]
    IDP[Identity Provider<br/>Entra ID]
    LLM[Qwen Model Service<br/>Azure AI Foundry]
    SYS(("Legal RAG<br/>System"))

    U -->|"login, questions, upload requests, cancel"| SYS
    SYS -->|"streamed answers, citations, ingestion status"| U
    SYS -->|"token validation request"| IDP
    IDP -->|"identity and claims"| SYS
    SYS -->|"prompts, text to embed"| LLM
    LLM -->|"generated tokens, embeddings"| SYS
```

### DFD Level 1: Major Processes

```mermaid
flowchart TB
    U[User / Lawyer]
    IDP[Identity Provider]
    LLM[Qwen Model Service]

    P1(["1.0<br/>Authenticate and Authorize"])
    P2(["2.0<br/>Ingest Documents"])
    P3(["3.0<br/>Retrieve - Hybrid Search"])
    P4(["4.0<br/>Generate Answer"])
    P5(["5.0<br/>Manage Memory"])
    P6(["6.0<br/>Audit and Monitor"])

    D1[("D1<br/>Document Store<br/>Blob")]
    D2[("D2<br/>Chunks and Embeddings<br/>Postgres")]
    D3[("D3<br/>Users, Matters, ACL<br/>Postgres")]
    D4[("D4<br/>Short-term Memory<br/>Redis")]
    D5[("D5<br/>Long-term Memory<br/>Postgres")]
    D6[("D6<br/>Audit Log<br/>Postgres")]

    U -->|"credentials / token"| P1
    P1 <-->|"verify token"| IDP
    P1 -->|"user id + allowed matter ids"| P3
    P1 <-->|"membership lookup"| D3

    U -->|"upload request + file"| P2
    P2 -->|"original file"| D1
    D1 -->|"file to process"| P2
    P2 -->|"chunks, embeddings, metadata"| D2
    P2 <-->|"document status"| D3
    P2 -->|"texts to embed"| LLM
    LLM -->|"embeddings"| P2
    P2 -->|"ingestion status"| U

    U -->|"question"| P3
    P3 <-->|"query embedding"| LLM
    P3 <-->|"ranked chunks, filtered by matter"| D2
    P3 -->|"fused and reranked passages"| P4
    P5 -->|"recent turns + relevant memories"| P4

    P4 <-->|"prompt / tokens"| LLM
    P4 -->|"streamed answer + citations"| U
    P4 -->|"completed turn"| P5

    P5 <-->|"recent turns"| D4
    P5 <-->|"summaries and facts"| D5

    P1 -.->|"access events"| P6
    P3 -.->|"matters searched, docs retrieved"| P6
    P2 -.->|"job outcomes"| P6
    P6 --> D6
```

### DFD Level 2: Process 2.0 Ingest Documents

```mermaid
flowchart TB
    U[User / Lawyer]
    LLM[Qwen Embedding]

    P21(["2.1<br/>Create Upload Slot"])
    P22(["2.2<br/>Validate and Scan"])
    P23(["2.3<br/>Parse Document"])
    P24(["2.4<br/>Structure-aware Chunking"])
    P25(["2.5<br/>Embed Chunks"])
    P26(["2.6<br/>Transactional Index Write"])
    P27(["2.7<br/>Notify Status"])

    D1[("D1 Blob Storage")]
    D2[("D2 Chunks + Embeddings")]
    D3[("D3 Documents / Status")]
    Q[["Service Bus Queue"]]
    DLQ[["Dead-letter Queue"]]

    U -->|"file name, size, matter id"| P21
    P21 -->|"document record: pending"| D3
    P21 -->|"time-limited write-only URL"| U
    U -->|"file bytes direct"| D1
    D1 -->|"blob created event"| Q
    Q --> P22

    P22 -->|"check magic bytes, size, page count,<br/>ZIP limits, malware scan"| P23
    P22 -->|"rejected: reason"| D3
    P23 -->|"text, per-page OCR flag,<br/>numbering, tables, footnotes,<br/>tracked changes"| P24
    P24 -->|"clause-level chunks with<br/>section path, page, version label"| P25
    P25 <-->|"batched embedding calls"| LLM
    P25 -->|"chunks + vectors"| P26
    P26 -->|"all-or-nothing insert<br/>idempotent by content hash"| D2
    P26 -->|"status: indexed"| D3
    D3 --> P27
    P27 -->|"live push or fetch on next connect"| U

    P22 -.->|"repeated failure"| DLQ
    P23 -.->|"repeated failure"| DLQ
    P25 -.->|"repeated failure"| DLQ
    DLQ -.->|"status: failed + error"| D3
```

### DFD Level 2: Process 3.0 and 4.0 Retrieve and Generate

```mermaid
flowchart TB
    U[User / Lawyer]
    LLM[Qwen Chat + Embedding]

    P31(["3.1<br/>Validate Frame and<br/>Resolve Matter Scope"])
    P32(["3.2<br/>Embed Query"])
    P33(["3.3<br/>Vector Search"])
    P34(["3.4<br/>Keyword Search"])
    P35(["3.5<br/>RRF Fusion"])
    P36(["3.6<br/>Rerank"])
    P37(["3.7<br/>Resolve Cross-references"])
    P41(["4.1<br/>Build Prompt"])
    P42(["4.2<br/>Stream Generation"])
    P43(["4.3<br/>Attach Citations"])
    P51(["5.1<br/>Persist Turn and<br/>Update Memory"])

    D2[("D2 Chunks")]
    D3[("D3 ACL")]
    D4[("D4 Redis STM")]
    D5[("D5 LTM")]
    D6[("D6 Audit Log")]

    U -->|"message: request id, session id, question"| P31
    P31 <-->|"allowed matter ids"| D3
    P31 -->|"ack"| U
    P31 --> P32
    P31 --> P34
    P31 -->|"load recent turns"| D4
    P32 <-->|"query vector"| LLM
    P32 --> P33

    P33 <-->|"top-K by cosine, matter filter"| D2
    P34 <-->|"top-K by ts_rank, matter filter"| D2
    P33 -->|"ranked list"| P35
    P34 -->|"ranked list"| P35
    P35 --> P36
    P36 -->|"top passages"| P37
    P37 <-->|"fetch referenced clauses and definitions"| D2

    P37 -->|"passages"| P41
    D4 -->|"history"| P41
    D5 -->|"relevant memories"| P41
    P41 -->|"delimited context + question"| P42
    P42 <-->|"prompt / token stream"| LLM
    P42 --> P43
    P43 -->|"token frames, final frame with sources"| U
    U -->|"cancel"| P42

    P43 --> P51
    P51 -->|"append turn, trim, refresh TTL"| D4
    P51 -->|"session summary at idle"| D5
    P31 -.->|"matters searched"| D6
    P36 -.->|"documents retrieved"| D6
```

---

## Chat Sequence

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant API as FastAPI (WebSocket)
    participant ID as Entra ID
    participant R as Redis (STM)
    participant PG as Postgres (pgvector)
    participant Q as Qwen (Azure AI Foundry)

    C->>API: WebSocket handshake + access token
    API->>ID: Validate token
    ID-->>API: User identity and claims
    API-->>C: Connected

    C->>API: question (request id, session id)
    API-->>C: ack
    API->>PG: Allowed matter ids for user

    par Parallel retrieval
        API->>R: Load recent turns
    and
        API->>Q: Embed query
        Q-->>API: Query vector
        API->>PG: Vector search (matter filter)
    and
        API->>PG: Keyword search (matter filter)
    end

    API->>API: RRF fusion, rerank, resolve cross-references
    API->>Q: Prompt with delimited context (stream)
    loop Token stream
        Q-->>API: token
        API-->>C: token frame
    end
    API-->>C: final frame (citations, request id)

    API->>PG: Persist turn, audit entry
    API->>R: Append turn, trim, refresh TTL

    opt User cancels
        C->>API: cancel (request id)
        API->>Q: Abort generation
    end
```

---

## Data Model

```mermaid
erDiagram
    USERS ||--o{ MATTER_MEMBERS : "belongs to"
    MATTERS ||--o{ MATTER_MEMBERS : "has"
    MATTERS ||--o{ DOCUMENTS : "contains"
    DOCUMENTS ||--o{ DOCUMENT_VERSIONS : "has"
    DOCUMENT_VERSIONS ||--o{ CHUNKS : "split into"
    USERS ||--o{ MEMORIES : "owns"
    USERS ||--o{ MESSAGES : "sends"
    USERS ||--o{ AUDIT_LOG : "generates"

    USERS {
        uuid id PK
        text email
        text tenant_id
    }
    MATTERS {
        uuid id PK
        text name
        text jurisdiction
        boolean walled
    }
    MATTER_MEMBERS {
        uuid user_id FK
        uuid matter_id FK
        text role
    }
    DOCUMENTS {
        uuid id PK
        uuid matter_id FK
        text doc_type
        text status
        text content_hash
        text blob_path
        date effective_date
        text governing_law
    }
    DOCUMENT_VERSIONS {
        uuid id PK
        uuid document_id FK
        text view_label
        text supersedes_id
    }
    CHUNKS {
        bigint id PK
        uuid version_id FK
        uuid matter_id FK
        text content
        vector embedding
        tsvector tsv
        text section_path
        int page_start
        int page_end
        real ocr_confidence
        jsonb metadata
    }
    MEMORIES {
        bigint id PK
        uuid user_id FK
        text content
        vector embedding
        timestamptz created_at
    }
    MESSAGES {
        bigint id PK
        uuid user_id FK
        text session_id
        text role
        text content
    }
    AUDIT_LOG {
        bigint id PK
        uuid user_id FK
        text matter_ids
        text document_ids
        timestamptz at
    }
```

`view_label` is `accepted` or `original`. Documents with no tracked changes store a single view. `matter_id` is denormalized onto `chunks` so row-level security and filtered index scans do not need a join.

---

## Key Design Decisions

| Decision | Rationale | Trade-off |
|---|---|---|
| **Redis for STM, Postgres for LTM** | STM is hot, small, and expiring; Redis gives sub-millisecond reads and native TTL. Postgres is the durable source of truth. | One more service to run |
| **Hybrid search in Postgres** | One datastore for vectors and keywords, transactional writes, no separate search engine | Less sophisticated keyword ranking than true BM25 (can add `pg_search` if allowed) |
| **RRF fusion** | Uses ranks, so no score normalization across vector and keyword results | Ignores score magnitude |
| **Index HNSW top-K directly, rank afterwards** | Ranking over the whole table with a window function defeats the index | Needs `EXPLAIN ANALYZE` verification |
| **Filtered-search handling** | Selective matter filters with HNSW can return too few rows. Use pgvector iterative scans or partition by matter | Added tuning and testing |
| **Structure-aware chunking** | Clauses and definitions lose meaning when split by token count | More parsing logic |
| **Dual index for tracked changes** | Prevents quoting deleted language as current terms | Larger index when tracked changes exist |
| **Direct-to-blob upload** | Files bypass API servers, so large uploads cannot block chat | More moving parts |
| **Queue-driven workers** | Absorbs bursts, retries, isolates ingestion from chat | Eventual consistency of document status |
| **Separate embedding quota for ingestion** | Bulk embedding cannot starve chat | Extra deployment to manage |
| **Transactional document writes** | A document is fully searchable or not at all | Larger transactions for big documents |

## Security

- **Network:** Postgres, Redis, Blob, and the model endpoint are reachable only through private endpoints. The public edge is the only internet-facing surface.
- **Identity:** Entra ID tokens validated on the WebSocket handshake; user identity always comes from the token, never from message payloads. Services use managed identities; remaining secrets live in Key Vault.
- **Authorization:** Row-level security on `chunks`, `documents`, and `memories`, scoped by matter membership. Ethical walls exclude walled matters even for firm members. Enforced in the API and the workers.
- **Upload safety:** Magic-byte validation, 5 MB cap, page cap, ZIP decompression limits (uncompressed size, entry count, nesting), parsing in a sandboxed worker with time and memory limits, malware scanning.
- **WebSocket hardening:** Origin check, frame size and schema validation, per-user and per-connection rate limits, heartbeats.
- **Prompt injection:** Retrieved text is untrusted. It is placed in a delimited context block and treated as data; the model has no side-effecting tools.
- **Data protection:** Encryption at rest and in transit. No document text or prompts in logs; IDs, latencies, and error codes only.
- **Cache and memory isolation:** STM and any answer cache are keyed by user and the set of matters searched.
- **Deletion:** Deleting a document removes its chunks, embeddings, cache entries, and related memories together.
- **Model data handling:** Confirm the Qwen deployment does not retain prompts or outputs and that processing region meets residency needs.

## Scalability and Responsiveness

**Scalability**
- API replicas are stateless; session state lives in Redis. Scale on concurrent WebSocket connections.
- If one replica must push to a socket held by another, add a pub/sub layer (Azure Web PubSub is a managed option).
- Workers scale independently on queue depth (KEDA).
- Postgres uses pooling (PgBouncer or the built-in pooler); scale compute first, add a read replica for retrieval if needed. Size memory so the HNSW index fits in RAM.
- Per-user token rate limits protect shared model quota.

**Responsiveness**
- Token streaming minimizes time to first token.
- Vector and keyword retrieval, STM load, and LTM lookup run in parallel.
- Immediate ack after each message; persistence happens after the stream ends.
- Cancellation aborts in-flight generation.
- Request IDs allow safe resume or discard after reconnect.
- Query embedding cache for repeated questions (keyed by matter scope).

**Failure behavior**
- Socket drop mid-answer: client reconnects with session and last request id; server resends the stored answer or reports the interruption.
- Model timeout: error frame, and the turn is not recorded as successful.
- Worker crash: transactional write leaves nothing partial; the message returns to the queue.

## Evaluation

Retrieval quality is measured, not assumed. The harness runs question-to-expected-clause pairs (built from CUAD's labeled clauses) against each retrieval mode.

| Mode | Recall@5 | Recall@10 | MRR |
|---|---|---|---|
| Vector only | not yet measured | not yet measured | not yet measured |
| Keyword only | not yet measured | not yet measured | not yet measured |
| Hybrid (RRF) | not yet measured | not yet measured | not yet measured |
| Hybrid + rerank | not yet measured | not yet measured | not yet measured |

Fill this table with real results once the harness runs. Also track time to first token, retrieval latency, queue age, and document failure rate.

## Project Structure

Planned layout:

```
legal-rag/
├── app/
│   ├── api/            # REST + WebSocket handlers
│   ├── retrieval/      # hybrid search, fusion, rerank
│   ├── memory/         # STM (Redis) and LTM (Postgres)
│   ├── security/       # token validation, matter scoping
│   └── prompts/        # prompt templates, citation rules
├── workers/
│   └── ingestion/      # validate, parse, chunk, embed, index
├── db/
│   └── migrations/     # schema, indexes, RLS policies
├── eval/               # evaluation harness and datasets
├── infra/              # IaC for Azure resources
├── docs/               # diagrams and design notes
└── README.md
```

## Configuration

| Variable | Purpose |
|---|---|
| `PG_DSN` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `QWEN_ENDPOINT` / `QWEN_KEY` | Model endpoint (prefer managed identity or Key Vault) |
| `QWEN_MODEL` / `EMBED_MODEL` | Chat and embedding deployment names |
| `BLOB_ACCOUNT_URL` | Storage account for original files |
| `SERVICEBUS_NAMESPACE` | Ingestion queue namespace |
| `ENTRA_TENANT_ID` / `ENTRA_AUDIENCE` | Token validation settings |
| `MAX_UPLOAD_MB` | Upload size limit (default 5) |
| `STM_TURNS` / `STM_TTL_SECONDS` | Short-term memory window and expiry |

The embedding dimension in the schema must match the embedding model. Changing the embedding model later requires re-embedding all content.

## Roadmap

1. Ingestion for digital PDFs and DOCX with structure-aware chunking
2. Schema, hybrid retrieval, and the evaluation harness
3. WebSocket chat with streaming and citations
4. Matter-based access control (RLS), ethical walls, audit logging
5. Reranking, tracked-changes dual index, scanned-page detection
6. Azure deployment and observability
7. Later: OCR with Azure Document Intelligence, amendment and supersession tracking, contract clause extraction

## License

Choose a license (for example MIT) before publishing.
