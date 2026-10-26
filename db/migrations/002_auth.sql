-- Built-in FastAPI authentication: password accounts and opaque session tokens.
-- Only SHA-256 hashes of tokens are stored, so a database leak does not expose live sessions.

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text;

CREATE TABLE IF NOT EXISTS auth_sessions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_sessions_user_idx ON auth_sessions (user_id);
-- No RLS here: sessions are looked up before any user context exists.
