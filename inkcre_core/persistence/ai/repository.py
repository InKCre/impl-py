"""AI registry facts inside the caller's transaction."""

from collections.abc import Sequence
from typing import Any

import sqlmodel
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from inkcre_core.schemas.ai import AIDialectModel, AIModelModel, AIProviderModel


class AIRepository:
  def __init__(self, session: AsyncSession) -> None:
    self._session = session

  async def get_model(self, model_id: int) -> AIModelModel | None:
    return await self._session.get(AIModelModel, model_id)

  async def get_provider(self, provider_id: int) -> AIProviderModel | None:
    return await self._session.get(AIProviderModel, provider_id)

  async def list_models(
    self, *, limit: int | None, cursor: int | None
  ) -> tuple[list[AIModelModel], int | None]:
    statement = sqlmodel.select(AIModelModel).order_by(sqlmodel.col(AIModelModel.id))
    if cursor is not None:
      statement = statement.where(sqlmodel.col(AIModelModel.id) > cursor)
    if limit is not None:
      statement = statement.limit(limit + 1)
    rows = list((await self._session.scalars(statement)).all())
    more = limit is not None and len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1].id if more else None

  async def sync_dialects(self, records: Sequence[dict[str, Any]]) -> None:
    if not records:
      return
    statement = insert(AIDialectModel).values(records)
    statement = statement.on_conflict_do_update(
      index_elements=[AIDialectModel.id],
      set_={
        "description": statement.excluded.description,
        "config_schema": statement.excluded.config_schema,
      },
    )
    await self._session.execute(statement)
