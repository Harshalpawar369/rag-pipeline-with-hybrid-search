"""Matter scoping. Results come from row-level security, so these helpers
cannot widen access. Callers can use them to reject a request up front
and to report which matters were refused."""
from __future__ import annotations

from typing import Iterable


class ForbiddenMatter(Exception):
    def __init__(self, matter_ids: Iterable[str]):
        self.matter_ids = sorted(matter_ids)
        super().__init__("access denied to one or more matters")


async def visible_matters(db, user_id: str) -> set[str]:
    async with db.as_user(user_id) as conn:
        rows = await conn.fetch("SELECT id FROM matters")
    return {str(r["id"]) for r in rows}


async def writable_matters(db, user_id: str) -> set[str]:
    async with db.as_user(user_id) as conn:
        rows = await conn.fetch("SELECT x AS id FROM app_writable_matter_ids() AS x")
    return {str(r["id"]) for r in rows}


async def require_visible(db, user_id: str, matter_ids: Iterable[str]) -> None:
    requested = {str(m) for m in matter_ids}
    missing = requested - await visible_matters(db, user_id)
    if missing:
        raise ForbiddenMatter(missing)
