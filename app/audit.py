"""Append-only audit trail: who searched which matters and which documents came back.
The app role has INSERT and SELECT on audit_log but no UPDATE or DELETE."""
from __future__ import annotations

from typing import Iterable


async def record(db, *, user_id: str, action: str, request_id: str | None = None,
                 matter_ids: Iterable[str] = (), document_ids: Iterable[str] = ()) -> None:
    await db.execute(
        "INSERT INTO audit_log (user_id, action, request_id, matter_ids, document_ids) "
        "VALUES ($1, $2, $3, $4::uuid[], $5::uuid[])",
        user_id, action, request_id, list(matter_ids), list(document_ids),
    )
