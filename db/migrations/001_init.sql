-- Initial schema, indexes, and row-level security.
-- {{EMBED_DIM}} is replaced by scripts/migrate.py with the EMBED_DIM setting.
-- Changing the embedding model later means changing the dimension and re-embedding everything.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email        text NOT NULL UNIQUE,
    display_name text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE matters (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text NOT NULL,
    jurisdiction text,
    walled       boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE matter_members (
    user_id   uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    matter_id uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    role      text NOT NULL CHECK (role IN ('lead', 'member', 'viewer')),
    PRIMARY KEY (user_id, matter_id)
);

-- Ethical walls: a member on this list cannot see the matter, even with a membership row.
CREATE TABLE ethical_walls (
    user_id   uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    matter_id uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, matter_id)
);

CREATE TABLE documents (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    matter_id     uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    uploaded_by   uuid NOT NULL REFERENCES users(id),
    title         text NOT NULL,
    doc_type      text NOT NULL CHECK (doc_type IN ('pdf', 'docx')),
    status        text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'processing', 'indexed', 'rejected', 'failed')),
    content_hash  text NOT NULL,
    blob_path     text NOT NULL,
    page_count    int,
    attempts      int NOT NULL DEFAULT 0,
    error_code    text,
    error_message text,
    warnings      jsonb NOT NULL DEFAULT '[]',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
-- Idempotent by content hash while a copy is live. Rejected or failed uploads can be retried.
CREATE UNIQUE INDEX documents_live_hash_uq
    ON documents (matter_id, content_hash)
    WHERE status IN ('pending', 'processing', 'indexed');
CREATE INDEX documents_matter_idx ON documents (matter_id);

CREATE TABLE document_versions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    matter_id   uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    view_label  text NOT NULL CHECK (view_label IN ('accepted', 'original')),
    UNIQUE (document_id, view_label)
);

CREATE TABLE chunks (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id  uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id   uuid NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    matter_id    uuid NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    ordinal      int NOT NULL,
    view_label   text NOT NULL CHECK (view_label IN ('accepted', 'original')),
    content      text NOT NULL,
    section_path text NOT NULL DEFAULT '',
    page_start   int,
    page_end     int,
    embedding    vector({{EMBED_DIM}}) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX chunks_matter_idx ON chunks (matter_id);
CREATE INDEX chunks_document_idx ON chunks (document_id);

CREATE TABLE memories (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    matter_ids uuid[] NOT NULL DEFAULT '{}',
    session_id text,
    content    text NOT NULL,
    embedding  vector({{EMBED_DIM}}) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX memories_embedding_hnsw ON memories USING hnsw (embedding vector_cosine_ops);

CREATE TABLE messages (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id text NOT NULL,
    request_id text NOT NULL,
    role       text NOT NULL CHECK (role IN ('user', 'assistant')),
    content    text NOT NULL,
    citations  jsonb NOT NULL DEFAULT '[]',
    status     text NOT NULL DEFAULT 'complete' CHECK (status IN ('complete', 'interrupted')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, request_id, role)
);

CREATE TABLE audit_log (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      uuid NOT NULL REFERENCES users(id),
    action       text NOT NULL,
    request_id   text,
    matter_ids   uuid[] NOT NULL DEFAULT '{}',
    document_ids uuid[] NOT NULL DEFAULT '{}',
    at           timestamptz NOT NULL DEFAULT now()
);

-- Access helpers. Policies call these, so the rules live in one place.
CREATE OR REPLACE FUNCTION app_current_user() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION app_visible_matter_ids() RETURNS SETOF uuid
LANGUAGE sql STABLE AS $$
    SELECT mm.matter_id
    FROM matter_members mm
    WHERE mm.user_id = app_current_user()
      AND NOT EXISTS (
          SELECT 1 FROM ethical_walls w
          WHERE w.user_id = mm.user_id AND w.matter_id = mm.matter_id)
$$;

CREATE OR REPLACE FUNCTION app_writable_matter_ids() RETURNS SETOF uuid
LANGUAGE sql STABLE AS $$
    SELECT mm.matter_id
    FROM matter_members mm
    WHERE mm.user_id = app_current_user()
      AND mm.role IN ('lead', 'member')
      AND NOT EXISTS (
          SELECT 1 FROM ethical_walls w
          WHERE w.user_id = mm.user_id AND w.matter_id = mm.matter_id)
$$;

-- Row-level security. Default deny: with no app.user_id set, every policy evaluates to false.
ALTER TABLE matters ENABLE ROW LEVEL SECURITY;
ALTER TABLE matters FORCE ROW LEVEL SECURITY;
CREATE POLICY matters_read ON matters FOR SELECT
    USING (id IN (SELECT app_visible_matter_ids()));

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
CREATE POLICY documents_read ON documents FOR SELECT
    USING (matter_id IN (SELECT app_visible_matter_ids()));
CREATE POLICY documents_write ON documents FOR ALL
    USING (matter_id IN (SELECT app_writable_matter_ids()))
    WITH CHECK (matter_id IN (SELECT app_writable_matter_ids()));

ALTER TABLE document_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY versions_read ON document_versions FOR SELECT
    USING (matter_id IN (SELECT app_visible_matter_ids()));
CREATE POLICY versions_write ON document_versions FOR ALL
    USING (matter_id IN (SELECT app_writable_matter_ids()))
    WITH CHECK (matter_id IN (SELECT app_writable_matter_ids()));

ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks FORCE ROW LEVEL SECURITY;
CREATE POLICY chunks_read ON chunks FOR SELECT
    USING (matter_id IN (SELECT app_visible_matter_ids()));
CREATE POLICY chunks_write ON chunks FOR ALL
    USING (matter_id IN (SELECT app_writable_matter_ids()))
    WITH CHECK (matter_id IN (SELECT app_writable_matter_ids()));

ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE memories FORCE ROW LEVEL SECURITY;
CREATE POLICY memories_owner ON memories FOR ALL
    USING (user_id = app_current_user())
    WITH CHECK (user_id = app_current_user());

ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;
CREATE POLICY messages_owner ON messages FOR ALL
    USING (user_id = app_current_user())
    WITH CHECK (user_id = app_current_user());
