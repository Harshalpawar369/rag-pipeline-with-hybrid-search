"""Apply SQL migrations and create the least-privilege application role.

The migration DSN is a privileged owner. The app connects as legalrag_app, which
is non-superuser and does not bypass RLS. Without that, the policies would not apply.

    python -m scripts.migrate
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import asyncpg

from app.config import load_settings

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db" / "migrations"
_ROLE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def apply_migrations(migrate_dsn: str, *, dim: int, app_role: str = "legalrag_app",
                           app_password: str = "legalrag_app") -> None:
    if not _ROLE.match(app_role):
        raise ValueError("invalid role name")
    conn = await asyncpg.connect(migrate_dsn)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        applied = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in applied:
                continue
            sql = path.read_text().replace("{{EMBED_DIM}}", str(int(dim)))
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
            print(f"applied {path.name}")

        if not await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", app_role):
            await conn.execute(
                f"CREATE ROLE {app_role} LOGIN PASSWORD {_literal(app_password)} NOSUPERUSER NOBYPASSRLS")
        db_name = await conn.fetchval("SELECT current_database()")
        await conn.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO {app_role}')
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {app_role}")
        await conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_role}")
        await conn.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}")
        await conn.execute(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {app_role}")
        await conn.execute(f"REVOKE ALL ON schema_migrations FROM {app_role}")
        # Audit is append-only for the application.
        await conn.execute(f"REVOKE UPDATE, DELETE ON audit_log FROM {app_role}")
    finally:
        await conn.close()


if __name__ == "__main__":
    import os
    s = load_settings()
    asyncio.run(apply_migrations(
        s.migrate_dsn, dim=s.embed_dim,
        app_role=os.getenv("APP_DB_USER", "legalrag_app"),
        app_password=os.getenv("APP_DB_PASSWORD", "legalrag_app"),
    ))
    print("migrations complete")
