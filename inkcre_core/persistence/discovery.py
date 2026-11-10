"""Bounded column-only metadata reads shared by domain repositories."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select


async def metadata_page(
  session: AsyncSession,
  statement: Select,
  key: Any,
  *,
  limit: int,
  after: str | int | None,
) -> list[dict[str, Any]]:
  """Seek by the unique key and retain one lookahead row, never full records."""
  if not 1 <= limit <= 100:
    raise ValueError("Discovery limit must be between 1 and 100")
  if after is not None:
    statement = statement.where(key > after)
  rows = await session.execute(statement.order_by(key).limit(limit + 1))
  return [dict(row) for row in rows.mappings()]
